"""转账核心业务：幂等、行锁、固定锁顺序、事务原子性。

资金安全的三条底线（面试高频考点）：
1. 幂等（Idempotency）：同一笔业务（同一个 trade_no）无论提交多少次、是否并发，
   资金只发生一次变化；
2. 行锁（Row Lock）：并发扣款必须 select ... for update 锁行，禁止表锁；
3. 锁顺序（Lock Ordering）：多行加锁必须全局固定顺序，从根源消除死锁。
"""

import logging

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.account import Account
from app.models.transfer import TransferRecord, TransferStatus
from app.schemas.transfer import TransferResultResponse, TransferSubmitRequest
from app.settings import get_settings
from app.utils.snowflake import next_trade_no

logger = logging.getLogger(__name__)

# 故障注入的两个阶段（仅当全局开关 ENABLE_FAULT_INJECT=true 时生效）
FAULT_STAGE_MID_TX = "mid_tx"  # 事务中途：扣钱完成、加钱之前
FAULT_STAGE_AFTER_COMMIT = "after_commit"  # 事务已提交、响应返回前（模拟网络断开）
VALID_FAULT_STAGES = frozenset({FAULT_STAGE_MID_TX, FAULT_STAGE_AFTER_COMMIT})


async def apply_trade_no(session: AsyncSession) -> str:
    """申请转账单号：后端用雪花算法生成全局唯一 trade_no，并先落一条 INIT 流水。

    金融标准方案：绝不让前端自定义单号 —— 否则前端可以随意伪造/复用单号，
    幂等防线形同虚设。单号必须由后端颁发。
    """
    trade_no = next_trade_no()
    async with session.begin():
        session.add(TransferRecord(trade_no=trade_no, status=TransferStatus.INIT))
    return trade_no


async def get_transfer_result(session: AsyncSession, trade_no: str) -> TransferResultResponse:
    """按单号查询转账结果。

    存在的意义：submit 已 commit、但响应在网络中丢失时，前端收到的是超时而非失败。
    前端凭同一个 trade_no 轮询本接口即可拿到最终状态，避免盲目重试或误判。
    """
    async with session.begin():
        record = await session.scalar(
            select(TransferRecord).where(TransferRecord.trade_no == trade_no)
        )
    if record is None:
        raise AppError("TRADE_NO_NOT_FOUND", f"转账单号不存在: {trade_no}", http_status=404)
    return TransferResultResponse.model_validate(record)


def _validate_fault_stage(fault_stage: str | None) -> None:
    """故障注入参数校验：必须是已知阶段，且全局开关已打开。"""
    if fault_stage is None:
        return
    if fault_stage not in VALID_FAULT_STAGES:
        raise AppError(
            "INVALID_FAULT_STAGE",
            f"fault_stage 仅支持 {sorted(VALID_FAULT_STAGES)}",
            http_status=400,
        )
    if not get_settings().enable_fault_inject:
        raise AppError(
            "FAULT_INJECT_DISABLED",
            "故障注入未开启（.env 中 ENABLE_FAULT_INJECT=false）",
            http_status=400,
        )


def _map_exception(exc: Exception) -> AppError:
    """把内部异常映射为对外业务错误：分类处理锁超时/死锁/数据库异常/未知异常。"""
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, OperationalError) and exc.orig is not None and exc.orig.args:
        errno = exc.orig.args[0]
        if errno == 1205:
            # Innodb lock wait timeout：行锁被别的事务长期占用
            return AppError(
                "LOCK_WAIT_TIMEOUT",
                "锁等待超时：账户正被其他事务占用，请稍后重试",
                http_status=503,
            )
        if errno == 1213:
            # 理论上固定锁顺序已杜绝死锁，这里只做防御性捕获
            return AppError(
                "DEADLOCK_DETECTED",
                "检测到死锁，事务已回滚，请稍后重试",
                http_status=409,
            )
    if isinstance(exc, SQLAlchemyError):
        logger.exception("转账过程中发生数据库异常")
        return AppError("DB_ERROR", "数据库异常，转账已回滚", http_status=500)
    logger.exception("转账过程中发生未预期异常")
    return AppError("INTERNAL_ERROR", "服务内部异常，转账已回滚", http_status=500)


async def _mark_failed(
    session: AsyncSession, req: TransferSubmitRequest, error_msg: str
) -> None:
    """事务回滚后，用独立事务把流水标记为 FAILED 并记录失败原因。

    为什么必须用"新事务"：原事务已整体回滚，任何写操作都随之消失，
    失败状态必须在回滚之后单独提交，否则查询接口永远只能看到 INIT。

    只更新 status=INIT 的行：若该单号已被并发请求推进到终态，绝不覆盖。
    若这里再次发生数据库异常（例如行仍被占用），只记录日志，保证原始异常继续上抛。
    """
    try:
        async with session.begin():
            await session.execute(
                update(TransferRecord)
                .where(
                    TransferRecord.trade_no == req.trade_no,
                    TransferRecord.status == TransferStatus.INIT,
                )
                .values(
                    status=TransferStatus.FAILED,
                    error_msg=error_msg[:255],
                    from_account_no=req.from_account_no,
                    to_account_no=req.to_account_no,
                    amount=req.amount,
                )
            )
    except SQLAlchemyError:
        logger.exception("标记 FAILED 流水失败: trade_no=%s", req.trade_no)


async def submit_transfer(
    session: AsyncSession,
    req: TransferSubmitRequest,
    fault_stage: str | None = None,
) -> TransferResultResponse:
    """提交转账（核心逻辑，业务步骤严格按 PRD 顺序执行）。"""
    settings = get_settings()
    _validate_fault_stage(fault_stage)

    # ① 幂等校验（快速路径，无锁）：
    #    先查流水再执行业务，绝大多数重复请求在这里就直接返回，不占用行锁。
    async with session.begin():
        record = await session.scalar(
            select(TransferRecord).where(TransferRecord.trade_no == req.trade_no)
        )
    if record is None:
        # 单号不是后端颁发的一律拒绝：防止前端伪造单号绕过幂等体系
        raise AppError(
            "TRADE_NO_NOT_FOUND",
            "转账单号不存在：单号必须由后端 /api/transfer/apply-no 预先颁发",
            http_status=400,
        )
    if record.status in (TransferStatus.SUCCESS, TransferStatus.FAILED):
        # 已完结的流水直接返回历史结果，绝不重复执行业务
        return TransferResultResponse.model_validate(record)

    # ② 参数校验（转出转入不同、amount>0、最多 2 位小数）已由 Pydantic 完成，
    #    非法参数在进入事务之前就被拦截，不消耗数据库资源。

    try:
        # ③ 开启事务。async with session.begin() 保证：
        #    正常退出自动 commit；任意异常自动 rollback。
        async with session.begin():
            # 幂等的并发兜底：给流水行加排他锁，把同一 trade_no 的并发请求串行化。
            # 注意：trade_no 唯一索引只能防止"重复插入"，而本 Demo 的流水在 apply-no
            # 阶段已存在，因此真正的并发幂等必须依赖这里的"行锁 + 二次状态校验"
            # （双重检查）：后到者阻塞到先到者提交后，看到 SUCCESS/FAILED 直接返回。
            # populate_existing：强制用数据库最新值覆盖 Session 缓存（identity map），
            # 否则上面快速路径读到的旧状态 INIT 会残留，后到者误以为业务还没执行。
            record = await session.scalar(
                select(TransferRecord)
                .where(TransferRecord.trade_no == req.trade_no)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if record.status != TransferStatus.INIT:
                return TransferResultResponse.model_validate(record)

            # 回填本次请求的业务字段：即使后续失败回滚，_mark_failed 也会再次写入，
            # 保证失败流水同样能追溯"谁想转给谁多少钱"。
            record.from_account_no = req.from_account_no
            record.to_account_no = req.to_account_no
            record.amount = req.amount

            # ④ 行锁两个账户。强制规则：永远先锁 account_no 字典序小的账户！
            #    A->B 与 B->A 并发时，双方都按 "A 先 B 后" 加锁，不存在循环等待，
            #    从根源消除死锁（对应测试：双向并发转账）。
            #    for update 的等值条件命中 account_no 唯一索引 => 只加行锁；
            #    若条件落在无索引列上，InnoDB 会退化为锁全表（防止升级表锁）。
            first_no, second_no = sorted([req.from_account_no, req.to_account_no])
            first_account = await session.scalar(
                select(Account).where(Account.account_no == first_no).with_for_update()
            )
            second_account = await session.scalar(
                select(Account).where(Account.account_no == second_no).with_for_update()
            )
            if first_no == req.from_account_no:
                account_from, account_to = first_account, second_account
            else:
                account_from, account_to = second_account, first_account

            if account_from is None or account_to is None:
                raise AppError("ACCOUNT_NOT_FOUND", "转出或转入账户不存在", http_status=404)

            # ⑤ 余额校验 + 扣加。金额全程 Decimal，任何一步都不允许出现 float。
            if account_from.balance < req.amount:
                raise AppError(
                    "INSUFFICIENT_BALANCE",
                    f"余额不足：当前 {account_from.balance}，需要 {req.amount}",
                    http_status=409,
                )

            account_from.balance = account_from.balance - req.amount
            # 先 flush 让"扣款"语句真正发到事务里，故障注入点才具备真实意义
            await session.flush()

            # 故障注入（阶段一）：扣钱完成、加钱之前主动抛异常，
            # 用来验证事务原子性 —— 整个事务回滚，两边余额一分不动。
            if settings.enable_fault_inject and fault_stage == FAULT_STAGE_MID_TX:
                raise RuntimeError("故障注入：扣款后、入账前发生异常")

            account_to.balance = account_to.balance + req.amount

            # ⑥ 标记流水 SUCCESS：与账户余额变更在同一个事务里，
            #    保证"钱和流水"要么一起成功、要么一起回滚（事务原子性）。
            record.status = TransferStatus.SUCCESS
            record.error_msg = None
        # ⑦ async with 正常退出 => 自动 commit
    except Exception as exc:
        # ⑧ 任意步骤异常：begin 上下文已自动回滚；
        #    随后用新事务把流水置为 FAILED，写入失败原因（余额不足/锁超时/DB异常等）。
        mapped = _map_exception(exc)
        await _mark_failed(session, req, mapped.message)
        if isinstance(exc, AppError):
            raise
        raise mapped from exc

    # 故障注入（阶段二）：commit 已成功、但响应返回前"网络断开"。
    # 此时数据库里资金已经变化，前端却只收到超时 —— 经典中间态。
    # 正确姿势：前端用同一个 trade_no 轮询查询接口；重试提交也不会重复扣款。
    if settings.enable_fault_inject and fault_stage == FAULT_STAGE_AFTER_COMMIT:
        raise AppError(
            "NETWORK_FAULT_INJECTED",
            "模拟网络中断：事务已提交，但响应未能返回前端",
            http_status=500,
        )

    # expire_on_commit=False，提交后仍可直接读取对象属性
    return TransferResultResponse.model_validate(record)
