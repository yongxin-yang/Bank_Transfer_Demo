"""故障注入用例：事务中途回滚、commit 后断网中间态。"""

from decimal import Decimal


async def test_fault_inject_mid_tx_rolls_back(api, enable_fault_inject):
    """用例8：扣款后、入账前抛异常 -> 完整回滚，余额不变，流水 FAILED。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()

    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "30.00", fault_stage="mid_tx")
    assert resp.status_code == 500
    assert resp.json()["code"] == "INTERNAL_ERROR"

    assert await api.get_balance("ACC_A") == Decimal("100.00")
    assert await api.get_balance("ACC_B") == Decimal("0.00")

    body = (await api.get_transfer(trade_no)).json()
    assert body["status"] == "FAILED"
    assert body["error_msg"]


async def test_after_commit_network_fault_then_poll_and_retry(api, enable_fault_inject):
    """用例9：commit 成功但响应前断网 -> 查询可查到 SUCCESS，重试不重复转账。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()

    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "30.00", fault_stage="after_commit")
    assert resp.status_code == 500
    assert resp.json()["code"] == "NETWORK_FAULT_INJECTED"

    # 经典中间态：前端只收到超时，但数据库里资金已经变化
    body = (await api.get_transfer(trade_no)).json()
    assert body["status"] == "SUCCESS"
    assert await api.get_balance("ACC_A") == Decimal("70.00")
    assert await api.get_balance("ACC_B") == Decimal("30.00")

    # 前端凭同一个 trade_no 重试：返回历史结果，不会重复扣款
    retry = await api.submit(trade_no, "ACC_A", "ACC_B", "30.00")
    assert retry.status_code == 200
    assert retry.json()["status"] == "SUCCESS"
    assert await api.get_balance("ACC_A") == Decimal("70.00")
    assert await api.get_balance("ACC_B") == Decimal("30.00")


async def test_fault_stage_requires_global_switch(api):
    """总开关关闭时，带 fault_stage 的请求被拒绝，流水保持 INIT。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()
    
    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "10.00", fault_stage="mid_tx")
    assert resp.status_code == 400
    assert resp.json()["code"] == "FAULT_INJECT_DISABLED"
    assert await api.get_balance("ACC_A") == Decimal("100.00")
    assert (await api.get_transfer(trade_no)).json()["status"] == "INIT"
