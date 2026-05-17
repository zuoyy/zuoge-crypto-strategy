# 生产数据库直查诊断手册

当 API 不可用或需要快速定位根因时，直接查 PostgreSQL 生产库 `crypto_trader`。

## 连接

```bash
PGPASSWORD="" psql -h localhost -U zuo -d crypto_trader
```

### ⚠️ DATABASE_URL 不可见时的 fallback

生产进程的 `DATABASE_URL` 可能无法通过 `ps eww` 或 plist 发现（macOS launchd 环境隔离）。fallback 路径：

- **生产库**：`postgres://zuo:@localhost:5432/crypto_trader?sslmode=disable`
- **开发库**：`postgres://zuo:@localhost:5432/crypto_trader_dev?sslmode=disable`（`.env.dev` 中配置）

直接 `psql` 连接免认证（本地 socket），比 `psql "$DATABASE_URL"` 更可靠：
```bash
psql -h localhost -U zuo -d crypto_trader -c "SELECT ..."
```

## 诊断 SQL

### 1. 信号状态总盘

```sql
SELECT status, count(*) FROM signals GROUP BY status ORDER BY count(*) DESC;
```

### 2. 拒绝原因分布

```sql
SELECT reason_code, count(*) as cnt
FROM strategy_signal_rejects
GROUP BY reason_code ORDER BY cnt DESC LIMIT 10;
```

### 3. 最近 decision_logs（看策略是否在跑、为什么 NO_TRADE）

```sql
SELECT decision, reason, symbol,
       to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') as cst
FROM strategy_decision_logs
ORDER BY created_at DESC LIMIT 40;
```

**最危险的信号**：如果最近 40 条全是 `NO_TRADE | strategy_risk_budget_missing`，说明策略预算已满或 overlay 挂了。

### 4. 实际成交（fills）——还原真实交易时间线

```sql
SELECT execution_id, symbol, side, quantity, price, realized_pnl,
       to_char(filled_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') as cst,
       signal_id
FROM fills ORDER BY filled_at DESC LIMIT 40;
```

**注意**：`execution_id` 以 `exec_exchange_history_` 开头的是交易所历史同步记录，可能和 `exec_*` 记录重复。用 `exec_*`（不含 `exchange_history`）作为权威成交。

### 5. 某 symbol 完整时间线

```sql
-- 所有成交按时间排列
SELECT execution_id, symbol, side, quantity, realized_pnl,
       to_char(filled_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI:SS') as cst,
       signal_id
FROM fills WHERE symbol='DUSKUSDT' ORDER BY filled_at;

-- 所有信号
SELECT signal_id, symbol, side, status,
       to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') as cst
FROM signals WHERE symbol='DUSKUSDT' ORDER BY created_at;
```

### 6. 当前活跃持仓和运行时状态

```sql
SELECT symbol, position_side, runtime_status,
       to_char(entry_filled_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') as filled
FROM position_plan_runtimes WHERE runtime_status IN ('active', 'cooldown');
```

### 7. 账户分配状况（看是否满仓）

通过 Agent API（需要 API key）：

```bash
export $(grep -v '^#' /opt/homebrew/etc/crypto-trader/strategy.env | grep -v '^$' | xargs)
curl -s "$STRATEGY_CONTEXT_API_URL/api/v1/agent/strategy/context/DUSKUSDT?strategy_id=$STRATEGY_ID" \
  -H "Authorization: Bearer $AGENT_API_KEY" | python3 -m json.tool
```

关注 `strategy_account_fit.remaining_total_budget_pct` — 如果是 `"0"` 则该策略资金池已满。

## 诊断决策树

```
用户说"胜率好低/不发信号"
  ↓
1. 查 signals.status → 全是 expired？
  ├─ 是 → 查 fills 表有没有实际成交（signals expired 不一定是没执行）
  │   ├─ fills 有数据 → 查具体 symbol 的完整时间线
  │   └─ fills 为空 → 信号确实没被执行
  │       └─ 查 strategy_signal_rejects 看拒绝原因
  │           ├─ invalid_json → data_dependencies 非 RFC3339
  │           ├─ market_seq_too_old → expire_ms 不够
  │           └─ signal_validation_failed → 查具体 signal
  │
2. 查 strategy_decision_logs → 全是 NO_TRADE？
  ├─ strategy_risk_budget_missing → 查 overlay 连通性 + 策略资金池状态
  │   ├─ overlay 正常 → 策略预算已满（查 strategy_account_fit）
  │   │   ├─ remaining_total_budget_pct=0 → 策略预算死锁
  │   │   │   ├─ 查 position_plan_runtimes 看哪些持仓占着
  │   │   │   └─ 查 fills 看是否"刚开仓就加仓"导致仓位翻倍
  │   │   └─ 有 budget → 其他原因
  │   └─ overlay 连接拒绝 → 查进程 stderr（SlowConsumer？）
  │       └─ SlowConsumer 598万+ → 通配符订阅泛滥
  │           └─ 改造为按 candidate 动态订阅
  ├─ same_side_already_near_max_exposure → 同向敞口过高
  └─ 其他 gate reject → 查 strategy code

3. 查 fills 成交性能分层 ← ⭐ 最关键的诊断（新增）
```

## ⭐ 成交性能分层诊断（fills action_id 拆分法）

**这是区分「策略方向错误」和「止损太紧」的第一手诊断。** 不要只看总胜率——必须按 `action_id` 拆分成交来源。

### 核心查询：策略平仓 vs 交易所止损

```sql
-- 按 action_id 是否为空拆分成交
SELECT 
    CASE WHEN f.action_id IS NULL THEN 'STOP_LOSS(exchange)' 
         ELSE 'STRATEGY_EXIT' END as exit_type,
    COUNT(*) as cnt,
    COUNT(*) FILTER (WHERE f.realized_pnl > 0) as wins,
    COUNT(*) FILTER (WHERE f.realized_pnl < 0) as losses,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.realized_pnl > 0) / 
          NULLIF(COUNT(*) FILTER (WHERE f.realized_pnl != 0),0), 1) as win_rate,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as total_pnl,
    ROUND(AVG(CASE WHEN f.realized_pnl > 0 THEN f.realized_pnl END)::numeric, 2) as avg_win,
    ROUND(AVG(CASE WHEN f.realized_pnl < 0 THEN f.realized_pnl END)::numeric, 2) as avg_loss
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE f.filled_at > NOW() - INTERVAL '7 days'
  AND f.realized_pnl != 0
  AND s.strategy_id = 'workflow_distilled_funnel'
GROUP BY 1;
```

**解读**：
- `action_id IS NULL` = 交易所止损/强平触发，非策略主动平仓
- `action_id IS NOT NULL` = 策略通过 `signal_actions` 主动平仓
- 如果 `STOP_LOSS` 胜率 0% 但 `STRATEGY_EXIT` 胜率正常 → **止损太紧，不是方向错**
- 如果两者胜率都低 → **策略方向判断有问题**

### 止损成交明细（看止损距离）

```sql
-- 提取止损单的 entry vs stop 距离
SELECT 
    f.symbol, f.position_side,
    ROUND(f.realized_pnl::numeric, 2) as loss,
    s.payload_json->>'price_ref' as entry_ref,
    s.payload_json->'trade_params'->'exits'->'stop_loss'->>'stop_price' as stop_price,
    s.payload_json->'trade_params'->'margin'->>'leverage' as leverage,
    -- 计算止损距离百分比
    ROUND(
      ABS((s.payload_json->'trade_params'->'exits'->'stop_loss'->>'stop_price')::numeric - 
           (s.payload_json->>'price_ref')::numeric) / 
      (s.payload_json->>'price_ref')::numeric * 100, 3
    ) as stop_distance_pct,
    f.filled_at
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE f.action_id IS NULL 
  AND f.realized_pnl < 0
  AND s.strategy_id = 'workflow_distilled_funnel'
  AND f.filled_at > NOW() - INTERVAL '7 days'
ORDER BY f.realized_pnl ASC;
```

如果 `stop_distance_pct` 全部 < 1.5% 且杠杆 11-23x → **止损公式分母太大，必须修复**。

### 按交易类型拆分 action_type

```sql
-- 看 action_type 分布（了解是什么样的平仓行为）
SELECT 
    sa.action_type, sa.trigger_reason,
    COUNT(*) as cnt,
    COUNT(*) FILTER (WHERE f.realized_pnl > 0) as wins,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as total_pnl
FROM fills f
JOIN signal_actions sa ON f.action_id = sa.action_id
WHERE f.filled_at > NOW() - INTERVAL '7 days'
  AND f.realized_pnl != 0
GROUP BY sa.action_type, sa.trigger_reason
ORDER BY total_pnl DESC;
```

### 按 symbol 拆分盈亏（找亏损集中币种）

```sql
SELECT 
    f.symbol, f.position_side,
    COUNT(*) FILTER (WHERE f.realized_pnl != 0) as trades,
    COUNT(*) FILTER (WHERE f.realized_pnl > 0) as wins,
    COUNT(*) FILTER (WHERE f.realized_pnl < 0) as losses,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as total_pnl
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE f.filled_at > NOW() - INTERVAL '7 days'
  AND f.realized_pnl != 0
  AND s.strategy_id = 'workflow_distilled_funnel'
GROUP BY f.symbol, f.position_side
ORDER BY total_pnl ASC;
```

### 完整诊断 SQL（一键跑）

```sql
-- ═══════════════════════════════════════════
-- 一键诊断：胜率、止损、策略平仓分层
-- ═══════════════════════════════════════════

-- A. 总盘
SELECT 
    COUNT(*) FILTER (WHERE realized_pnl != 0) as trades,
    COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
    COUNT(*) FILTER (WHERE realized_pnl < 0) as losses,
    ROUND(SUM(realized_pnl)::numeric, 2) as total_pnl
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE s.strategy_id = 'workflow_distilled_funnel'
  AND f.filled_at > NOW() - INTERVAL '7 days';

-- B. action_id 分层（止损 vs 策略平仓）
SELECT 
    CASE WHEN f.action_id IS NULL THEN 'STOP_LOSS' ELSE 'STRATEGY' END as src,
    COUNT(*) FILTER (WHERE f.realized_pnl != 0) as trades,
    COUNT(*) FILTER (WHERE f.realized_pnl > 0) as wins,
    COUNT(*) FILTER (WHERE f.realized_pnl < 0) as losses,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as total_pnl
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE s.strategy_id = 'workflow_distilled_funnel'
  AND f.filled_at > NOW() - INTERVAL '7 days'
  AND f.realized_pnl != 0
GROUP BY 1;

-- C. 按币种
SELECT f.symbol, f.position_side,
    COUNT(*) FILTER (WHERE f.realized_pnl != 0) as trades,
    COUNT(*) FILTER (WHERE f.realized_pnl > 0) as wins,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as total_pnl
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE s.strategy_id = 'workflow_distilled_funnel'
  AND f.filled_at > NOW() - INTERVAL '7 days'
  AND f.realized_pnl != 0
GROUP BY f.symbol, f.position_side
ORDER BY total_pnl ASC
LIMIT 15;
```

## 进程级诊断

### SlowConsumer 检查

```bash
grep -c 'SlowConsumer' /opt/homebrew/var/crypto-trader/logs/realtime-strategy.stderr.log
```

如果 > 100万，策略的事件循环已经被 NATS 消息洪水冲垮。

### overlay 连通性检查

```bash
grep 'strategy_context_overlay_failed' /opt/homebrew/var/crypto-trader/logs/realtime-strategy.stdout.log | tail -5
```

### 手动测试 overlay API

```bash
export $(grep -v '^#' /opt/homebrew/etc/crypto-trader/strategy.env | grep -v '^$' | xargs)
curl -s "$STRATEGY_CONTEXT_API_URL/api/v1/agent/strategy/context/GWEIUSDT?strategy_id=$STRATEGY_ID" \
  -H "Authorization: Bearer $AGENT_API_KEY" | python3 -c "import json,sys; d=json.load(sys.stdin); item=d.get('item',d); print('strategy_account_fit:', item.get('strategy_account_fit',{}))"
```
