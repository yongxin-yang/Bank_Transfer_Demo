"""数据库连接与 Session 管理。

要点：
1. 全部使用 SQLAlchemy 2.0 异步 API + asyncmy 驱动，禁止阻塞事件循环；
2. innodb_lock_wait_timeout 是 MySQL 会话级变量，必须在每条连接建立时设置，
   否则默认 50 秒，演示"锁等待超时"场景过于漫长；
3. pool_pre_ping 防止从连接池取到已被服务端断开的失效连接。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.settings import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # asyncmy 的 init_command 会在每条新连接建立后自动执行。
    connect_args={
        "init_command": f"SET SESSION innodb_lock_wait_timeout = {settings.db_lock_wait_timeout}",
    },
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个独立 Session，请求结束自动关闭。"""
    async with async_session_factory() as session:
        yield session
