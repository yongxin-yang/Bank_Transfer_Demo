"""基础转账用例：正常流程、非法参数、余额不足、幂等、边界金额、伪造单号。"""

import asyncio
from decimal import Decimal


async def test_normal_transfer_flow(api):
    """用例1：申请单号 -> 提交转账，A 扣钱 B 加钱，流水 SUCCESS。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")

    trade_no = await api.apply_no()

    # 申请单号后流水应为 INIT
    resp = await api.get_transfer(trade_no)
    assert resp.status_code == 200
    assert resp.json()["status"] == "INIT"

    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "30.00")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["trade_no"] == trade_no

    assert await api.get_balance("ACC_A") == Decimal("70.00")
    assert await api.get_balance("ACC_B") == Decimal("30.00")


async def test_invalid_params_rejected_before_transaction(api):
    """用例2：同一账户/金额 0/负金额/超过 2 位小数，参数层直接拦截，不进事务。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()

    resp = await api.submit(trade_no, "ACC_A", "ACC_A", "10.00")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    for bad_amount in ("0", "-1.00", "0.001"):
        resp = await api.submit(trade_no, "ACC_A", "ACC_B", bad_amount)
        assert resp.status_code == 422, f"amount={bad_amount} 应被拦截"

    # 余额不变，且流水仍是 INIT（非法参数不进入事务，不产生 FAILED 终态）
    assert await api.get_balance("ACC_A") == Decimal("100.00")
    assert await api.get_balance("ACC_B") == Decimal("0.00")
    assert (await api.get_transfer(trade_no)).json()["status"] == "INIT"


async def test_insufficient_balance_marks_failed(api):
    """用例3：余额不足 -> 事务回滚，余额不变，流水 FAILED。"""
    await api.create_account("ACC_A", "10.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()

    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "10.01")
    assert resp.status_code == 409
    assert resp.json()["code"] == "INSUFFICIENT_BALANCE"

    assert await api.get_balance("ACC_A") == Decimal("10.00")
    assert await api.get_balance("ACC_B") == Decimal("0.00")

    body = (await api.get_transfer(trade_no)).json()
    assert body["status"] == "FAILED"
    assert "余额不足" in body["error_msg"]


async def test_idempotent_repeat_and_concurrent_submit(api):
    """用例4：同一个 trade_no 多次/并发调用 submit，资金只变更一次。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()

    # 串行重复提交
    first = await api.submit(trade_no, "ACC_A", "ACC_B", "30.00")
    second = await api.submit(trade_no, "ACC_A", "ACC_B", "30.00")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "SUCCESS"
    assert await api.get_balance("ACC_A") == Decimal("70.00")
    assert await api.get_balance("ACC_B") == Decimal("30.00")

    # 并发重复提交：流水行锁把同单号请求串行化，只有一个真正执行
    trade_no2 = await api.apply_no()
    responses = await asyncio.gather(
        api.submit(trade_no2, "ACC_A", "ACC_B", "20.00"),
        api.submit(trade_no2, "ACC_A", "ACC_B", "20.00"),
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json()["status"] == "SUCCESS" for r in responses)

    assert await api.get_balance("ACC_A") == Decimal("50.00")
    assert await api.get_balance("ACC_B") == Decimal("50.00")


async def test_boundary_amounts_decimal_precision(api):
    """用例11：0.01 最小金额 + 大额金额，Decimal 精度不丢失。"""
    await api.create_account("ACC_A", "1.00")
    await api.create_account("ACC_B", "0.00")

    trade_no = await api.apply_no()
    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "0.01")
    assert resp.status_code == 200, resp.text
    assert await api.get_balance("ACC_A") == Decimal("0.99")
    assert await api.get_balance("ACC_B") == Decimal("0.01")

    await api.create_account("ACC_C", "1000000000.10")
    trade_no = await api.apply_no()
    resp = await api.submit(trade_no, "ACC_C", "ACC_B", "999999999.99")
    assert resp.status_code == 200, resp.text
    assert await api.get_balance("ACC_C") == Decimal("0.11")
    assert await api.get_balance("ACC_B") == Decimal("1000000000.00")


async def test_fake_trade_no_rejected(api):
    """用例12：伪造/不存在的 trade_no 直接拒绝。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")

    resp = await api.submit("123456789012345678", "ACC_A", "ACC_B", "10.00")
    assert resp.status_code == 400
    assert resp.json()["code"] == "TRADE_NO_NOT_FOUND"
    assert await api.get_balance("ACC_A") == Decimal("100.00")

    # 缺少 trade_no 的请求在参数层被拦截
    resp = await api.client.post(
        "/api/transfer/submit",
        json={"from_account_no": "ACC_A", "to_account_no": "ACC_B", "amount": "1.00"},
    )
    assert resp.status_code == 422
