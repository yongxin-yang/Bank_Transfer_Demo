"""账户接口：新建测试账户、查询账户余额。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.exceptions import AppError
from app.models.account import Account
from app.schemas.account import AccountCreateRequest, AccountResponse

router = APIRouter()


@router.post("/create", response_model=AccountResponse, summary="新建测试账户")
async def create_account(
    payload: AccountCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> Account:
    """仅供构造测试数据使用。"""
    account = Account(account_no=payload.account_no, balance=payload.init_balance)
    try:
        async with session.begin():
            session.add(account)
    except IntegrityError as exc:
        # 唯一索引兜底：并发创建同名账户时也只有一条能成功
        raise AppError("ACCOUNT_EXISTS", f"账户已存在: {payload.account_no}", http_status=409) from exc
    return account


@router.get("/{account_no}", response_model=AccountResponse, summary="查询账户余额")
async def get_account(
    account_no: str,
    session: AsyncSession = Depends(get_session),
) -> Account:
    async with session.begin():
        account = await session.scalar(select(Account).where(Account.account_no == account_no))
    if account is None:
        raise AppError("ACCOUNT_NOT_FOUND", f"账户不存在: {account_no}", http_status=404)
    return account
