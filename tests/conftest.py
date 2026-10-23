"""pytest 公共夹具。

注意：测试直接使用 .env 配置的 MySQL 库，每个用例前会 TRUNCATE
account / transfer_record 两张表 —— 请勿把该库指向包含真实数据的数据库。
"""

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.database import async_session_factory, engine
from app.main import app
from app.models.account import Account
from app.services.limiter import transfer_limiter
from app.settings import get_settings


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine():
    """所有用例结束后释放连接池，避免事件循环关闭时连接未清理。"""
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_state():
    """每个用例前清空业务表并恢复默认配置，保证用例互不影响。"""
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE transfer_record"))
        await conn.execute(text("TRUNCATE TABLE account"))
    get_settings().enable_fault_inject = False
    transfer_limiter.reset()
    yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    # 超时必须大于 DB_LOCK_WAIT_TIMEOUT：锁等待超时用例本身要等 5 秒左右
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as c:
        yield c


@pytest_asyncio.fixture
async def api(client):
    """把常用接口调用封装成便捷方法，避免用例里重复写请求代码。"""

    async def create_account(account_no: str, balance: str = "0.00") -> httpx.Response:
        return await client.post(
            "/api/account/create",
            json={"account_no": account_no, "init_balance": balance},
        )

    async def apply_no() -> str:
        resp = await client.post("/api/transfer/apply-no")
        assert resp.status_code == 200, resp.text
        return resp.json()["trade_no"]

    async def submit(
        trade_no: str,
        from_no: str,
        to_no: str,
        amount: str,
        fault_stage: str | None = None,
    ) -> httpx.Response:
        params = {"fault_stage": fault_stage} if fault_stage else None
        return await client.post(
            "/api/transfer/submit",
            params=params,
            json={
                "trade_no": trade_no,
                "from_account_no": from_no,
                "to_account_no": to_no,
                "amount": amount,
            },
        )

    async def get_transfer(trade_no: str) -> httpx.Response:
        return await client.get(f"/api/transfer/{trade_no}")

    async def get_balance(account_no: str) -> Decimal:
        resp = await client.get(f"/api/account/{account_no}")
        assert resp.status_code == 200, resp.text
        return Decimal(str(resp.json()["balance"]))

    return SimpleNamespace(
        client=client,
        create_account=create_account,
        apply_no=apply_no,
        submit=submit,
        get_transfer=get_transfer,
        get_balance=get_balance,
    )


@pytest.fixture
def enable_fault_inject():
    """打开故障注入开关（用例结束后由 _clean_state 自动恢复）。"""
    get_settings().enable_fault_inject = True


@pytest_asyncio.fixture
async def account_lock():
    """在独立 Session 的长事务中持有账户行锁，用于构造锁竞争场景。"""
    holders: list = []

    async def _lock(account_no: str):
        session = async_session_factory()
        await session.begin()
        await session.execute(
            select(Account).where(Account.account_no == account_no).with_for_update()
        )
        holders.append(session)
        return session

    yield _lock
    for session in holders:
        # close 会自动回滚未提交事务并释放行锁
        await session.close()
