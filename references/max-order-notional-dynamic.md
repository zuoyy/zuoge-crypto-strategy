# 动态下单金额封顶

策略不再硬编码 `max_order_notional` 百分比，改为动态读取后端配置。

## 代码

```python
# _apply_risk_budget_sizing() 中
risk_limits = context.get("risk_limits") or {}
max_order_pct = strategy_sdk.number(
    risk_limits.get("max_order_notional_pct"), 40  # ⚠️ 这 40 只是 fallback 默认值，不是后端实际配置
) / 100.0
desired_notional = min(desired_notional, allocated_equity × max_order_pct)
```

## 🔴 陷阱：不要假设 fallback 值

代码里 `, 40` 是 fallback 默认值——只在后端没返回该字段时生效。**实际值必须从数据库查询，不要用 40 去估算。**

```sql
-- 查实际后端配置
SELECT strategy_id, max_order_notional_pct FROM strategy_risk_allocations;
```

生产实测 `workflow_distilled_funnel` 的后端配置是 **100%**（不是 40%）。

## 效果

| 后端配置 | max_order_pct | 单笔 notional 上限（equity=$5,000） |
|----------|--------------|-----------------------------------|
| 40%（代码 fallback） | 0.40 | $2,000 |
| 100%（生产实际） | 1.00 | $5,000 |

## 两层预算的区别

| 参数 | 位置 | 作用 | 例子 |
|------|------|------|------|
| `max_order_notional_pct` | 后端 DB | 名义金额天花板 | 100% → $5000 |
| `risk_pct` | 策略代码 | 风险预算（净值%） | 2.5% → $125 risk |

`desired_notional = target_risk / stop_pct` 先算出理想仓位，再用 `max_order_notional_pct` 兜底封顶。如果公式算出来的值本来就没碰到天花板，改 `max_order_notional_pct` 不会影响仓位。

## 后端修改方式

```sql
UPDATE strategy_risk_allocations SET max_order_notional_pct = 60
WHERE strategy_id = 'workflow_distilled_funnel';
```

修改后 strategy 下次 `build_signals_from_context()` 调用自动读取新值，无需重启策略进程（context overlay 缓存 TTL 已处理）。

⚠️ 策略进程的本地位数缓存（`_apply_risk_budget_sizing` 中 `remaining_symbol_cap` 等）来自 `context.strategy_account_fit`，由 API overlay 提供，有独立 TTL（默认 2 秒 strategy context TTL）。

## 历史

- 最初：硬编码 `0.30`（30%）→ 用户反馈太小
- 改为：动态读 `risk_limits.max_order_notional_pct`，默认 40%
- 当前：后端配置 100%，策略自动跟随
