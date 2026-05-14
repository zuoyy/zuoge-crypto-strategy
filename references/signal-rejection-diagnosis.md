# 信号推送被拒诊断参考

诊断日期：2026-05-14。策略 `workflow_distilled_funnel` 产生的 286 条信号 100% 被拒。

## 信号链路全貌

```
strategy.build_signals_from_context()
    → signal_envelope() [strategy_sdk.py]
    → _validate_signal() [realtime_main.py:320]
    → publish_signal_async() [Core NATS, subject: strategy.signals]
    → NATS JetStream STRATEGY_SIGNALS [subjects: strategy.signals, strategy.signals.dead]
    → Go strategyingress.Service.Run() [PullSubscribe, consumer: strategy-ingress]
    → json.Unmarshal → StrategySignalEvent
    → proposalFromEvent() → validateAgainstCurrentMarket()
    → signalService.SubmitSignalProposal()
    → risk → execution (if passed)
    → 失败: saveReject() + publishDead() → strategy.signals.dead
```

## 关键环境变量（worker 进程）

```
NATS_URL=nats://127.0.0.1:4222
DATABASE_URL=postgres://zuo:@localhost:5432/crypto_trader  ← production 库
NATS_STRATEGY_SIGNALS_STREAM=STRATEGY_SIGNALS
NATS_STRATEGY_INGRESS_CONSUMER=strategy-ingress
```

与 dev 环境（`.env.dev`）的区别：
- NATS port: 4222（prod）vs 4223（dev）
- Database: `crypto_trader`（prod）vs `crypto_trader_dev`（dev）

## NATS 诊断命令速查

```bash
# 查看所有 stream 及其消息数
nats stream ls

# 查看 STRATEGY_SIGNALS stream 中两个 subject 的分布
nats stream subjects STRATEGY_SIGNALS

# 查看 consumer 消费进度
nats consumer info STRATEGY_SIGNALS strategy-ingress

# 关键字段解读：
#   Consumer sequence: 已消费并 ack 的消息数
#   Unprocessed: 匹配 filter 但未投递的消息数
#   Outstanding Acks: 已投递但未 ack/nak 的消息数
#   Waiting Pulls: 活跃的 pull 请求数（1 = 正常等待中）
#   Last Delivered: 最后一次投递时间
```

## 数据库查询

```sql
-- 查所有 reject 按原因分组
SELECT reason_code, COUNT(*) as cnt
FROM strategy_signal_rejects
GROUP BY reason_code ORDER BY cnt DESC;

-- 查最近 reject 详情
SELECT reason_code, reason, signal_id, 
       payload_json->>'symbol' as symbol,
       payload_json->>'data_dependencies' as dd,
       rejected_at
FROM strategy_signal_rejects
ORDER BY rejected_at DESC LIMIT 5;

-- 确认 signals 表是否有入库
SELECT COUNT(*) FROM signals WHERE strategy_id = 'workflow_distilled_funnel';
```

## 实际发现（2026-05-14）

总 reject: 291 条

| reason_code | 数量 | 占比 |
|---|---|---|
| invalid_json | 192 | 66% |
| signal_validation_failed | 81 | 28% |
| market_seq_too_old | 17 | 6% |
| price_deviation_exceeded | 1 | <1% |

## `data_dependencies` 格式问题详细

### Go 期望格式

```go
type StrategySignalEvent struct {
    DataDependencies map[string]time.Time `json:"data_dependencies,omitempty"`
}
```

`time.Time` 在 JSON 反序列化时要求 RFC3339 格式字符串。

### overlay 成功时的正确输出

```json
"data_dependencies": {
    "kline": "2026-05-14T05:48:04.427959Z",
    "l1_book": "2026-05-14T05:48:04.264959Z",
    "mark_price": "2026-05-14T05:48:03.876959Z",
    "l2_book_top": "2026-05-14T05:48:04.339959Z"
}
```

来源：API `/api/v1/agent/strategy/context/{symbol}` 返回的 `data_dependencies[dep].source_time`。

### overlay 失败时的错误输出

```json
"data_dependencies": {
    "kline": "74",
    "l1_book": "29",
    "mark_price": "385",
    "ticker_24h": "708",
    "l2_book_top": "37"
}
```

来源：疑似 `snapshot.freshness_ms` 的整数值被错误填入。overlay 失败时 context 不应包含 `data_dependencies`，但实际信号中却出现了这些整数值。

### 修复建议

`strategy_sdk.py` 的 `data_dependency_times()` 应加入防御：
- 只输出能解析为 `time.Time` 的有效 RFC3339 字符串
- 无法获取有效时间戳时返回 `{}`
- 或在 `signal_envelope()` 中增加后处理校验

## 策略注册问题

即使 JSON 格式正确，`signal_validation_failed` 拒绝原因：
`validation failed for skill_name: is not registered`

策略 `workflow_distilled_funnel` 的 `strategy_id` 需要在生产 strategy registry 中注册。
注册后信号才能通过 `SubmitSignalProposal` 的 skill 校验。

## 策略注册 — Settings 刷新机制

修改 `app_setting_configs.allowed_signal_skills` 后，运行时不会立即生效。
需发布 NATS 通知触发生效：

```bash
nats pub settings.changed '{"version":1}'
```

或者重启 worker 进程（KeepAlive 模式下 `kill` 自动重启）。

## `stop_price=0` — 超低价币量化归零

### 症状

```json
"stop_loss": {"mode": "price", "stop_price": "0"}
"take_profit": {"mode": "ladder", "targets": [{"price": "0", ...}, {"price": "0", ...}]}
```

Go 校验规则：`!stopLoss.StopPrice.IsPositive()` → reject `stop_price is required when stop loss mode is price`

### 根因

超低价币（如 JCTUSDT price≈0.004）的 tick_size（如 0.01）大于价格本身时，
`quantize_price(price * 0.99) → (0.004 * 0.99) / 0.01 → 0.396 → ROUND_DOWN → 0`。

### 修复

SDK 新增 `_quantized_or_fallback(value, fallback, context)`：
- 先对 value 做量化
- 若结果 ≤0（量化归零），使用 fallback 的未量化原始值
- 确保 `stop_price`、`tp1`、`tp2` 永远 >0

见 `strategy_sdk.py` 中 `basic_trade_params()` 的 long/short 分支。更完整的量化陷阱分析见 [references/price-quantization-pitfalls.md](references/price-quantization-pitfalls.md)。

## `expire_ms` 建议

- 原默认 15s — 在 NATS 投递 + ingress 消费 + 校验链路中经常不够
- 改为 **60s**（`signal_envelope(expire_ms=60000)`，SDK 默认值也改为 60000）
- 可进一步降低 `market_seq_too_old` 和 `signal_expired` 两类 reject

## 手动部署（sudo 不可用时）

`crypto-skill strategy deploy-current` 需要 sudo 操作 launchd 服务。sudo 不可用时：

```bash
# 1. 复制修改的文件到当前 release
cp strategy/runtime/strategy_sdk.py /opt/homebrew/var/crypto-trader/current/strategy/runtime/

# 2. 确认 WorkingDirectory 指向 current 且 KeepAlive=true
#    kill 后 launchd 会自动重启
kill $(pgrep -f realtime_main | head -1)

# 3. 等待重启验证
sleep 3 && pgrep -f realtime_main
```

## 风控层拒绝：`min_leverage` — 杠杆低于系统最小允许值

### 症状

风险决策表 `risk_decisions` 中 `reasons_json` 包含：
```json
{"message": "请求杠杆低于系统最小允许值", "rule_id": "min_leverage"}
```

### 根因

`basic_trade_params()` 硬编码 `leverage: "1"`。系统风险配置表 `risk_limit_configs` 的 `min_leverage` 通常设置为 5x（Binance futures 实际要求）。

Go 风控检查（`internal/risk/service.go`）：
```go
requestedLeverage := proposal.EffectiveLeverage(e.config.MinLeverage)
if requestedLeverage.LessThan(e.config.MinLeverage) {
    reasons = append(reasons, Reason{RuleID: "min_leverage", ...})
}
```

`EffectiveLeverage()` 逻辑：
```go
func (p Proposal) EffectiveLeverage(defaultLeverage decimal.Decimal) decimal.Decimal {
    if leverage := p.RequestedLeverage(); leverage.IsPositive() {
        return leverage  // 信号有 leverage → 直接用信号值
    }
    return defaultLeverage  // 信号无 leverage → 用系统默认
}
```

信号有 leverage="1" → 直接用 1 → 1 < 5 → 拒绝。

### 修复

```python
# 读取 context 中可能暴露的 min_leverage，fallback 5x
leverage = str(max(number(risk_limits.get("min_leverage"), 0.0), 5.0))
```

### 修复（已实施）

SDK `basic_trade_params()` 调用 `pick_leverage(context, *, volatility_pct, score, stage)` 在 `[min_leverage, max_leverage]` 范围内按币种动态选杠杆（保守阶段用 min，高分加杠杆，高波动降杠杆）。杠杆必须为整数（Binance 要求）。

后端 `strategy_context.go` 的 `strategyRiskLimits` 已新增 `min_leverage`/`max_leverage` 字段，从 `risk.Limits` 读取。验证：
```
risk_limits: {"min_leverage": "5", "max_leverage": "20", ...}
```

详见 [references/position-aware-trading-plan.md](references/position-aware-trading-plan.md)。

## 风控层拒绝：`min_reward_risk` — 盈亏比低于执行门槛

### 计算逻辑

Go 风控检查（`internal/risk/service.go`）：
```go
if proposal.RewardRiskRatio(entryPrice).LessThan(
    proposal.TradeParams.ExecutionConstraints.MinRewardRisk) {
    reasons = append(reasons, Reason{RuleID: "min_reward_risk", ...})
}
```

使用 **第一个 TP 目标** 计算 reward:risk，与 `execution_constraints.min_reward_risk` 比较。

### 量化如何破坏盈亏比（三重合击）

**击 1 — TP 距离不匹配**：`basic_trade_params` 原用 `stop_distance * 1.5` 算 tp1，但 `min_reward_risk` 常设为 2.2 → tp1 仅在 1.5R 处，不满足约束。

**击 2 — 止损量化被推远**：粗 tick_size 把止损从公式距离推远数倍。公式 `stop_distance = price * stop_pct = 0.0029`，量化后止损移到 0.12（实际风险 0.0128，扩大了 4.4 倍）。TP 仍按公式距离算 → 盈亏比崩塌。

**击 3 — 止盈量化被吃掉**：即使 TP 按实际风险算，粗 tick_size ROUND_DOWN 又吃掉关键比例。KITEUSDT 示例：tp1 从 0.247 量化到 0.24，ratio 从 2.2 掉到 1.53。

### 修复

SDK `basic_trade_params()` 三步修复：

1. **tp1_ratio**：`tp1_ratio = max(reward_risk, 1.5)` — tp1 至少满足 min_reward_risk
2. **actual_risk**：`actual_risk = max(price - stop_price, stop_distance)` — 用量化后真实止损距离
3. **min_value 保护**：`_quantized_or_fallback(value, fallback, ..., min_value=tp1_min)` — 量化后不满足最小值时回退到未量化值

```python
actual_risk = max(price - stop_price, stop_distance)
tp1_min = price + actual_risk * tp1_ratio
tp1 = _quantized_or_fallback(
    price + actual_risk * tp1_ratio,
    price * (1.0 + tp1_ratio * stop_pct),
    context,
    min_above=price,
    min_value=tp1_min,  # 防止量化吃掉关键比例
)
```

## 风控决策表查询

```sql
-- 风险决策通过/拒绝
SELECT passed, COUNT(*) FROM risk_decisions GROUP BY passed;

-- 拒绝原因分布
SELECT reason->>'rule_id' as rule, COUNT(*) as cnt
FROM risk_decisions, jsonb_array_elements(reasons_json) as reason
WHERE passed = false
GROUP BY rule ORDER BY cnt DESC;

-- 关联 signal 详情
SELECT s.signal_id, s.payload_json->>'symbol' as sym,
       rd.reasons_json::text
FROM risk_decisions rd
JOIN signals s ON s.signal_id = rd.signal_id
WHERE rd.passed = false
ORDER BY rd.created_at DESC LIMIT 5;
```
