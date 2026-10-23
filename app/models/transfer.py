"""转账流水表 ORM 模型（幂等核心）。"""

import enum
from decimal import Decimal

from sqlalchemy import BigInteger, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TransferStatus(str, enum.Enum):
    """流水状态。INIT 阶段发生于 apply-no 颁发单号之后、submit 执行之前。"""

    INIT = "INIT"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class TransferRecord(Base, TimestampMixin):
    """转账流水表。

    trade_no 唯一索引是幂等的数据库层兜底：
    即使应用层并发控制失效，同一个单号也不可能落两条流水。
    """

    __tablename__ = "transfer_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_no: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        index=True,
        nullable=False,
        comment="后端颁发的雪花单号（唯一索引）",
    )
    # apply-no 阶段还不知道转出/转入账户与金额，因此以下字段先允许为空，submit 时回填
    from_account_no: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True, comment="转出账户号"
    )
    to_account_no: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True, comment="转入账户号"
    )
    amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, comment="转账金额"
    )
    status: Mapped[TransferStatus] = mapped_column(
        SAEnum(
            TransferStatus,
            name="transfer_status",
            values_callable=lambda members: [m.value for m in members],
        ),
        nullable=False,
        default=TransferStatus.INIT,
        comment="流水状态: INIT/SUCCESS/FAILED",
    )
    error_msg: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="失败原因"
    )
