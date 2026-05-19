# $100 小型账户策略配置

## 核心问题

`workflow_distilled_funnel` 策略按百分比设计（risk_pct=2-4%, stop=1-8%），理论不受本金影响。但后端 `strategy_risk_allocations` 的绝对值天花板会在小账户下压扁策略行为。

## 关键参数分析

### max_order_notional_pct（下单金额天花板）

默认值 100% 会在 $100 账户下把所有下单压在 $100，导致：
- 期望 risk $2-4 → 实际 risk $0.5-1（因为 notional 封顶缩小）
- 策略行为从「每笔冒险 2-4%」变成「每笔冒险 0.5-1%」

**$100 推荐值：300%**（下单上限 $300，允许策略使用完整 risk budget）

### max_positions（最大仓位数）

$100 无法支持 3 个仓位：
- 3 × $300 = $900 总敞口，需要 max_total ≥ 900%
- 或每仓缩到 $167，risk 跌破 2% 底线

**$100 推荐值：2**（给轮换空间，不过度 churn）

### max_add_count（加仓次数）

$100 无加仓预算空间。

**$100 推荐值：0**

### max_total_exposure_pct（总敞口）

**$100 推荐值：500%**（2 仓 × $250 = $500）

## 轮换可行性

$100 可以轮换 2 个仓位，但信号密度是关键：
- 信号 > 20/10min → 高位 churn → 手续费侵蚀
- 每个轮换 = 平仓费 + 开仓费 ≈ 0.08% × $300 × 2 = $0.48（0.48% 本金）
- 通过提分门槛（neutral_probe 82→83）控制信号密度

## 配置速查

| 参数 | 默认 | $100 推荐 |
|------|------|----------|
| max_order_notional_pct | 100% | 300% |
| max_positions | 3 | 2 |
| max_add_count | 3 | 0 |
| max_total_exposure_pct | 1000% | 500% |
| max_symbol_exposure_pct | 300% | 300% |

## 信号密度调优

$100 账户信号过多 → 提 neutral_probe 门槛：
- 82→83：砍 90% 擦边信号
- 82→85：砍 98%，留极端高质信号

## 超时参数

$100 testnet 账户有额外延迟：
- `expire_ms`: 90s → 120s
- `expire_after_seconds` 市价: 60s → 120s
- `expire_after_seconds` 限价: 180s → 360s
