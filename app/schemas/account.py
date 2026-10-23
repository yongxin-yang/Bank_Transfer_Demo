"""账户相关入参/出参模型。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AccountCreateRequest(BaseModel):
    """新建测试账户入参。"""

    account_no: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    # 初始余额 >= 0，最多 2 位小数，禁止超过 DECIMAL(18,2) 的表示范围
    init_balance: Decimal = Field(ge=0, max_digits=18, decimal_places=2)


class AccountResponse(BaseModel):
    """账户信息出参。"""

    model_config = ConfigDict(from_attributes=True)

    account_no: str
    balance: Decimal
