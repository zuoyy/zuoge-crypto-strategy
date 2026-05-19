# Daily Trading Review Cron Job

Set up a cron job that queries the production DB at 10:00 Beijing time every day and pushes a comprehensive review report to the user via Telegram.

## Schedule

- Beijing time 10:00 = UTC 02:00
- Cron expression: `0 2 * * *`
- Cron command: `hermes cron create --schedule "0 2 * * *" --prompt "$(cat daily-review-prompt.md)"`

## Key Tables in `crypto_trader` DB

| Table | Use | Key Columns |
|---|---|---|
| `fills` | Yesterday's filled trades | `venue`, `filled_at`, `symbol`, `side`, `position_side`, `quantity`, `price`, `notional`, `realized_pnl`, `commission` |
| `portfolio_snapshots` | Equity & exposure history | `venue`, `updated_at`, `equity`, `total_exposure`, `open_positions`, `available_cash` |
| `position_plan_runtimes` | Current open positions | `venue`, `runtime_status`='active', `symbol`, `position_side`, `remaining_position_ratio` |
| `signals` | Signal lifecycle | `venue`, `created_at`, `status`, `symbol`, `side` |

## DB Query Patterns

### Yesterday's fills summary (per symbol)

```sql
SELECT symbol, position_side,
       SUM(CASE WHEN side='buy' THEN realized_pnl ELSE 0 END) as buy_pnl,
       SUM(realized_pnl) as realized_pnl,
       SUM(commission) as total_commission,
       (SUM(realized_pnl) - SUM(commission)) as net_pnl
FROM fills
WHERE venue = 'live'
  AND filled_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') - interval '1 day')::timestamptz
  AND filled_at < date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')::timestamptz
GROUP BY symbol, position_side
ORDER BY net_pnl ASC;
```

### Overall yesterday stats

```sql
SELECT 
  COUNT(DISTINCT symbol) as symbols,
  SUM(realized_pnl) as total_realized_pnl,
  SUM(commission) as total_commission,
  (SUM(realized_pnl) - SUM(commission)) as net_pnl,
  COUNT(*) FILTER (WHERE realized_pnl > 0) as win_fills,
  COUNT(*) FILTER (WHERE realized_pnl < 0) as loss_fills,
  COUNT(*) as total_fills
FROM fills
WHERE venue = 'live'
  AND filled_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') - interval '1 day')::timestamptz
  AND filled_at < date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')::timestamptz;
```

### Equity history (yesterday hourly)

```sql
SELECT 
  ROUND(MIN(equity::numeric), 2) as min_equity,
  ROUND(MAX(equity::numeric), 2) as max_equity,
  ROUND(AVG(equity::numeric), 2) as avg_equity,
  ROUND(AVG(total_exposure::numeric), 2) as avg_exposure,
  ROUND(AVG(open_positions::numeric), 1) as avg_positions
FROM portfolio_snapshots
WHERE venue = 'live'
  AND updated_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') - interval '1 day')::timestamptz
  AND updated_at < date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')::timestamptz;
```

### Current positions

```sql
SELECT symbol, position_side, runtime_status,
       entry_filled_at AT TIME ZONE 'Asia/Shanghai' as entry_at,
       remaining_position_ratio
FROM position_plan_runtimes
WHERE venue = 'live' AND runtime_status = 'active'
ORDER BY symbol;
```

### Signal stats yesterday

```sql
SELECT status, COUNT(*) as cnt
FROM signals
WHERE venue='live'
  AND created_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') - interval '1 day')::timestamptz
  AND created_at < date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')::timestamptz
GROUP BY status ORDER BY cnt DESC;
```

## Report Template

```
📊 昨日交易复盘 | YYYY-MM-DD

━━━ 总体情况 ━━━
• 交易对数量：N 个
• 总成交笔数：N 笔
• 已实现盈亏：-$X.XX
• 手续费：$X.XX
• 净盈亏：-$X.XX

━━━ 权益变化 ━━━
• 最低权益：$X.XX
• 最高权益：$X.XX
• 平均权益：$X.XX
• 平均敞口：$X.XX
• 平均持仓：N 个

━━━ 按标的盈亏排行（亏→盈）━━━
1. XXX 空头 -$X.XX
N. XXX 多头 +$X.XX ✓

━━━ 赢家/输家分析 ━━━
• 最大亏损：XXX -$X.XX — 原因分析
• 最大盈利：XXX +$X.XX — 原因分析

━━━ 当前持仓 ━━━
• 账户权益：$X.XX
• 当前持仓数：N
• XXX：多头（开仓于昨日…）

━━━ 交易信号统计 ━━━
• 信号总数：N
• 各状态分布：expired N / executed N / rejected N / failed N

━━━ 小结与建议 ━━━
• 昨日整体评价（净亏时客观分析，不粉饰）
• 问题点
• 今日关注方向
```

## Security Constraints

- **Never** write `${ZUOGE_CRYPTO_API_KEY}` literally in the cron prompt — it triggers `exfil_curl_auth_header` pattern block
- If the cron needs API data, use DB queries as fallback (position_plan_runtimes instead of /api/v1/agent/positions)
- The API key exists as an env var and can be referenced in shell commands, just not in the prompt text itself

## Fallback When No Yesterday Data

If yesterday had zero trades, send a minimal report:
```
📊 昨日交易复盘 | YYYY-MM-DD
━━━ 总体情况 ━━━
昨日无交易记录。
```

## Cron Prompt Guidance

The cron prompt should be a complete self-contained markdown prompt that a clean-session agent can execute without prior context. Include:
1. Exact psql commands with full SQL
2. The report template
3. Send_message instruction at the end
4. Enforce Beijing timezone (Asia/Shanghai) for all date calculations
