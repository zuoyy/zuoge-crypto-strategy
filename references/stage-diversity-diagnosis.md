# 信号阶段多样性诊断

## 现象

策略输出信号阶段单一（如 89% trend_pressure_build + 11% expansion_continuation），缺少 reversal、breakout、early_trend 等阶段的信号。零 long 信号。

## 诊断方法

### 1. 信号阶段分布

```sql
SELECT
  CASE
    WHEN signal_reason LIKE '%expansion_continuation%' THEN 'expansion'
    WHEN signal_reason LIKE '%trend_pressure_build%' THEN 'trend_build'
    WHEN signal_reason LIKE '%accepted_breakout%' THEN 'breakout'
    WHEN signal_reason LIKE '%high_reversal%' THEN 'high_reversal'
    WHEN signal_reason LIKE '%low_reversal%' THEN 'low_reversal'
    WHEN signal_reason LIKE '%early_trend%' THEN 'early_trend'
    WHEN signal_reason LIKE '%sweep_reclaim%' THEN 'sweep_reclaim'
    WHEN signal_reason LIKE '%pullback%' THEN 'pullback'
    ELSE 'other'
  END as stage,
  COUNT(*) as cnt,
  ROUND(AVG(…score…)::numeric, 1) as avg_score
FROM signals
WHERE strategy_id = 'workflow_distilled_funnel'
GROUP BY 1 ORDER BY cnt DESC;
```

### 2. 缺失阶段根因排查

对每个缺失阶段，查为什么 gate 不让过：

- **accepted_breakout**: `breakout_without_1h_4h_confirmation` 挡住多少？`_stage()` 需要的 signed_change ≥ 3.0 + book ≥ 0.08 + spread ≤ 18 + bias ≥ 0.05 是否同时满足？
- **early_trend**: stage_bonus 是 0 吗？被 `stage_score_too_low` (72) 还是 `neutral_probe_too_weak` (82) 砍？
- **pullback_reaccept**: 被 `trend_pressure_build` 先匹配了吗？（signed_change 0.2-1.8 范围重叠）
- **sweep_reclaim**: `_stage()` 是否从未 return 这个值？（**死代码检查**）
- **high_reversal_short / low_reversal_long**: funding 条件（> 0.0008 / < -0.0008）是否达到？

### 3. long 信号缺失排查

- BTC regime gate: `btc_change < -1.5%` 是否一刀切杀了所有 long？
- discover() 的 `_universe_setups()` 中 long setup 是否只在 `change < 0` 时才生成？
- 超买过滤 `pos_1h > 0.88 or pos_4h > 0.88` 是否太严？

## 已知问题

### sweep_reclaim 死代码

`sweep_reclaim` 在以下三处被引用，但 `_stage()` 从不返回它：

| 位置 | 用途 |
|------|------|
| `_trade_gate` 行 353 | 反手豁免：允许 strong setup 反手 |
| `_apply_entry_plan` 行 651 | pullback_into_range 触发 |
| `_stage_bonus` 行 1012 | +7 分奖励 |

`_stage()` 中缺少对 sweep_reclaim 的判断逻辑。这是一个完整的 setup 从未被实现。

### early_trend stage_bonus=0

`_stage_bonus` 中 `"early_trend": 0.0`，而 `trend_pressure_build` 得 +2.0。这导致 early_trend 的信号更难通过 score floor，所有过渡期信号都被 trend_pressure_build 吸收。

### BTC regime gate 一刀切

`discover()` 行 66-68：
```python
if side == "long" and btc_change < -1.5:
    continue  # BTC dropping, don't long alts
if side == "short" and btc_change > 1.5:
    continue  # BTC pumping, don't short alts
```

只在 BTC ±1.5% 窄幅时两边都放行。建议对极端值柔性化处理（如分级降权而非全杀）。

### 阶段匹配顺序陷阱

`_stage()` 返回第一个匹配的阶段。`trend_pressure_build`（signed_change > 0.5, book ≥ -0.02）范围很宽，会抢先匹配 `pullback_reaccept`（-1.5 ≤ signed_change ≤ 1.8, book ≥ 0.05）和 `early_trend`（signed_change > 0.2, book ≥ 0.0）的候选。**更宽泛的阶段应放在更后面**，或者给窄阶段更严格但独立的入参。

## 修复方向

1. 实现 sweep_reclaim 在 `_stage()` 中的判断
2. early_trend 给正 bonus（如 +1.0），不要 0
3. BTC regime gate 分级：-2% 以下全杀 long，-1% 到 -2% 降权
4. 重排 `_stage()` 匹配顺序：窄阶段在前，宽阶段在后
