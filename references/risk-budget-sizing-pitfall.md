# risk_budget 模式下 max_notional 瓶颈

## 症状

策略切到 `sizing.mode = "risk_budget"` 后，实际下单名义金额极小（如 $50），与预期杠杆化仓位（如 $4,000+）严重不符。用户反馈“下单数量好小”。

## 根因

`computeSizing()` 在 Go 后端 `/internal/risk/service.go:374` 的执行顺序：

```go
case signal.SizingModeRiskBudget:
    // 1. 用 risk_budget 公式算出 notional
    notional = TargetRiskAmount / (|entry - stop| / entry)
    //   例: $50.17 / 0.012 = $4,180 ✅

// 2. 然后用 max_notional 做硬上限！
if proposal.TradeParams.Sizing.MaxNotional.IsPositive() &&
   notional.GreaterThan(proposal.TradeParams.Sizing.MaxNotional) {
    notional = proposal.TradeParams.Sizing.MaxNotional
    //   例: $4,180 > $50.17 → capped at $50.17 ❌
}
```

**根源**：`basic_trade_params()` 的 `max_notional` 是按**现金口径**计算的：

```python
notional = max(equity * budget_pct / 100.0, min_notional)  # $50.17
```

这个值在 `target_notional` 模式下合理（不开杠杆），但切到 `risk_budget` 后，Go 端算出百倍的杠杆化 notional，又被这个 $50 压回去。

## 修复（2026-05-15 最终版）

**根因修复**：`risk_pct` 应从"名义金额占比"改为"风险金额占比"，修复在 `_apply_risk_budget_sizing()`：

```python
# 修复前（错误）
desired_notional = allocated_equity * risk_pct / 100.0    # $50 ← risk_pct 当 notional 用

# 修复后（正确）
target_risk_amount = allocated_equity * risk_pct / 100.0   # $50 = 1% 风险
desired_notional = target_risk_amount / stop_pct            # $50 / 0.02 = $2,500
```

`desired_notional` 变大了 50 倍，后续 `max_notional = min(caps)` 由 `effective_order_cap`（~$1,500）主导，不再是 $50 瓶颈。

```python
def _apply_risk_budget_sizing(self, trade_params, context, state, risk_pct):
    strategy_fit = context.get("strategy_account_fit") or {}
    equity = number(strategy_fit.get("account_equity"), 1000.0)
    risk_amount = equity * risk_pct / 100.0
    sizing = trade_params.get("sizing") or {}
    sizing["mode"] = "risk_budget"
    sizing["target_risk_amount"] = fmt(risk_amount)

    # ── 关键：重新算 max_notional ──
    leverage = float(trade_params["margin"]["leverage"])
    exposure_pct = number(risk_limits.get("max_symbol_exposure_pct"), 50.0)
    stop_pct = max(state["stop_pct"], 0.003)
    risk_budget_notional = risk_amount / stop_pct       # $4,180
    leverage_cap = equity * leverage * exposure_pct / 100.0  # $25,085
    max_notional = min(risk_budget_notional * 1.3, leverage_cap)

    sizing["target_notional"] = fmt(risk_budget_notional)
    sizing["max_notional"] = fmt(max_notional)  # ← 不再是 $50
    sizing.pop("risk_pct", None)
    sizing.pop("stop_pct", None)
    trade_params["sizing"] = sizing
```

### 修复后效果

| 参数 | 修复前 | 修复后 |
|------|--------|--------|
| `target_risk_amount` | $50.17 | $50.17 |
| Go 端 notional | $50.17 → capped by max_notional | $4,180 → 通过 |
| `max_notional` | $50.17 (现金) | $5,435 (杠杆感知) |
| 杠杆 12x 保证金 | 微不足道 | $334 (7% of $5K) |

## 排查链路

当用户说“下单数量太小”时，不要只查策略代码，要追踪到 Go 后端的 `computeSizing()`：

1. 确认 `sizing.mode` 是 `risk_budget`
2. 确认 `target_risk_amount > 0`
3. 确认 `max_notional` 没有被设成现金口径的值
4. 若 `max_notional ≈ target_risk_amount`（ratio ≈ 1.0x），则是本坑

验证方法：本地模拟策略跑一遍，看 `max_notional / target_risk_amount` 的比例。正常应该是 **> 50x**。

## 相关文件

- `strategy/runtime/strategy_sdk.py` — `basic_trade_params()` 算 notional
- `internal/risk/service.go:374-426` — Go 端 `computeSizing()` 和 `max_notional` 硬上限
- `internal/api/strategy_context.go:96-101` — `strategyRiskLimits` 暴露 `min/max_leverage`
