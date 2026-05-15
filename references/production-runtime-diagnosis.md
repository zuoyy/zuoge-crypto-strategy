# 生产运行时诊断：策略是否正常？

当用户问"检查策略是否正常"时，按以下清单逐级诊断。目标是快速回答三个问题：
1. 进程是否活着？
2. 是否在产出决策/信号？
3. 如果没有，根因是什么？

## 1. 进程层

### 确认进程存在与管理方式

```bash
# 方式 A：health API 最权威
curl -s http://127.0.0.1:18000/api/v1/health | python3 -m json.tool
# 输出中 "processes"."realtime-strategy" 显示 manager=launchd, pid=<PID>

# 方式 B：直接 ps
ps aux | grep realtime_main | grep -v grep

# 方式 C：launchctl（注意：Label 是 com.crypto-trader.realtime-strategy，可能不显式列出）
launchctl list | grep crypto
```

### 确认进程环境与工作目录

```bash
# 查看进程的环境变量
ps eww <PID> | tr ' ' '\n' | grep -E 'STRATEGY|NATS|DATABASE|HTTP'

# 查看打开的文件和工作目录
lsof -p <PID> | grep -E 'cwd|\.py$|\.log$|TCP|ESTABLISHED'
# cwd → release 目录路径
# stdout/stderr → 日志文件路径
# TCP → NATS/API 连接状态
```

### 确认运行代码来源

```bash
ls -la /opt/homebrew/var/crypto-trader/current           # → symlink → release 目录
lsof -p <PID> | grep cwd                                  # 进程的工作目录 = 实际 release 目录
```

### macOS 专用：sample 探查进程在做什么

当进程活着但没有日志输出时，用 `sample` 抓取调用栈：

```bash
sample <PID> 2 2>&1 | head -50
```

关键信号：
- `start → dyld` 中 → 进程卡在启动/加载阶段
- `_asyncio` 中 → 在事件循环内正常运行
- `_PyEval_EvalFrameDefault` 深层嵌套 → 正在执行 Python 代码
- `nats.*subscribe` / `nats.*connect` → 正在操作 NATS

## 2. 策略加载层

### 验证策略是否正确加载

```bash
cd /opt/homebrew/var/crypto-trader/current
python3 -c "
import sys; sys.path.insert(0,'strategy')
from runtime.strategy_manager import load_enabled_strategies
h,e = load_enabled_strategies()
print(f'Handles: {len(h)}, Errors: {len(e)}')
for x in e: print(f'  ✗ {x}')
for x in h: print(f'  ✓ {x.strategy_id} v{x.version}')
"
```

期望输出：`Handles: 1, Errors: 0  ✓ workflow_distilled_funnel v0.1.0`

如果 `Errors: 1 → code hash mismatch`：manifest 未更新，策略静默加载失败。参照 SKILL.md 「生产直接修改策略文件的完整步骤」。

### 验证 manifest hash 匹配

```bash
# 计算实际 hash
shasum -a 256 /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py
# 对比 manifest 中的 code_hash
cat /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.manifest.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('code_hash',''))"
```

### 查看 strategy_status 心跳

```sql
SELECT strategy_id, runtime_status, last_heartbeat_at, last_signal_at,
       signal_count_24h, reject_count_24h, updated_at
FROM strategy_status
WHERE strategy_id = 'workflow_distilled_funnel';
```

- `last_heartbeat_at` 超过 1 分钟未更新 → 进程可能卡住或心跳回路断裂
- `signal_count_24h = 0` 但进程活着 → 策略产出问题（decision logs 下一步排查）

## 3. 决策产出层

### 检查是否在产出决策

```sql
SELECT COUNT(*) FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '1 minute';
```

如果 count = 0：
- `handle_universe` 未被调用 → universe.delta 无消息，或 handle_universe 内部异常
- 候选池为空 → discover() 被过滤、candidate limit 或 BTC regime 拦截

### 分析拒绝根因分布

```sql
SELECT decision, reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '30 minutes'
GROUP BY decision, reason
ORDER BY cnt DESC
LIMIT 20;
```

这是最关键的诊断查询。输出直接告诉你为什么没有信号：

| 拒绝原因 | 含义 | 调整方向 |
|----------|------|---------|
| breakout_without_1h_4h_confirmation | 4h 趋势不配合 | 放宽 trend_4h 阈值 |
| neutral_probe_too_weak | 弱信号分数不够 | 降低 neutral_probe score floor |
| book_not_supporting_short/long | 盘口方向与候选方向矛盾 | 检查 book gate 阈值 |
| spread_too_wide | 点差过大 | 放宽 spread 上限 |
| same_side_already_near_max_exposure | 同向敞口已满 | 增加敞口上限或等平仓 |
| strategy_budget_missing | 账户风险预算不可用 | API overlay 失败或账户满仓 |
| do_not_chase_pump | 追涨拦截 | 币种涨幅超 8% 门禁 |

### 检查信号表

```sql
SELECT signal_id, symbol, side, status, created_at
FROM signals
WHERE created_at > now() - interval '1 hour'
ORDER BY created_at DESC LIMIT 10;
```

- status = `expired` → worker 未及时消费（signal poller 间隔太长 / expire_ms 太短）
- status = `rejected` → 查 strategy_signal_rejects 表

## 4. NATS 连通层

### ⚠️ 决策日志才是活着的唯一证据

`subsz` 可能不显示 `nats-py` 客户端创建的订阅（取决于 NATS 版本和订阅方式）。**如果 decision_logs 有最近记录，进程就是活着的**——即使 subsz 看不到 strategy 订阅也不要误判为“进程卡住”。

```sql
-- 活着的证据：最近 1 分钟内有新决策
SELECT COUNT(*) FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '1 minute';
```

### 检查 NATS 连接与订阅

```bash
# 总览
curl -s http://127.0.0.1:8222/varz | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'connections:{d.get(\"connections\")} subs:{d.get(\"subscriptions\")}')"

# strategy 相关订阅（注意：nats-py 订阅可能不显式列出）
curl -s http://127.0.0.1:8222/subsz | python3 -c "
import sys,json
subs=json.load(sys.stdin).get('list',[])
for s in subs:
    if 'universe' in s.get('subject','').lower() or 'strategy' in s.get('subject','').lower():
        print(f\"{s['subject']} msgs={s.get('msgs','?')}\")
"
```

- 如果无 strategy 相关订阅 **但 decision_logs 有最近记录** → 订阅正常工作，只是 subsz 不显示
- 如果无 strategy 相关订阅 **且 decision_logs 无最近记录** → 策略初始化未到达 subscribe 阶段，或订阅失败
- 如果 `msgs` 持续为 0 → universe.delta 没有消息（feature-engine 问题）

## 5. 账户/持仓层

### 检查持仓敞口

```sql
SELECT venue, symbol, position_side, quantity, avg_entry_price, notional
FROM execution_position_basis
ORDER BY updated_at DESC;
```

### 检查风险预算配置

```sql
SELECT strategy_id, allocation_pct, max_order_notional_pct,
       max_symbol_exposure_pct, max_total_exposure_pct
FROM strategy_risk_allocations
WHERE strategy_id = 'workflow_distilled_funnel';
```

- `max_order_notional_pct` → 策略中 `max_order_pct = risk_limits.max_order_notional_pct / 100`，动态读取
- `allocation_pct` → 策略中 `allocated_equity` 的计算基础

## 6. 常见故障模式速查

| 症状 | 诊断路径 | 常见根因 |
|------|---------|---------|
| 进程不在 | launchctl list / ps | 崩溃后 KeepAlive 未生效、plist 被删除 |
| 进程在但无日志 | sample + stdout.log | Python print 缓冲（文件输出默认为全缓冲 4096 字节） |
| 进程在但无订阅 | NATS subsz | connect 后、subscribe 前异常退出 |
| 进程在有多条 decision log 但全 NO_TRADE | decision log 聚合查询 | gate 过严 / budget 不足 |
| 有 SIGNAL 但全 expired | signals 表 status | expire_ms 太短、worker signal poller 间隔太长 |
| 心跳不更新 | strategy_status | _heartbeat task 异常、KV 写入失败 |
| 候选池空 | strategy_candidates 表 | discover() 过滤过严、BTC regime 全局拦截 |

## 7. 最常用诊断命令速查

```bash
# 一键健康检查
curl -s http://127.0.0.1:18000/api/v1/health

# 进程状态
ps aux | grep realtime_main | grep -v grep

# 进程环境
ps eww $(pgrep -f realtime_main | head -1) | tr ' ' '\n' | grep -E 'STRATEGY|NATS|DATABASE'

# 策略加载验证
cd /opt/homebrew/var/crypto-trader/current && python3 -c "
import sys; sys.path.insert(0,'strategy')
from runtime.strategy_manager import load_enabled_strategies
h,e=load_enabled_strategies(); print(f'{len(h)} handles, {len(e)} errors')
"

# 决策拒绝 Top10
psql -h localhost -U zuo -d crypto_trader -c "
SELECT reason, COUNT(*) as n FROM strategy_decision_logs
WHERE strategy_id='workflow_distilled_funnel' AND created_at > now()-interval '30 min'
GROUP BY reason ORDER BY n DESC LIMIT 10;"

# 最近信号状态
psql -h localhost -U zuo -d crypto_trader -c "
SELECT symbol, side, status, created_at FROM signals
WHERE created_at > now()-interval '1 hour' ORDER BY created_at DESC LIMIT 10;"
```
