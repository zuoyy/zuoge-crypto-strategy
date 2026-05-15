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

## 修复（两次迭代）

### 第一轮：公式倒置修复

`_apply_risk_budget_sizing()` 中 `risk_pct` 从 notional % 改为 risk %：

```python
# ❌ 修复前
desired_notional = allocated_equity * risk_pct / 100.0    # $50

# ✅ 修复后
target_risk_amount = allocated_equity * risk_pct / 100.0   # $50 = 1% 风险
desired_notional = target_risk_amount / stop_pct            # $50 / 0.02 = $2,556
```

### 第二轮：20% 净值封顶

公式倒置后 `desired_notional` 可能过大（$2,556），超过 Go 后端单笔预算被拒（"下单金额超过当前策略单笔预算"）。加 20% 净值硬上限：

```python
max_notional_cap_pct = 0.20  # single order ≤ 20% of equity
desired_notional = min(desired_notional, allocated_equity * max_notional_cap_pct)
# $2,556 → capped at $5,113 × 0.20 = $1,022
```

最终链路（risk_pct=1%，stop=2%，equity=$5,113）：
```
target_risk = $51 → / 0.02 = $2,556 → cap 20% = $1,022
max_notional = min($1,022, $2,045, …) = $1,022  ✅
```

## 相关

- [risk_budget sizing 瓶颈](risk-budget-sizing-pitfall.md)——`max_notional` 硬上限的另一面
