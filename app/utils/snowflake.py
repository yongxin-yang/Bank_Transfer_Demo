"""雪花算法转账单号生成。

64 位结构：1 位符号 + 41 位时间戳 + 10 位机器号 + 12 位序列号，
单机每毫秒可生成 4096 个不重复 ID，天然按时间递增。

trade_no 对外一律使用字符串：19 位整数超出 JS Number 的安全整数范围，
前端直接按数字解析会丢精度，导致"看起来拿到单号、实际查不到"的诡异问题。
"""

from snowflake import SnowflakeGenerator

_generator = SnowflakeGenerator(42)


def next_trade_no() -> str:
    """生成下一个全局唯一转账单号（字符串形式）。"""
    return str(next(_generator))
