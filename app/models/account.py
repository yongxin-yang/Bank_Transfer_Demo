"""账户表 ORM 模型。"""

from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Account(Base, TimestampMixin):
    """账户表。

    balance 使用 DECIMAL(18,2)：金融金额严禁使用 float，
    二进制浮点无法精确表示 0.1 这类十进制小数，多次加减后会产生精度漂移。
    """

    __tablename__ = "account"
    __table_args__ = (
        # 数据库层兜底：即使业务代码有 bug，余额也不允许被写成负数（MySQL 8.0.16+ 真正生效）
        CheckConstraint("balance >= 0", name="ck_account_balance_non_negative"),
        {"comment": "账户表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # account_no 必须建唯一索引：
    # ① 业务上账户号不能重复；
    # ② submit 中 select ... for update 的等值条件命中唯一索引时才只加行锁，
    #    若条件落在无索引列上，InnoDB 会退化为锁全表（防止升级表锁）。
    account_no: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        index=True,
        nullable=False,
        comment="账户号（唯一索引）",
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
        comment="余额（2 位小数）",
    )
