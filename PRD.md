你现在要开发一个学习用的银行转账模拟后端Demo，技术栈固定：FastAPI、SQLAlchemy 2.0（异步模式）、asyncmy、MySQL，uv管理依赖。
⚠️ 重要：这是教学Demo，仅用于学习资金转账一致性问题，严禁用于真实资金场景。

## 重要修正：trade_no 生成规则（金融标准方案）
不要让前端传入自定义trade_no！拆成两步：
1. 前端先调用【申请转账单号接口】，后端使用雪花算法生成全局唯一trade_no返回前端
2. 前端拿着后端下发的trade_no，再调用转账提交接口提交转账
幂等逻辑依靠后端颁发的trade_no；transfer_record表trade_no设置唯一索引兜底，防止重复业务执行。

## 项目需求总目标
模拟账户之间转账，复现面试问到的场景：转账成功、数据库报错回滚、锁等待堵塞、重复提交、commit后网络中断、死锁风险；实现幂等防护、并发限流，附带覆盖大量极端场景的完整pytest测试用例。

## 需要实现的数据库表（alembic迁移）
1. account 账户表
字段：id(主键bigint)，account_no(账户号，唯一索引)，balance(Decimal 余额，保留2位小数，不能负数)，created_at，updated_at
2. transfer_record 转账流水表（幂等核心）
字段：id，trade_no(雪花ID，唯一索引！)，from_account_no，to_account_no，amount(Decimal)
status枚举：[INIT 初始化, SUCCESS 成功, FAILED 失败]
error_msg：失败原因，created_at, updated_at

## 需要对外暴露的REST JSON接口
1. POST /api/transfer/apply-no
作用：申请转账单号，后端生成雪花trade_no返回给前端，此时流水记录状态INIT
入参：无
返回：{"trade_no": "xxx"}

2. POST /api/transfer/submit 提交转账（核心转账接口）
入参：
{
  "trade_no": str,
  "from_account_no": str,
  "to_account_no": str,
  "amount": Decimal
}
业务逻辑顺序严格遵守：
① 幂等校验：优先查询transfer_record，根据trade_no查询已有记录；
   - 如果状态SUCCESS/FAILED：直接返回已有结果，不再执行业务逻辑
   - 如果不存在该trade_no：直接返回参数错误（单号必须是后端预先颁发的）
② 参数校验：转出转入账户不能相同，amount>0
③ 开启数据库事务，select ... for update 行锁锁定两个账户；
✅ 强制上锁规则：永远先锁 account_no 字典序小的账户，再锁字典序大的账户，从根源消除死锁；必须注释写明该规则的目的。
④ 校验转出账户余额充足；执行转出账户扣减，转入账户增加余额
⑤ 更新流水记录状态为SUCCESS
⑥ 提交事务
⑦ 任意步骤异常：事务回滚，更新流水状态FAILED，写入error_msg
⑧ 配置数据库锁等待超时，捕获锁超时异常，标记失败

3. GET /api/transfer/{trade_no} 查询转账结果
根据trade_no查询流水状态，用于前端轮询，专门解决【后端commit成功、但是返回前端时网络断开，前端收到超时】的经典中间状态场景。

4. POST /api/account/create 新建测试账户
入参 account_no, init_balance，用于构造测试数据

5. GET /api/account/{account_no} 查询账户余额

## 并发 & 错误防护逻辑
1. 幂等：trade_no由后端统一颁发，流水表唯一索引兜底；同一个trade_no提交多次只会生效一次
2. 锁约束：必须行锁，禁止表锁；account_no必须索引，for update查询条件命中索引，注释写明“防止升级表锁”
3. 简易并发限流：使用asyncio.Semaphore对 /api/transfer/submit 做接口层限流，限制同一时间进入转账核心逻辑的并发数量，保护数据库，注释标注：Demo简易限流，生产使用Redis令牌桶/网关限流
4. 异常分类处理：
   - 余额不足 → 回滚，标记FAILED
   - Innodb锁等待超时 → 回滚，标记FAILED，提示稍后重试
   - SQL异常、服务内部异常 → 事务回滚，记录失败流水
   - 重复trade_no（已完结状态）→ 返回历史结果，不重复执行业务
5. 接口内部禁止自动无限重试；重试交给调用方，重试必须携带同一个trade_no
6. 故障注入开关：全局配置 enable_fault_inject，开启后可以在事务中【扣钱完成、加钱之前】主动抛出异常，用来测试事务回滚；额外支持模拟commit之后、响应返回前抛出网络异常，模拟commit成功但是前端超时的场景

## 代码结构规范
- 分层：routes（接口）、services（转账业务、事务逻辑）、models（sqlalchemy orm模型）、schemas（pydantic入参出参）、database（连接、session管理）、settings（pydantic-settings读取.env）、utils（雪花ID工具）
- 全部异步async，禁止阻塞事件循环
- 所有金融金额使用Decimal，严禁float，注释说明浮点精度丢失风险
- 事务统一 async with session.begin()
- 全局异常捕获，统一JSON返回格式，不把原生异常直接抛给前端
- 核心业务代码大量中文注释，解释：幂等、行锁、锁顺序防死锁、防止表锁、事务原子性的意义

## 测试要求（pytest + pytest-asyncio + httpx TestClient，**必须覆盖极端场景**）
测试清单：
1. 正常转账流程：申请单号→提交转账，A扣钱B加钱，流水SUCCESS
2. 非法参数：转出转入同一账户、金额0、负金额，直接拦截，不进事务
3. 余额不足转账：事务回滚，账户余额不变，流水FAILED
4. 幂等重复提交：同一个trade_no多次调用submit，资金只变更一次
5. 热点账户高并发：大量协程同时操作同一个转出账户，验证行锁保护，最终余额数学一致，不会超扣
6. 双向并发转账：A转B 和 B转A 同时并发执行，验证固定锁顺序，**不会发生死锁**
7. 锁等待超时场景：构造长事务抢占行锁，后到请求触发锁超时，回滚、记录失败流水
8. 故障注入：事务中途（扣钱后、加钱前）抛出异常，验证完整回滚，余额不变
9. 经典中间态：事务commit成功，但是返回前端前模拟网络异常；前端查询接口可以查到转账成功，再次提交不会重复转账
10. 并发超过限流阈值：验证限流生效，超出并发请求直接返回繁忙，不会涌入数据库
11. 边界金额测试：最小金额0.01转账、大额金额转账，验证Decimal精度正确
12. 传入不存在/伪造的trade_no：直接报错拒绝处理

## 配套文件
1. .env.example：数据库地址、并发限流数量、锁等待超时时间、故障注入开关
2. README文档：
   - uv add依赖命令、alembic初始化、迁移执行步骤
   - 服务启动方式
   - 每一个接口curl调用示例，完整演示【申请单号→提交转账→查询结果】全流程
   - 逐条说明每个测试case如何运行、预期现象
   - 清晰写明Demo局限性：本Demo只有本地单库事务；生产环境还需要：分布式锁、MQ削峰、SAGA/TCC、定时对账任务、异常人工调账表、分布式链路追踪

## 开发顺序（分步开发）
1. 项目骨架、ORM模型、alembic配置、雪花ID工具
2. 数据库session封装、全局异常处理
3. 账户新建、账户查询接口
4. 申请trade_no接口
5. 转账submit核心service（事务、幂等、for update、固定锁顺序，重点写注释）
6. 转账查询接口、Semaphore限流、故障注入逻辑
7. 编写全部pytest测试用例
8. 撰写README、curl示例，自我验证全部case通过

## 硬性禁止项
❌ 不要引入Redis、MQ等额外中间件，保持Demo最简，用于学习
❌ 不要省略trade_no唯一索引、account_no索引
❌ 不要写同步数据库代码，全部异步
❌ 不要让前端自由传入自定义trade_no
