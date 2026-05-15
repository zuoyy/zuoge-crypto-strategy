# risk_budget 尺寸公式倒置陷阱

## 现象

策略下单后持仓极小。$5,000 账户，每笔 notional 仅 $13–$50，实际风险 $1（0.02% 账户）。

## 根因

`_apply_risk_budget_sizing()` 中 `risk_pct` 被直接乘到 **notional** 上，而非风险金额：

```python
# ❌ 当前（错误）
desired_notional = equity × risk_pct / 100    # $5000 × 1% = $50
risk_amount      = max_notional × stop_pct     # $50 × 0.02 = $1
```

risk_budget 模式的正确语义应该是「risk_pct 比例的 equity 作为风险金额」：

```python
# ✅ 正确
target_risk_amount = equity × risk_pct / 100   # $5000 × 1% = $50 风险
desired_notional   = target_risk_amount / stop_pct  # $50 / 0.02 = $2,500
```

## 验证

2026-05-15 实盘验证（策略 `workflow_distilled_funnel 0.1.0`）：

| 币种 | 当前 notional | 应得 notional（$50 风险 / 2% stop） |
|------|-------------|-----------------------------------|
| FHEUSDT | $49 | $2,500 |
| GOATUSDT | $33 | $2,500 |
| XANUSDT | $13 | $2,500 |

## 修复位置

`_apply_risk_budget_sizing()` 中 `desired_notional` 的计算行。

## 相关

- [risk_budget sizing 瓶颈](risk-budget-sizing-pitfall.md)——`max_notional` 硬上限的另一面
