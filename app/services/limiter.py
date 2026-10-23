"""Demo 简易并发限流。

⚠️ 生产环境请使用 Redis 令牌桶或 API 网关限流：
asyncio.Semaphore 只在单进程内生效，多副本部署时每个实例各限各的，无法形成全局约束。
限流的目的：限制同一时间进入转账核心逻辑（数据库事务）的请求数，保护数据库不被拖垮。
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.exceptions import AppError
from app.settings import get_settings


class TransferLimiter:
    """基于 asyncio.Semaphore 的进程内限流器。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.semaphore = asyncio.Semaphore(limit)

    def reset(self, limit: int | None = None) -> None:
        """重建信号量（测试用：模拟不同的限流阈值）。"""
        if limit is not None:
            self.limit = limit
        self.semaphore = asyncio.Semaphore(self.limit)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        # 非阻塞抢占：信号量已被占满说明并发达到阈值，
        # 直接返回"繁忙"，绝不让请求排队堆积、最终一起涌入数据库。
        if self.semaphore.locked():
            raise AppError("SERVICE_BUSY", "当前转账请求过多，请稍后重试", http_status=503)
        await self.semaphore.acquire()
        try:
            yield
        finally:
            # 无论成功失败都必须释放，否则限流名额会被永久占用
            self.semaphore.release()


# 进程级单例：限流阈值来自 .env 的 TRANSFER_SEMAPHORE_LIMIT
transfer_limiter = TransferLimiter(get_settings().transfer_semaphore_limit)
