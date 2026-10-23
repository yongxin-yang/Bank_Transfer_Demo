"""转账相关入参/出参模型。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.transfer import TransferStatus


class TradeNoResponse(BaseModel):
    """申请单号出参。"""

    trade_no: str


class TransferSubmitRequest(BaseModel):
    """提交转账入参。"""

    trade_no: str = Field(min_length=1, max_length=32)
    from_account_no: str = Field(min_length=1, max_length=32)
    to_account_no: str = Field(min_length=1, max_length=32)
    # Decimal，严禁 float；gt=0 拦截 0 与负数，decimal_places=2 拦截超过 2 位小数的金额
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, description="转账金额，>0 且最多 2 位小数")

    @model_validator(mode="after")
    def _check_accounts_different(self) -> "TransferSubmitRequest":
        # 转出转入不能是同一账户：在参数校验层直接拦截，不进入数据库事务
        if self.from_account_no == self.to_account_no:
            raise ValueError("转出账户和转入账户不能相同")
        return self


class TransferResultResponse(BaseModel):
    """转账结果出参（提交返回 / 查询返回共用）。"""

    model_config = ConfigDict(from_attributes=True)

    trade_no: str
    status: TransferStatus
    from_account_no: str | None = None
    to_account_no: str | None = None
    amount: Decimal | None = None
    error_msg: str | None = None
