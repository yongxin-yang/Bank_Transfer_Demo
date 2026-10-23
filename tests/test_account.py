"""账户接口测试。"""

from decimal import Decimal


async def test_create_and_get_account(api):
    resp = await api.create_account("ACC_A", "100.00")
    assert resp.status_code == 200, resp.text
    assert Decimal(str(resp.json()["balance"])) == Decimal("100.00")

    resp = await api.client.get("/api/account/ACC_A")
    assert resp.status_code == 200
    assert Decimal(str(resp.json()["balance"])) == Decimal("100.00")


async def test_duplicate_account_rejected(api):
    assert (await api.create_account("ACC_A", "1.00")).status_code == 200
    resp = await api.create_account("ACC_A", "2.00")
    assert resp.status_code == 409
    assert resp.json()["code"] == "ACCOUNT_EXISTS"


async def test_get_unknown_account_returns_404(api):
    resp = await api.client.get("/api/account/NOT_EXIST")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ACCOUNT_NOT_FOUND"
