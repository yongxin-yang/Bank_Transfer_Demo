"""转账接口：申请单号、提交转账、查询结果。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.transfer import (
    TradeNoResponse,
    TransferResultResponse,
    TransferSubmitRequest,
)
from app.services.limiter import transfer_limiter
from app.services.transfer import apply_trade_no, get_transfer_result, submit_transfer

router = APIRouter()


@router.post("/apply-no", response_model=TradeNoResponse, summary="申请转账单号（后端颁发雪花ID）")
async def apply_no(session: AsyncSession = Depends(get_session)) -> TradeNoResponse:
    trade_no = await apply_trade_no(session)
    return TradeNoResponse(trade_no=trade_no)


@router.post("/submit", response_model=TransferResultResponse, summary="提交转账（核心接口）")
async def submit(
    payload: TransferSubmitRequest,
    fault_stage: str | None = Query(
        default=None,
        description="故障注入阶段：mid_tx（事务中途）/ after_commit（提交后断网），仅当 ENABLE_FAULT_INJECT=true 时生效",
    ),
    session: AsyncSession = Depends(get_session),
) -> TransferResultResponse:
    # Demo 简易限流：进程内信号量限制进入转账核心逻辑的并发数，保护数据库。
    # ⚠️ 生产环境使用 Redis 令牌桶 / API 网关限流，多副本下才有全局约束。
    async with transfer_limiter.acquire():
        return await submit_transfer(session, payload, fault_stage)


@router.get("/{trade_no}", response_model=TransferResultResponse, summary="查询转账结果（前端轮询）")
async def query_transfer(
    trade_no: str,
    session: AsyncSession = Depends(get_session),
) -> TransferResultResponse:
    return await get_transfer_result(session, trade_no)
