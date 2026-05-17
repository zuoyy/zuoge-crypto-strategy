# 胜率/盈亏比诊断 SQL 全集

生产数据库：`postgres://zuo:@localhost:5432/crypto_trader?sslmode=disable`

## Step 0: 拆分成交来源（止损 vs 策略平仓）

**这是最重要的第一步**——不拆就不知道问题在止损还是策略方向。

```sql
SELECT 
    CASE WHEN f.action_id IS NULL THEN 'exchange_stop' ELSE 'strategy_close' END as source,
    COUNT(*) as fill_count,
    COUNT(DISTINCT f.signal_id) as signal_count,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as total_pnl,
    ROUND(AVG(f.realized_pnl)::numeric, 4) as avg_pnl_per_fill,
    COUNT(*) FILTER (WHERE f.realized_pnl > 0) as win_fills,
    COUNT(*) FILTER (WHERE f.realized_pnl < 0) as loss_fills
FROM fills f 
WHERE f.signal_id LIKE 'sig-workflow%'
GROUP BY 1;
```

## Step 1: 总盘（胜率/盈亏比/期望值）

```sql
WITH trades AS (
    SELECT f.signal_id, SUM(f.realized_pnl) as pnl
    FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
    GROUP BY f.signal_id
)
SELECT 
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE pnl > 0) as wins,
    COUNT(*) FILTER (WHERE pnl < 0) as losses,
    COUNT(*) FILTER (WHERE pnl = 0) as breakeven,
    ROUND(100.0 * COUNT(*) FILTER (WHERE pnl > 0) / NULLIF(COUNT(*),0), 1) as win_rate_pct,
    ROUND(AVG(pnl) FILTER (WHERE pnl > 0)::numeric, 2) as avg_win,
    ROUND(AVG(ABS(pnl)) FILTER (WHERE pnl < 0)::numeric, 2) as avg_loss,
    ROUND(
        NULLIF(AVG(pnl) FILTER (WHERE pnl > 0), 0) / 
        NULLIF(AVG(ABS(pnl)) FILTER (WHERE pnl < 0), 0), 2
    ) as profit_factor,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl,
    ROUND(AVG(pnl)::numeric, 2) as expected_value
FROM trades;
```

## Step 2: 按阶段拆分胜率（使用 CASE，不用 SPLIT_PART）

SPLIT_PART 解析 `signal_reason` 可能失败返回空。用 CASE + LIKE 更可靠：

```sql
WITH trades AS (
    SELECT f.signal_id, f.symbol, SUM(f.realized_pnl) as pnl
    FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
    GROUP BY f.signal_id, f.symbol
)
SELECT 
    CASE 
        WHEN s.signal_reason LIKE '%expansion_continuation%' THEN 'expansion_continuation'
        WHEN s.signal_reason LIKE '%trend_pressure_build%' THEN 'trend_pressure_build'
        WHEN s.signal_reason LIKE '%neutral_probe%' THEN 'neutral_probe'
        WHEN s.signal_reason LIKE '%accepted_breakout%' THEN 'accepted_breakout'
        WHEN s.signal_reason LIKE '%pullback_reaccept%' THEN 'pullback_reaccept'
        WHEN s.signal_reason LIKE '%sweep_reclaim%' THEN 'sweep_reclaim'
        ELSE 'other'
    END as stage,
    COUNT(*) as trades,
    COUNT(*) FILTER (WHERE t.pnl > 0) as wins,
    ROUND(100.0 * COUNT(*) FILTER (WHERE t.pnl > 0) / NULLIF(COUNT(*),0), 1) as win_rate_pct,
    ROUND(AVG(t.pnl)::numeric, 2) as avg_pnl,
    ROUND(SUM(t.pnl)::numeric, 2) as total_pnl
FROM trades t
JOIN signals s ON s.signal_id = t.signal_id
WHERE s.signal_id LIKE 'sig-workflow%'
GROUP BY 1
ORDER BY trades DESC;
```

## Step 2b: 按阶段+方向拆分

```sql
WITH trades AS (
    SELECT f.signal_id, SUM(f.realized_pnl) as pnl
    FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
    GROUP BY f.signal_id
)
SELECT 
    CASE 
        WHEN s.signal_reason LIKE '%expansion_continuation%' THEN 'expansion_continuation'
        WHEN s.signal_reason LIKE '%trend_pressure_build%' THEN 'trend_pressure_build'
        ELSE 'other'
    END as stage,
    s.side,
    COUNT(*) as trades,
    COUNT(*) FILTER (WHERE t.pnl > 0) as wins,
    ROUND(AVG(t.pnl)::numeric, 2) as avg_pnl,
    ROUND(SUM(t.pnl)::numeric, 2) as total_pnl
FROM trades t
JOIN signals s ON s.signal_id = t.signal_id
WHERE s.signal_id LIKE 'sig-workflow%'
GROUP BY 1, 2
ORDER BY 1, 2;
```

## Step 3: 止损距离分析 🔴 关键诊断

用 fills 表的 buy/sell 价格计算实际入场价，与 payload_json 中的 stop_price 对比：

```sql
SELECT 
    s.signal_id, s.symbol, s.side,
    ROUND(SUM(f.realized_pnl)::numeric, 2) as pnl,
    MIN(f.price) FILTER (WHERE f.side = 'buy') as first_buy,
    MIN(f.price) FILTER (WHERE f.side = 'sell') as first_sell,
    (s.payload_json->'trade_params'->'exits'->'stop_loss'->>'stop_price')::float as stop_price,
    SUBSTRING(s.signal_reason FROM 'change=([0-9.]+)%')::float as change_pct,
    SUBSTRING(s.signal_reason FROM 'book=([0-9.-]+)')::float as book_val
FROM signals s
JOIN fills f ON f.signal_id = s.signal_id
WHERE s.signal_id LIKE 'sig-workflow%'
GROUP BY s.signal_id, s.symbol, s.side, s.payload_json, s.signal_reason
HAVING SUM(f.realized_pnl) < 0
ORDER BY SUM(f.realized_pnl);
```

止损距离手工计算：
- **Long**: `(entry_price - stop_price) / entry_price × 100%`
- **Short**: `(stop_price - entry_price) / entry_price × 100%`

### 止损距离解读

| 止损距离 | 判定 | 行动 |
|----------|------|------|
| < 0.5% | 🔴 即刻止损 | 止损公式分母太大，修 |
| 0.5-1.0% | 🟡 过紧 | 配合 >10x 杠杆必被扫 |
| 1.0-2.0% | 🟢 合理 | 有呼吸空间 |
| > 3.0% | 🟡 过宽 | 单笔风险过大 |

### 2026-05-17 诊断发现

低价币（<$1）止损距离几乎为 0%（如 GTCUSDT 0.22%、SQDUSDT ≈0%、BRETTUSDT ≈0%、XANUSDT ≈0%），而高价币（>$40）止损距离 1.5%+。根因：止损公式分母 850 不对价格 scale。修复方向见 `stop-formula-dual-volatility.md`。

## Step 4: book 值与盈亏关系

```sql
WITH trades AS (
    SELECT f.signal_id, SUM(f.realized_pnl) as pnl
    FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
    GROUP BY f.signal_id
)
SELECT 
    s.side,
    SUBSTRING(s.signal_reason FROM 'book=([0-9.-]+)')::float as book_val,
    CASE WHEN t.pnl > 0 THEN 'WIN' ELSE 'LOSS' END as result,
    COUNT(*) as trades,
    ROUND(AVG(t.pnl)::numeric, 2) as avg_pnl
FROM trades t
JOIN signals s ON s.signal_id = t.signal_id
WHERE s.signal_id LIKE 'sig-workflow%'
GROUP BY s.side, 
    SUBSTRING(s.signal_reason FROM 'book=([0-9.-]+)')::float,
    CASE WHEN t.pnl > 0 THEN 'WIN' ELSE 'LOSS' END
ORDER BY s.side, book_val;
```

## Step 5: 按日期看 PnL 演进

```sql
WITH trades AS (
    SELECT f.signal_id, SUM(f.realized_pnl) as pnl,
        MIN(f.filled_at) as first_fill
    FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
    GROUP BY f.signal_id
)
SELECT 
    DATE(first_fill) as trade_date,
    COUNT(*) as trades,
    COUNT(*) FILTER (WHERE pnl > 0) as wins,
    COUNT(*) FILTER (WHERE pnl < 0) as losses,
    ROUND(SUM(pnl)::numeric, 2) as daily_pnl
FROM trades
GROUP BY 1 ORDER BY 1;
```

## Step 6: 盈亏平衡单明细

盈亏平衡单（pnl=0）通常是因为手动平仓、部分成交后撤单、或信号冷却导致重复入场后立即止损。需要逐笔检查 `fill_count` 和 `has_exchange_stop` 判断原因。

```sql
WITH trades AS (
    SELECT f.signal_id, SUM(f.realized_pnl) as pnl,
        COUNT(*) as fill_count,
        BOOL_OR(f.action_id IS NULL) as has_exchange_stop
    FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
    GROUP BY f.signal_id
)
SELECT s.signal_id, s.symbol, s.side,
    t.fill_count, t.has_exchange_stop,
    s.signal_reason, s.created_at
FROM trades t
JOIN signals s ON s.signal_id = t.signal_id
WHERE t.pnl = 0
ORDER BY s.created_at DESC;
```

## Step 7: 信号量趋势

```sql
SELECT DATE(created_at) as day, COUNT(*) as signals, COUNT(DISTINCT symbol) as symbols
FROM signals WHERE signal_id LIKE 'sig-workflow%'
GROUP BY 1 ORDER BY 1 DESC LIMIT 10;
```

## 诊断优先级

1. **Step 0 先拆来源** — 如果止损全亏策略平仓胜率正常 → 修止损公式
2. **Step 2 按阶段拆** — 找出烧钱的 stage（如 trend_pressure_build 14.3% 胜率）
3. **Step 3 算止损距离** — 如果 < 0.5% → 分母 850 陷阱
4. **Step 4 查 book 值** — 是否有 book 反向入场（short 时 book > 0）
5. **Step 5 看时间演进** — 最近是在改善还是恶化
