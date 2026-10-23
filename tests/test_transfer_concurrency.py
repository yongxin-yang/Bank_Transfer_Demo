"""并发用例：热点账户、双向并发防死锁、锁等待超时、限流阈值。"""

import asyncio
from decimal import Decimal

from app.services.limiter import transfer_limiter


async def test_hot_account_concurrent_debit(api):
    """用例5：热点账户高并发扣款，行锁保护下不会超扣，最终余额数学一致。"""
    transfer_limiter.reset(50)  # 提高限流阈值，让请求真正并发竞争行锁
    await api.create_account("ACC_HOT", "100.00")
    await api.create_account("ACC_B", "0.00")

    trade_nos = [await api.apply_no() for _ in range(20)]
    responses = await asyncio.gather(
        *[api.submit(trade_no, "ACC_HOT", "ACC_B", "1.00") for trade_no in trade_nos]
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json()["status"] == "SUCCESS" for r in responses)

    assert await api.get_balance("ACC_HOT") == Decimal("80.00")
    assert await api.get_balance("ACC_B") == Decimal("20.00")


async def test_bidirectional_transfer_no_deadlock(api):
    """用例6：A<->B 双向并发转账，固定锁顺序保证不会发生死锁。"""
    transfer_limiter.reset(50)
    await api.create_account("ACC_AA", "1000.00")
    await api.create_account("ACC_BB", "1000.00")

    tasks = []
    for _ in range(10):
        a2b = await api.apply_no()
        b2a = await api.apply_no()
        tasks.append(api.submit(a2b, "ACC_AA", "ACC_BB", "10.00"))
        tasks.append(api.submit(b2a, "ACC_BB", "ACC_AA", "10.00"))

    responses = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json()["status"] == "SUCCESS" for r in responses)

    assert await api.get_balance("ACC_AA") == Decimal("1000.00")
    assert await api.get_balance("ACC_BB") == Decimal("1000.00")


async def test_lock_wait_timeout_marks_failed(api, account_lock):
    """用例7：长事务抢占行锁，后到请求/锁等待超时 -> 回滚 + 记录失败流水。"""
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")
    trade_no = await api.apply_no()

    holder = await account_lock("ACC_A")
    # 本次请求会等待 innodb_lock_wait_timeout（.env 配置 5 秒）后报 1205
    resp = await api.submit(trade_no, "ACC_A", "ACC_B", "10.00")
    await holder.rollback()

    assert resp.status_code == 503
    assert resp.json()["code"] == "LOCK_WAIT_TIMEOUT"

    assert await api.get_balance("ACC_A") == Decimal("100.00")
    assert await api.get_balance("ACC_B") == Decimal("0.00")

    body = (await api.get_transfer(trade_no)).json()
    assert body["status"] == "FAILED"
    assert "锁等待超时" in body["error_msg"]


async def test_semaphore_rejects_when_saturated(api, account_lock):
    """用例10：并发超过限流阈值，超出的请求直接返回繁忙，不会涌入数据库。"""
    transfer_limiter.reset(1)
    await api.create_account("ACC_A", "100.00")
    await api.create_account("ACC_B", "0.00")

    trade_nos = [await api.apply_no() for _ in range(5)]
    holder = await account_lock("ACC_A")

    tasks = [
        asyncio.create_task(api.submit(trade_no, "ACC_A", "ACC_B", "1.00"))
        for trade_no in trade_nos
    ]
    # 等所有请求完成信号量抢占：1 个进入核心逻辑并阻塞在行锁上，其余 4 个被限流
    await asyncio.sleep(0.5)
    await holder.rollback()  # 释放行锁，放行已进入核心逻辑的请求

    responses = await asyncio.gather(*tasks)
    busy = [r for r in responses if r.status_code == 503 and r.json()["code"] == "SERVICE_BUSY"]
    success = [r for r in responses if r.status_code == 200]
    assert len(busy) == 4
    assert len(success) == 1

    assert await api.get_balance("ACC_A") == Decimal("99.00")
    assert await api.get_balance("ACC_B") == Decimal("1.00")
