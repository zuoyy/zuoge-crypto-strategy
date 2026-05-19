# 小账户仓位太小（notional flooring issue）

**诊断日期**: 2026-05-19
**症状**: $102 账户开仓只拿到 $10 notional 仓位，远小于预期 $97。

## 根因

`basic_trade_params()` 的 sizing 公式用错了：

```python
# 当前公式（有问题）:
budget_pct = min(risk_pct, remaining_symbol, remaining_total, max_order)  # ~4%
notional = max(equity * budget_pct / 100.0, min_notional)  # max($4.08, $10) = $10
```

对小账户（$102），`equity × budget_pct = $4.08` 低于交易所 `min_notional = $10`，notional 被硬地板钉死在 $10。

但正确的公式应该是**风险逆推**：
```python
# 正确公式（风险预算/止损）:
risk_amount = equity * risk_pct / 100.0  # $4.08
notional = max(risk_amount / stop_pct, min_notional)  # max($4.08/0.0422, $10) = $97
```

| 项 | 当前（错误） | 修正后 |
|------|-------------|-------|
| risk_pct | 4% | 4% |
| risk_amount | $4.08 | $4.08 |
| stop_pct | 4.22% | 4.22% |
| notional | max($4.08, $10) = **$10** | max($4.08/0.0422, $10) = **$97** |
| 实际风险 | $0.42 (0.4% equity) | $4.08 (4% equity) |

## `_apply_signal_form_contract()` 的连锁问题

当前：
```python
target_notional = 10  # 从 basic_trade_params 来的地板值
stop_pct = 0.0422
target_risk_amount = max(10 * 0.0422, 0.01) = $0.42  # 只有 $0.42 风险!
```

修正后应该是：
```python
# 用正确的 notional 重新计算 risk
target_notional = 97
target_risk_amount = max(97 * 0.0422, 0.01) = $4.08
```

## 修改位置

`strategy_sdk.py` 中 `basic_trade_params()` 函数，第 359-360 行附近。

## 诊断 SQL

```sql
-- 查信号中的 sizing 数据
SELECT 
    payload_json->'trade_params'->'sizing'->>'target_notional' as target_notional,
    payload_json->'trade_params'->'sizing'->>'target_risk_amount' as risk_amount,
    payload_json->'trade_params'->'sizing'->>'max_notional' as max_notional,
    payload_json->'price_ref' as entry,
    payload_json->'trade_params'->'exits'->'stop_loss'->>'stop_price' as stop
FROM signals
WHERE strategy_id = 'workflow_distilled_funnel'
ORDER BY created_at DESC LIMIT 1;
```

## 小账户参数参考

$100 级别账户的预期仓位：

| 账户 | risk_pct | stop_pct | 正确 notional | 地板值 |
|------|---------|---------|-------------|-------|
| $100 | 4% | 7.5% (deep_reversal) | $53 | $10 (错误) |
| $100 | 4% | 4% (breakout) | $100 | $10 (错误) |
| $500 | 4% | 4% | $500 | $20 |
| $1000 | 4% | 4% | $1000 | $40 |
