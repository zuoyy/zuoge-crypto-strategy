# 持仓管理：专业金字塔加仓门禁

## 设计哲学

**加仓是趋势交易中的金字塔建仓，不是看到信号就加。** 需要趋势延续 + 浮盈保护 + 回调入场。

旧逻辑（已废弃）：`score >= 80 + PnL >= 0` -> 加仓，冷却 120min。

## 加仓门禁：7 层 Gate（`_trade_gate`）

| # | Gate | 条件 | 拒绝 reason |
|---|------|------|-------------|
| 1 | 敞口上限 | <= max_symbol_exposure * 75% | `same_side_already_near_max_exposure` |
| 2 | 浮盈门槛 | PnL% >= 1.5% (~1R) | `add_requires_min_float_profit` |
| 3 | 亏损保护 | unrealized_pnl >= 0 | `do_not_add_to_losing_position` |
| 4 | 趋势确认 | long: 4h trend >= -0.3%; short: <= 0.3% | `add_trend_4h_against_*` |
| 5 | Book 确认 | long: directional_book >= -0.02; short: <= 0.02 | `add_book_not_supporting_*` |
| 6 | 回调入场 | long: pos_1h <= 0.75; short: pos_1h >= 0.25 | `add_no_chase_*` |
| 7 | 阶段过滤 | trend-following stages only | `add_stage_not_eligible` |

## 冷却分层

| 场景 | 冷却 |
|------|------|
| 无持仓新开 | 90 min |
| 已有持仓 | 180 min |
| 金字塔加仓 | 240 min |
| neutral_probe | 120 min |

## 加仓预算（动态 `max_add_count`）

**不写死，从 backend `strategy_account_fit.max_add_count` 动态读取。** Fallback: `open_position_slots_remaining`。

```python
add_ratio = 1.0 / (1.0 + add_slots)  # 1->50%, 2->33%, 3->25%
budget = max(0.10, budget * add_ratio)
```

真正的金字塔序列递减（50%->25%->12.5%）需 backend 在 `owned_position` 暴露 `add_count`。

## 加仓参数

`max_add_count` 从 `strategy_account_fit` 动态读取，不写死。`_risk_budget_pct` 和 `_apply_position_management` 两处均用同一数据源。
