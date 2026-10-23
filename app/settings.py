"""全局配置：通过 pydantic-settings 从 .env 读取，集中管理数据库连接与 Demo 开关。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """应用配置。

    字段名与 .env 环境变量一一对应（大小写不敏感），例如 DB_HOST -> db_host。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 数据库连接 ----
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "transfer_demo"

    # /api/transfer/submit 并发限流数量。
    # Demo 简易限流（进程内 Semaphore），生产环境请使用 Redis 令牌桶 / API 网关限流。
    transfer_semaphore_limit: int = 5

    # InnoDB 锁等待超时（秒）。该参数是 MySQL 会话级变量，在每条连接建立时设置。
    db_lock_wait_timeout: int = 5

    # 故障注入总开关：开启后可在 submit 时通过 fault_stage 参数选择注入阶段。
    enable_fault_inject: bool = False

    @property
    def database_url(self) -> URL:
        """异步驱动 DSN。

        使用 URL.create 而不是手拼字符串：密码中若含 @ : / 等特殊字符，
        手拼会导致 DSN 解析错误。
        """
        return URL.create(
            drivername="mysql+asyncmy",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    """进程内单例配置。"""
    return Settings()
