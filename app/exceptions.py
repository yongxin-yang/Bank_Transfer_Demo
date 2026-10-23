"""业务异常与全局异常处理。

统一 JSON 错误格式 {"code": "...", "message": "..."}：
不把原生异常（堆栈、SQL 语句）直接抛给前端，避免泄露内部细节，
同时保证前端可以用稳定的错误码做分支处理。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务异常基类：携带对外错误码、提示文案和 HTTP 状态码。"""

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": f"参数校验失败: {details}"},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "服务内部异常，请稍后重试"},
        )
