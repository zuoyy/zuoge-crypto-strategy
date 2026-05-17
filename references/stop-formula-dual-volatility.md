# 止损宽度双源波动率

## 问题

旧公式只用一个波动率源：

```python
volatility_proxy = max(abs(change), 1.0)  # change = 24h涨跌幅
stop_pct = clamp(0.009 + volatility_proxy / 900.0, 0.009, 0.022)
```

**缺陷**：24h change 是滞后指标。一个币 23 小时前涨了 10%，现在横盘，止损仍然放得很宽（1.38%），实际没必要。反之，一个币 24h 只涨 1% 但最近 1h 剧烈波动 ±5%，止损却太紧（0.9%），容易被扫。

## 方案：双源波动率

```python
# 1h trend 捕获近期动量
trend_1h_pct = abs(structure.get("trend_1h", 0.0) * 100.0)
# 取 24h 和 1h 的最大值
volatility_proxy = max(abs(change), trend_1h_pct, 1.5)
# 略微调宽系数和范围
stop_pct = clamp(0.007 + volatility_proxy / 850.0, 0.007, 0.025)
```

**效果**：

| 场景 | change | trend_1h% | 旧 stop | 新 stop |
|------|--------|-----------|---------|---------|
| 横盘币（昨天涨过但已冷） | 8% | 0.5% | 1.37% | 0.95% ✅ 更紧 |
| 突爆币（24h 不大但最近猛） | 2% | 6% | 1.0% | 1.41% ✅ 更宽 |
| 正常波动 | 5% | 3% | 1.28% | 1.25% 基本一致 |
| 极端波动 | 20% | 15% | 2.2%→clamp | 2.5%→clamp |

## 参数选择

- `clamp(0.007, 0.025)` — floor 从 0.009 降到 0.007（流动性好的币可以更紧），ceiling 从 0.022 提到 0.025（高波动币给更多呼吸空间）
- `/850.0` — 比旧的 `/900.0` 略激进，让 stop 更接近真实波动率
- `max(..., 1.5)` — 极稳定币至少 1.5% 的代理波动率

## ⚠️ 分母校准陷阱（2026-05-16 生产验证）

### 现象

`workflow_distilled_funnel` 0.1.0 生产运行 7 天：
- 交易所止损触发（`fills.action_id IS NULL`）：**14笔全亏，胜率 0%**，净亏 -$355
- 策略主动平仓（`fills.action_id IS NOT NULL`）：**7W/7L，胜率 50%**，净盈 +$188

策略方向是对的，但止损全被扫掉。

### 根因：分母 850 太大

```python
# 当前公式
stop_pct = clamp(0.007 + volatility_proxy / 850.0, 0.007, 0.025)
```

对于典型山寨币波动（volatility_proxy = 3~10%）：
- `/850` → 波动贡献 0.004~0.012 → 最终 stop 0.7%~1.9%
- 配合 **11x~23x 杠杆** → 1% 逆向波动 = 11%~23% 仓位亏损
- 加密货币分钟级 wiggle 1-2% 是常态 → **止损必触发**

### 实际验证（从生产库提取）

| symbol | volatility_proxy | stop_pct 公式输出 | lev | 止损距离 | 结果 |
|--------|-----------------|-------------------|-----|---------|------|
| LIGHTUSDT | ~3% | 1.0% | 23x | 1.0% | -$32.44 |
| STXUSDT | ~2% | 0.48% | 11x | 0.48% | -$28.92 |
| MOCAUSDT | ~3% | 1.0% | 11x | 1.0% | -$62.88 (3笔) |
| CROSSUSDT | ~3% | 1.0% | 23x | 1.0% | -$29.24 |

所有止损距离 ≤1.0%，无一幸存。

### 正确校准

分母应降到 **150~200**，使止损幅度匹配山寨币实际波动：

| volatility_proxy | /850 (旧) | /200 | /150 | /100 |
|-----------------|-----------|------|------|------|
| 3% | 1.05% | 2.2% | 2.7% | 3.7% |
| 5% | 1.29% | 3.2% | 4.0% | 5.7% |
| 8% | 1.64% | 4.7% | 6.0% | 8.7% |
| 12% | 2.11% | 6.7% | 8.7% | 12.7%→clamp |

**推荐**：`/200` 作为起点，稳定币 ~2% stop，高波动币 ~5-7% stop。

### 校准公式

```python
# 修复后
stop_pct = clamp(0.015 + volatility_proxy / 200.0, 0.015, 0.075)
# floor 从 0.7% → 1.5%（山寨币基础 wiggle）
# ceiling 从 2.5% → 7.5%（高波动币有呼吸空间）
```

### 配套调整

- **杠杆联动**：高杠杆币应给更宽的止损。`pick_leverage()` 中 max_leverage 应从 23x 降到 10-12x（山寨币合理区间）
- **neutral_probe 放宽**：若保留 `stop_pct * 1.5` 逻辑，floor clamp 也需相应提高

### 验证方法

修改止损公式后，用生产库验证：

```sql
-- 对比修复前后的预期止损距离
SELECT f.symbol, f.position_side,
  ROUND((f.price::numeric - s.payload_json->>'price_ref')::numeric / 
        (s.payload_json->>'price_ref')::numeric * 100, 2) as actual_stop_pct,
  (s.payload_json->'trade_params'->'exits'->'stop_loss'->>'stop_price')::numeric as stop_price
FROM fills f
JOIN signals s ON f.signal_id = s.signal_id
WHERE f.action_id IS NULL AND f.realized_pnl < 0
  AND s.strategy_id = 'workflow_distilled_funnel'
  AND f.filled_at > NOW() - INTERVAL '7 days';
```

## 风险

放宽止损范围意味着单笔最大亏损可能增大。配合 `risk_budget_pct` 的动态递减（加仓 budget × 0.5~0.25）和 `max_order_notional_pct` 封顶，总风险可控。

## ⚠️ 连锁效应：止损放宽 → 仓位缩小 → 需提 risk_pct（2026-05-17）

### 现象

止损分母 850→200 后，stop_pct 从 ~1% 扩到 ~3%，但 `risk_pct`（单笔风险预算）未同步调整。下单公式：

```
notional = (equity × risk_pct) / stop_pct
```

`stop_pct` 放大 3 倍 → `notional` 缩小到 1/3。$5000 账户实际仓位从 ~$5000 缩到 ~$1667。

### 诊断方法

先查后端实际风险参数，不要假设默认值（代码里的 40 是 fallback）：

```sql
SELECT * FROM strategy_risk_allocations WHERE strategy_id = 'workflow_distilled_funnel';
```

本例中 `max_order_notional_pct = 100%`（天花板 $5000 根本没碰到），瓶颈是策略自己的 `risk_pct`。

### 修复

```python
# 旧（止损窄时够用）
risk_pct = 1.0 if score < 82 else 1.5

# 新（止损放宽后补偿）
risk_pct = 2.5 if score < 82 else 3.5
```

| 场景 | 旧 stop | 新 stop | 旧 risk | 新 risk | 旧 notional | 新 notional |
|------|---------|---------|---------|---------|-------------|-------------|
| $5000, vol=3% | 1.05% | 3.0% | 1.0% | 2.5% | $4762 | $4167 |
| $5000, vol=5% | 1.29% | 4.0% | 1.0% | 2.5% | $3876 | $3125 |
| $5000, vol=8% | 1.64% | 5.5% | 1.5% | 3.5% | $4573 | $3182 |

实际仓位由后端 `max_order_notional_pct` 天花板兜底。

### 原则

**改 stop 公式后必须检查 notional 是否合理。** 止损和 risk_pct 是联动的——一个变宽，另一个就要提上去，否则仓位缩水。两个参数一起调。
