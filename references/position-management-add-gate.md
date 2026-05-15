# 持仓管理：加仓门禁逻辑

`_apply_position_management()` 决定信号是否允许加仓、反手、冷却时间等。

## 加仓条件

```python
same_side = position["has_position"] and position["side"] == state["side"]
allow_add = False
max_add = 0

if same_side and state["stage"] != "neutral_probe":
    if state["score"] >= 80 and position.get("unrealized_pnl", 0) >= 0:
        allow_add = True
        max_add = 1
```

**加仓必须同时满足：**
1. 已有同向持仓
2. 非 `neutral_probe` 阶段（弱信号不加仓）
3. 评分 ≥ 80
4. 已有浮盈（PnL ≥ 0）

**禁止加仓的场景：**
- 刚开仓就出信号（无浮盈）
- 亏损加仓（下跌补仓）
- neutral_probe 弱信号加仓

## 冷却时间分层

| 场景 | 冷却时间 | 说明 |
|------|---------|------|
| 默认（无持仓新开） | 30 分钟 | 同一 symbol 重新发信号的最小间隔 |
| neutral_probe | 60 分钟 | 弱信号更长冷却 |
| 加仓开启时 | 120 分钟 | 已加仓 → 更长冷却防连续加仓 |

⚠️ **已知问题**：无可持仓 vs 已持仓的冷却区分。已持有时即使不加仓，30 分钟冷却也偏短。

## 反手逻辑

```python
is_reversal = position["has_position"] and position["side"] != state["side"]
allow_reverse = is_reversal
```

反手不做额外门槛——信号本身的 `intent` 已表达 `REVERSE_LONG` / `REVERSE_SHORT`，由 `build_signals_from_context` 中 `_maybe_close_position` 触发。

## 加仓参数传递

```python
trade_params["position_management"] = {
    "allow_add_position": allow_add,
    "max_add_count": max_add,        # 最大加仓次数
    "allow_partial_exit": is_ladder, # 阶梯止盈允许部分退出
    "allow_reverse_on_opposite_signal": allow_reverse,
    "same_symbol_cooldown_minutes": cooldown,
}
```

这些参数被序列化到 `StrategySignalEvent` 的 `position_management` 字段，由后端执行器消费。

## 加仓预算约束（策略层）

在 `_apply_risk_budget_sizing()` 中：

```python
leverage = trade_params["margin"]["leverage"]
leverage_notional_cap = remaining_symbol_cap × leverage  # 杠杆放大后的符号敞口上限
notional_caps = [desired_notional, effective_order_cap, remaining_symbol_cap,
                 remaining_total_cap, leverage_notional_cap]
desired_notional = min(notional_caps)  # 取最严格的上限
```

加仓时 `remaining_symbol_cap` 已被已有持仓消耗，自动限制加仓金额。

## 设计原则

1. **同向加仓默认关闭** — 只在强 setup + 浮盈时开启
2. **单次加仓上限 1** — 防连续加仓
3. **冷却阶梯** — 加仓后冷却更长
4. **预算自然收敛** — 剩余预算随持仓增加减少，自动缩量
