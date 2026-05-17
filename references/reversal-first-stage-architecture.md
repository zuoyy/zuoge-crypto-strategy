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

```
direction = 1.0 if side == "long" else -1.0
signed_change = change * direction
```

- Long on -8% 币：signed_change = -8%（负 = 逆方向）
- Short on +8% 币：signed_change = +8%（正 = 逆方向，因为 short 方向是 -1）

所以 reversal 判断：
- Long reversal: signed_change < -5（币跌了 5%+）
- Short reversal: signed_change > 5（币涨了 5%+）

## 效果

牛市（全场涨）：反转阶段不触发 → 策略不追涨 → 等回调。这是专业纪律。
熊市（全场跌）：反转阶段大量触发 → 策略抄底做多。
