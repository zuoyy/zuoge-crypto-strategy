# 反转优先阶段分类器架构

2026-05-17 第八轮优化：`_stage()` 从动量追涨改为反转优先。

## 问题

旧 `_stage()` 所有阶段都是顺方向追涨/追空：
- `signed_change > 0.5` → trend_pressure_build（追）
- `signed_change > 1.2` → expansion_continuation（追）
- `signed_change > 3.0` → accepted_breakout（追）

结果：币涨了做多，币跌了做空 → 山顶买入、谷底做空 → 胜率 14%。

## 设计：反转优先五层架构

```
Level 1: deep_reversal      (+10 bonus) — 反向5%+ + 超买超卖 + book翻转
Level 2: pullback_reversal   (+7 bonus)  — 反向2-5% + 接近极值 + book转向
Level 3: trend_continuation  (+5 bonus)  — 4h趋势确认 + 价格回调 + book同向
Level 4: breakout            (+5 bonus)  — 极强突破(book>0.10, bias>0.10, spread≤15)
Level 5: early_trend         (0 bonus)   — 弱信号(0.3-2% + book>0)
Fallback: neutral_probe      (-3 penalty) — 兜底，大概率被拒
```

## 关键设计原则

1. **反转优先**：币大跌→找底做多，币大涨→找顶做空。Level 1-2 覆盖。
2. **趋势中回调买**：Level 3 不做追涨——只在 4h 趋势确认后，价格小幅回调时顺势入场。
3. **突破极严格**：book>0.10 + bias>0.10 + spread≤15 → 稀有事件。
4. **兜底惩罚**：neutral_probe 给 -3 分惩罚，配合 score<82 拒掉。

## signed_change 方向约定

```python
direction = 1.0 if side == "long" else -1.0
signed_change = change * direction
```

- Long on -8% 币：signed_change = +8% * (-1) = -8%（负 = 逆方向，币跌越多越负）
- Short on +8% 币：signed_change = +8% * (-1) = -8%（负 = 逆方向，币涨越多越负）
- **不要混淆**：signed_change 的符号只反映方向变换的结果，**不要直接用 > 5 判断反转**

⚠️ 常见误解：认为 short reversal 需要 `signed_change > 5`。实际上代码中的写法是 `signed_change < -5.0`——对于 short（direction=-1），change=+5% → signed_change=-5.0，所以 `< -5.0` 等价于「币涨了 5%+」。

```python
# Code 中的写法：
if signed_change < -5.0 and ...:  # 币涨了 5%+（对 short 方向 = 逆势超5%）
    return "deep_reversal"
```

所以 reversal 判断统一为 **`signed_change < -N`**，不管做多还是做空：
- Long reversal: signed_change < -5（币跌了 5%+，逆 long 方向超5%）
- Short reversal: signed_change < -5（币涨了 5%+，逆 short 方向超5%）

## directional_book 方向约定

```python
directional_book = book_imbalance * direction
```

- Long: direction=+1 → directional_book>0 = buyers dominate（买盘主导，确认入场安全）
- Short: direction=-1 → directional_book>0 = sellers dominate → **坑**！这要求卖方已主导

**⚠️ 反转做空必须用 `directional_book < -0.02`（买方仍主导），不等 book 翻盘。**
反转做空的语义是在拉升顶部买盘最狂热时入场，不是等卖盘接手。

| Stage | Long | Short |
|-------|------|-------|
| deep_reversal | `directional_book > 0.02`（买入主导） | **`directional_book < -0.02`**（买盘仍主导，摸顶） |
| pullback_reversal | `directional_book > 0.01`（买入逐渐出现） | **`directional_book < -0.01`**（买盘仍主导） |
| trend_continuation | `directional_book > 0.0` | `directional_book < 0.0`（卖盘主导） |

## 效果

牛市（全场涨）：反转阶段不触发 → 策略不追涨 → 等回调。这是专业纪律。
熊市（全场跌）：反转阶段大量触发 → 策略抄底做多。
