# 持仓管理：专业金字塔加仓门禁

## 设计哲学

**加仓是趋势交易中的金字塔建仓，不是看到信号就加。** 需要趋势延续 + 浮盈保护 + 回调入场三个条件同时满足。

旧逻辑（已废弃）：`score ≥ 80 + PnL ≥ 0` → 加仓。问题：
- 不区分浮盈大小，PnL=0 也加
- 不检查趋势是否延续
- 不检查 book 压力是否还在
- 不要求回调入场（追高加仓）
- 冷却 120 分钟，已持仓非加仓场景只有 30 分钟

## 加仓门禁：7 层 Gate（`_trade_gate`）

所有同向持仓信号在进入 `_apply_position_management` 之前，必须在 `_trade_gate` 中通过全部 7 层检查：

| # | Gate | 条件 | 拒绝 reason |
|---|------|------|-------------|
| 1 | 敞口上限 | 加仓后 ≤ max_symbol_exposure × 75% | `same_side_already_near_max_exposure` |
| 2 | 浮盈门槛 | PnL% ≥ 1.5%（≈1R，stop 通常 1.2-1.5%） | `add_requires_min_float_profit` |
| 3 | 亏损保护 | unrealized_pnl ≥ 0 | `do_not_add_to_losing_position` |
| 4 | 趋势确认 | long → 4h trend ≥ -0.3%; short → 4h trend ≤ 0.3% | `add_trend_4h_against_*` |
| 5 | Book 确认 | long → directional_book ≥ -0.02; short → ≤ 0.02 | `add_book_not_supporting_*` |
| 6 | 回调入场 | long → pos_1h ≤ 0.75（非超买）; short → pos_1h ≥ 0.25（非超卖） | `add_no_chase_*` |
| 7 | 阶段过滤 | 仅 trend-following 阶段：`accepted_breakout`, `expansion_continuation`, `pullback_reaccept`, `trend_pressure_build` | `add_stage_not_eligible` |

> Gate 在 `_trade_gate` 中执行，早于 `_apply_position_management`。如果 gate 拒绝，信号以 `NO_TRADE` 终止，不会到达持仓管理阶段。

## 冷却分层（`_apply_position_management`）

| 场景 | 冷却时间 | 说明 |
|------|---------|------|
| 无持仓新开 | **90 min** | 防同 symbol 反复开仓 |
| 已有持仓（不加仓） | **180 min** | 持仓中的币种更长冷却，避免过度交易 |
| 金字塔加仓 | **240 min** | 加仓后最长冷却，让加仓完全展开 |
| neutral_probe | **120 min** | 弱信号统一长冷却 |

## 加仓预算

`_risk_budget_pct()` 中已有预算减半：

```python
if position["has_position"] and position["side"] == state["side"]:
    budget = max(0.15, budget * 0.5)
```

加仓金额自动为首次开仓的 50%，同时受 `remaining_symbol_budget` 自然收敛约束。

## 加仓参数传递

```python
allow_add = False
max_add = 0
if same_side:
    if state["score"] >= 78 and position.get("unrealized_pnl", 0) > 0:
        allow_add = True   # gate 已过，此处仅防御性双检
        max_add = 1

trade_params["position_management"] = {
    "allow_add_position": allow_add,
    "max_add_count": max_add,
    "allow_partial_exit": is_ladder,
    "allow_reverse_on_opposite_signal": is_reversal,
    "same_symbol_cooldown_minutes": cooldown,
}
```

## 反手逻辑

反手不在 `_apply_position_management` 中做额外门槛——信号 intent 由 `_intent_for_owned_position()` 生成 `REVERSE_LONG` / `REVERSE_SHORT`，由 `_maybe_close_position` + 反向 candidate 联合触发。
