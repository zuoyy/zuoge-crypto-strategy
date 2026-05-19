# breakout 位置感知保护

2026-05-18 新增。原 breakout 只看 momentum+book，用户连续质疑
DOODUSDT 和 1000000BOBUSDT 的 breakout short 是追空。

## 触发条件

```python
# `_stage()` level 4 内
if signed_change > 3.0 and directional_book > 0.10 and spread_bps <= 15 and bias > 0.10:
    if side == "short" and pos_4h < 0.30:
        pass  # → neutral_probe
    elif side == "long" and pos_4h > 0.70:
        pass  # → neutral_probe
    else:
        return "breakout"
```

## 实际案例

| 信号 | pos_4h | 是否拦截 | 结果 |
|------|:------:|:--------:|------|
| DOODUSDT | 0.25 | ✅ 拦截 | 正确，用户认可 |
| 1000000BOBUSDT | 0.33 | ❌ 放行 | 用户追问"算追空吗" |

## 用户哲学

"以小博大" = 反转交易，不是动量追单。breakout 本身就是动量跟随，
和 reversal-first 架构矛盾。breakout 只有在：
- pos_4h 不在极端（≥0.30）
- book 极强（>0.30）
- spread 极低（≤10bps）
时才允许。

## 调试查询

```sql
-- 查某个信号的 pos_4h
SELECT evidence_json->>'position_4h' as pos4h,
       evidence_json->>'position_1h' as pos1h,
       evidence_json->>'stage' as stage,
       evidence_json->>'score' as score,
       evidence_json->>'signed_change' as sc
FROM strategy_decision_logs
WHERE symbol = 'DOODUSDT'
  AND created_at > now() - interval '1 hour'
  AND decision = 'SIGNAL'
LIMIT 1;

-- 查 breakout 被拦截的比例
SELECT reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id='workflow_distilled_funnel'
  AND created_at > now() - interval '5 minutes'
  AND evidence_json->>'stage' = 'breakout'
  AND reason LIKE '%oversold%'  
GROUP BY reason;
```
