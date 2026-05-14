# 持仓感知交易计划 —— 策略编写模式

## 问题

策略在 `build_signals_from_context()` 中生成信号时，如果不读当前持仓：
- 同向已有仓位时盲目加仓 → 过度集中
- 反向已有仓位时直接开反向 → 两边打脸
- 杠杆硬编码 → 低价币用相同杠杆，风险不对等
- 不知道账户还剩多少预算 → 超买

## 解决方案：三步持仓感知

### 第一步：读持仓

```python
position = strategy_sdk.position_snapshot(context)
# → {side, qty, entry_price, unrealized_pnl, notional, has_position, ...}
```

### 第二步：持仓冲突门控

在 `_trade_gate()` 中增加持仓检查：

```python
def _trade_gate(self, state, context, position):
    # 账户预算检查
    account_ok, reason = self._account_gate(context, position, state)
    if not account_ok:
        return False, reason

    # 持仓冲突
    if position["has_position"]:
        if position["side"] != state["side"]:
            # 反向：仅强 setup + 高分
            if state["stage"] not in STRONG_STAGES:
                return False, "opposite_position_weak_setup"
            if state["score"] < 78:
                return False, "opposite_position_score_too_low"
        else:
            # 同向：检查敞口和浮亏
            if exposure_pct >= max_exposure * 0.75:
                return False, "same_side_already_near_max_exposure"
            if unrealized_pnl < -notional * 0.03:
                return False, "do_not_add_to_losing_position"

    # 原有市场结构门控 ...
```

### 第三步：仓位预算和表单适配

```python
def _risk_budget_pct(self, context, state, position):
    budget = max(0.25, min(state["risk_pct"], remaining_symbol, remaining_total, max_order))
    if position["has_position"] and position["side"] == state["side"]:
        budget = max(0.15, budget * 0.5)  # 同向加仓减半
    return budget

def _apply_position_management(self, trade_params, state, position):
    same_side = position["has_position"] and position["side"] == state["side"]
    is_reversal = position["has_position"] and position["side"] != state["side"]
    trade_params["position_management"] = {
        "allow_add_position": same_side,
        "max_add_count": 1 if same_side else 0,
        "allow_partial_exit": take_profit["mode"] == "ladder",
        "allow_reverse_on_opposite_signal": is_reversal,
        "same_symbol_cooldown_minutes": 15 if same_side else 30,
    }
```

## 动态杠杆

不再硬编码杠杆。`basic_trade_params()` 调用 `pick_leverage()`：

```python
trade_params = strategy_sdk.basic_trade_params(
    context, side, price,
    # ... stop/TP ...
    leverage_score=state["score"],
    leverage_stage=state["stage"],
    leverage_vol_pct=abs(state.get("signed_change", 3.0)),
)
```

`pick_leverage()` 规则：
- 保守阶段（sweep/reversal/neutral）→ 最小杠杆
- 高分（score 60→95）→ 线性加分（0–25% of spread）
- 高波动（vol 2%→10%）→ 线性降分（0–35% of spread）
- 结果 clamp 在 `[min_leverage, max_leverage]`

后端要求：`strategyRiskLimits` 必须暴露 `min_leverage`/`max_leverage`，从 `risk.Limits` 读取。

## context 字段速查

| 字段 | 策略用途 |
|------|---------|
| `context.position.side` | 当前持仓方向 |
| `context.position.qty` | 持仓数量 |
| `context.position.entry_price` | 开仓均价 |
| `context.position.unrealized_pnl` | 浮动盈亏 |
| `context.position.notional` | 名义价值 |
| `context.account_fit.symbol_exposure_pct` | 该币种敞口占比 |
| `context.account_fit.remaining_symbol_budget_pct` | 该币种剩余预算 |
| `context.account_fit.remaining_total_budget_pct` | 全账户剩余预算 |
| `context.risk_limits.min_leverage` | 最小杠杆 |
| `context.risk_limits.max_leverage` | 最大杠杆 |
| `context.risk_limits.max_symbol_exposure_pct` | 单币种敞口上限 |
