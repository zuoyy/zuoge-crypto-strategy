# 动态杠杆计算：pick_leverage()

杠杆不是固定值，由 `strategy_sdk.pick_leverage()` 根据上下文动态计算。

## 公式

```python
min_lev = risk_limits.min_leverage   # 默认 5
max_lev = risk_limits.max_leverage   # 默认 20
spread  = max_lev - min_lev

# 保守阶段 → 直接用最小杠杆
if stage in ("sweep_reclaim", "low_reversal_long", "high_reversal_short", "neutral_probe"):
    return min_lev

# 其他阶段
base         = min_lev + spread × 0.50         # 5 + 15×0.5 = 12.5
score_boost  = clamp((score - 60) / 35, 0, 0.25) × spread  # 0 ～ 3.75
vol_discount = clamp((volatility - 2) / 8, 0, 0.35) × spread  # 0 ～ 5.25

result = base + score_boost - vol_discount
return round(result)  # clamped to [min_lev, max_lev]
```

## 典型值

| 阶段 | score | 波动率 | 杠杆 | 说明 |
|------|-------|--------|------|------|
| neutral_probe | 任意 | 任意 | **5** | 保守阶段固定 min |
| trend_pressure_build | 72 | 8% | ~10 | 低分 + 中波动 |
| trend_pressure_build | 85 | 3% | ~14 | 高分 + 低波动 |
| trend_pressure_build | 90 | 10% | ~11 | 高分但高波抵消 |

## 数据库配置

```sql
SELECT min_leverage, max_leverage FROM risk_limit_configs ORDER BY config_id DESC LIMIT 1;
-- 默认: min=5, max=20
```

修改后端 `min_leverage` / `max_leverage` 后策略自动跟随，无需改代码。

## 调用链

```
build_signals_from_context()
  → strategy_sdk.basic_trade_params(leverage_score=score, leverage_stage=stage, leverage_vol_pct=abs(signed_change))
    → pick_leverage(context, volatility_pct=..., score=..., stage=...)
      → margin: {leverage: str(int(final_leverage))}
  → _apply_risk_budget_sizing()
    → leverage = trade_params["margin"]["leverage"]  # 读取 basic_trade_params 计算值
    → leverage_notional_cap = remaining_symbol_cap × leverage  # 杠杆倍数封顶
```

stage 名称来自 `_evaluate_context()` 的 `state["stage"]` 字段。
