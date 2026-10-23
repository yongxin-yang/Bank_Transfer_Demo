"""FastAPI 应用入口。"""

import logging

from fastapi import FastAPI

from app.exceptions import register_exception_handlers
from app.routes.account import router as account_router
from app.routes.transfer import router as transfer_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="银行转账模拟 Demo",
        description=(
            "⚠️ 仅用于学习资金转账一致性问题的教学 Demo，严禁用于真实资金场景。\n\n"
            "演示：幂等、行锁、固定锁顺序防死锁、事务回滚、锁等待超时、"
            "commit 后网络中断的中间态、简易并发限流、故障注入。"
        ),
        version="0.1.0",
    )
    register_exception_handlers(app)
    app.include_router(account_router, prefix="/api/account", tags=["账户"])
    app.include_router(transfer_router, prefix="/api/transfer", tags=["转账"])
    return app


app = create_app()
