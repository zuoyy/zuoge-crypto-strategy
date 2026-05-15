# 阶段分类多样性 — 打破全 short 单一信号

## 症状

策略产出信号全部是 `trend_pressure_build short`，没有 long、没有 reversal、没有 breakout。决策日志中：

```
SIGNAL: trend_pressure_build short score=81...
SIGNAL: trend_pressure_build short score=80...
SIGNAL: trend_pressure_build short score=79...
```

无多样性意味着策略只在一种市场条件下工作（下跌趋势），市场反转时零信号。

## 根因分析

### 1. Long book gate 过严

```python
# 旧：book 不能为负
if state["side"] == "long" and state["directional_book"] < 0:
    return False, "book_not_supporting_long"
```

实际市场中 book_imbalance 轻微为负是常态（卖盘压力天然存在）。要求绝对正 book 意味着几乎所有 long candidate 被拒。

**修复**：允许 ±0.03 的中性区
```python
if state["side"] == "long" and state["directional_book"] < -0.03:
    return False
```

### 2. Stage 分类断裂

```python
# 旧：trend_pressure_build 之后直接跳到 neutral_probe
if signed_change > 0.5 and directional_book >= -0.02:
    return "trend_pressure_build"
return "neutral_probe"  # 所有不够格的都掉坑里
```

neutral_probe 被 score floor 85 拦截，导致大量 candidate 在 `trend_pressure_build → NO_TRADE` 之间无路可走。

**修复**：加过渡阶段 `early_trend`
```python
if signed_change > 0.5 and directional_book >= -0.02:
    return "trend_pressure_build"
if signed_change > 0.2 and directional_book >= 0.0:
    return "early_trend"     # ← 新阶段：弱趋势，book 刚转正
return "neutral_probe"
```

`early_trend` 的 stage_bonus = 0（neutral_probe -2，trend_pressure_build +2），作为平缓过渡。它不是 neutral_probe，不享受 1.5x 止损放宽，但通过 gate 的分数门槛是 72（非 85），比 neutral_probe 更容易产出信号。

### 3. 配套修改

| 位置 | 改动 |
|------|------|
| `_stage_bonus` | 加 `"early_trend": 0.0` |
| `time_minutes` | 加 `"early_trend": 480`（最长时间止损） |
| `add_stage_filter` | 加 `"early_trend"` 到可加仓阶段 |
| trailing_stop | early_trend 不是 neutral_probe，自动获得 trailing ✅ |

## 验证

部署后 15 分钟即出现 `high_reversal_short` 信号（之前从未出现），证明 stage 分类和 book gate 的放宽生效。
