# change_bonus 模式：修理奖励追涨的评分公式

## 问题

策略评分公式中出现 `signed_change * N` 线性系数时，涨幅最大的币天然得最高分。
例如 `signed_change * 4.0` 意味着 +15% 涨幅获得 +60 分，直接把 score 推上满分。
这是追涨杀跌的结构性根因——不是阈值问题，是公式问题。

## 解法：`_change_bonus()` 衰减函数

```python
@staticmethod
def _change_bonus(signed_change: float) -> float:
    """奖励温和动量，惩罚极端追涨/杀跌。

    区间：
      [0%,  5%]    — 线性奖励（方向 × 绝对值 × 1.0）
      (5%, 15%]    — 线性衰减到 0（奖赏归零）
      [15%, ∞)     — 不奖励（返回 0）
    """
    a = abs(signed_change)
    if a <= 5.0:
        return signed_change           # 线性奖励
    if a >= 15.0:
        return 0.0                     # 不奖励追涨
    sign = 1.0 if signed_change >= 0 else -1.0
    return sign * (5.0 - (a - 5.0) * 0.5)   # 5→15 线性衰减，15 处刚好 0
```

## 替换方式

```python
# 旧（奖励追涨）
flow_score = clamp(50.0 + book * 180 + signed_change * 4.0, 0, 100)

# 新（衰减追涨奖励）
flow_score = clamp(50.0 + book * 180 + change_bonus(signed_change) * 4.0, 0, 100)
```

## 效果

| 涨幅 | 旧奖励 | 新奖励 |
|------|--------|--------|
| +3% | +12 分 | +12 分 |
| +7% | +28 分 | +16 分 |
| +10% | +40 分 | +10 分 |
| +15% | +60 分 | 0 分 |
| +20% | +80 分 | 0 分 |
| +25% | +100 分 | 0 分 |

涨得最凶的币不再自动得最高分，必须靠盘口（book）质量取胜。

## 调参

三个魔术数字：
- `5.0`：线性奖励区上限（%）。调大 = 更容忍追涨
- `15.0`：零奖励起点（%）。调小 = 更早掐断奖励
- `0.5`：衰减斜率（分/%）。= 5 / (15-5)，让 15 处刚好到 0。保持这个关系

**不要**把这三个值放到 gate 层面去调——它们和 book gate、stop width 是不同层面的东西。这是公式设计，不是阈值调参。

## 适用范围

任何出现 `abs(change)` 或 `signed_change` 直接乘以系数的评分公式都可以套用。
也适用于 `candidate_score`、`move_score`、`strength_score` 等复合评分。
