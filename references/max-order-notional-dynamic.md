# 动态下单金额封顶

策略不再硬编码 `max_order_notional` 百分比，改为动态读取后端配置。

## 代码

```python
# _apply_risk_budget_sizing() 中
risk_limits = context.get("risk_limits") or {}
max_order_pct = strategy_sdk.number(
    risk_limits.get("max_order_notional_pct"), 40  # 默认 40%
) / 100.0
desired_notional = min(desired_notional, allocated_equity × max_order_pct)
```

## 效果

| 后端配置 | max_order_pct | 单笔 notional 上限（equity=$5,000） |
|----------|--------------|-----------------------------------|
| 40%（旧默认） | 0.40 | $2,000 |
| 60%（当前） | 0.60 | $3,000 |
| 任意调整 | 自动跟随 | 自动适配 |

## 后端修改方式

```sql
UPDATE risk_limit_configs SET max_order_notional_pct = 60
WHERE config_id = (SELECT MAX(config_id) FROM risk_limit_configs);
```

修改后 strategy 下次 `build_signals_from_context()` 调用自动读取新值，无需重启策略进程（context overlay 缓存 TTL 已处理）。

⚠️ 策略进程的本地位数缓存（`_apply_risk_budget_sizing` 中 `remaining_symbol_cap` 等）来自 `context.strategy_account_fit`，由 API overlay 提供，有独立 TTL（默认 2 秒 strategy context TTL）。

## 历史

- 最初：硬编码 `0.30`（30%）→ 用户反馈太小
- 改为：动态读 `risk_limits.max_order_notional_pct`，默认 40%
- 当前：后端配置 60%，策略自动跟随
