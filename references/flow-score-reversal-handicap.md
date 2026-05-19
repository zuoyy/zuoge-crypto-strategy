# flow_score — Reversal Signal Handicap (Detected & Fixed)

2026-05-18 发现并修复：flow_score 对反转入场施加了结构性惩罚，即使 stage 分类准确，候选也因低分被 trade gate 斩杀。

## 问题

```python
flow_score = clamp(50.0 + directional_book * 180.0 + change_bonus(signed_change) * 3.0, 0.0, 100.0)
```

**flow_score 本质是动量跟随评分**，对反转入场天然不利：

| 场景 | directional_book | change_bonus(signed_change) | flow_score |
|------|------------------|---------------------------|------------|
| 做空拉升 4% 币 | `(-0.10)*180 = -18` | `(-4.0)*3 = -12` | **20** ❌ |
| 做多暴跌 4% 币 | `(-0.10)*180 = -18` | `(-4.0)*3 = -12` | **20** ❌ |
| 做空暴跌 8% 币 | `(+0.05)*180 = +9` | `(+8.0)*3 = +24` | **83** ✓ |
| 做多大涨 4% 币 | `(+0.05)*180 = +9` | `(+4.0)*3 = +12` | **71** ✓ |

反转入场时，`directional_book` 不利（你在对抗当前盘口），`change_bonus` 也是负值（价格已经逆方向了），flow_score 会被打到 20 分。

## 候选总分链式效应

```
score = candidate_score × 0.30
      + setup_score × 0.25
      + liquidity_quality × 0.15
      + flow_score × 0.20       ← 反转候选拿 20 → 贡献 4 分
      + stage_bonus              ← deep_reversal +10 (旧), pullback +7 (旧)
      - penalties
```

即使 deep_reversal (+10 bonus)，总分 ≈ 55，不到 trade gate 的 72（非 neutral_probe）或 83（neutral_probe）门槛。

## 修复方案（2026-05-18 impl）

### 修复一：flow_score 反转感知

```python
if stage in ("deep_reversal", "pullback_reversal"):
    flow_score = clamp(50 + abs(directional_book) * 180 + abs(change_bonus) * 3, 0, 100)
else:
    flow_score = clamp(50 + directional_book * 180 + change_bonus * 3, 0, 100)
```

关键：赋值中用 `abs()`，让逆向盘口和逆向变动变为加分而非扣分。

### 修复二：stage_bonus 提升

| Stage | 旧 bonus | 新 bonus |
|-------|---------|---------|
| deep_reversal | +10 | **+15** |
| pullback_reversal | +7 | **+10** |
| trend_continuation | +5 | 不变 |
| breakout | +5 | 不变 |

#### 修复效果（做空拉升 4% 币）

```
旧: score = 19.8 + 16.5 + 8.25 + flow(20*0.20=4) + 10 - 4 = 54.6 ❌
新: score = 19.8 + 16.5 + 8.25 + flow(80*0.20=16) + 15 - 4 = 71.6 ≈ 72 ✓
```

## 诊断方法

```sql
-- signed_change 分布确认候选来源
SELECT CASE 
  WHEN (evidence_json->>'signed_change')::numeric < -5.0 THEN 'deep_reversal_range'
  WHEN (evidence_json->>'signed_change')::numeric < -2.0 THEN 'pullback_range'
  ELSE 'other' END as sc_range,
  COUNT(*) as cnt
FROM strategy_decision_logs 
WHERE strategy_id='workflow_distilled_funnel'
  AND created_at > now() - interval '5 minutes'
GROUP BY sc_range;

-- 检查反转 stage 是否已被正确识别
SELECT symbol, side, evidence_json->>'stage' as stage, 
       evidence_json->>'signed_change' as sc,
       evidence_json->>'score' as score,
       reason
FROM strategy_decision_logs 
WHERE strategy_id='workflow_distilled_funnel'
  AND created_at > now() - interval '5 minutes'
  AND evidence_json->>'stage' NOT IN ('neutral_probe', '')
  AND evidence_json->>'stage' IS NOT NULL;
```

## Pitfalls

- **`if/else` 全分支覆盖**：stage-aware 代码必须覆盖 reversal 和 non-reversal 两条路径，不能只改一条线。
- **双侧验证**：修复 flow_score 后必须验证双侧（long + short）的 avg_flow_score 都在正常范围（>60）。2026-05-18 生产发现 short 侧正确（76-100），但 long 侧仅 15.14，因为 long 侧 stage 分类器未产生 `deep_reversal` 值，`abs()` 分支未触发。
- **stage 分类器先于 flow_score**：如果 `_stage()` 没有为 long 侧产生 `deep_reversal` 值，flow_score 的 `abs()` 分支永不会触发。先查 stage 分布，再查 flow_score。
- **stage_bonus 联动**：仅修复 flow_score 还不够（4% 币从 54 → 56）。必须同步提升 stage_bonus。
- **flow_score 权重限制**：20% 权重下，flow 从 20→80 只能加 12 个综合分点。如果基分太低光靠 flow 和 bonus 救不回。
- **市场环境依赖**：反转修复仍需等待被拉升的币出现（signed_change < 0 for short）。无涨的币不会触发反转。
