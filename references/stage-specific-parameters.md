# Stage-Specific Parameters: Stop Width & Trailing

2026-05-17 实战优化 workflow_distilled_funnel 0.1.0。

## 问题

一刀切参数导致矛盾：reversal 需要宽止损扛噪声，但 trend_continuation 需要在趋势确认后快速止盈。统一参数要么 reversal 被扫、要么 trend 跑不掉。

## 方案：Stage 差异化三件套

### 1. 止损宽度 — _evaluate_context()

```python
# Base stop from volatility formula
stop_pct = clamp(0.015 + volatility_proxy / 200, 0.015, 0.075)

# Stage-specific adjustment
if stage == "deep_reversal":
    stop_pct = clamp(stop_pct * 1.3, 0.025, 0.080)   # wider: 扛反转噪声
elif stage in ("trend_continuation", "early_trend"):
    stop_pct = clamp(stop_pct * 0.8, 0.015, 0.050)   # tighter: 趋势确认，不需要太大空间
```

| vol=3% | base | deep_rev | trend_cont |
|--------|------|----------|------------|
| stop_pct | 3.0% | 3.9% | 2.4% |
| 含义 | | 给反转更多呼吸 | 趋势不对立刻走 |

### 2. 移动止盈 — _apply_stage_exits()

```python
if stage in ("deep_reversal", "pullback_reversal"):
    # Runner mode: reversal bets can run 10-20%+
    activation = stop_pct * 4.0    # 激活更晚
    trail_width = stop_pct * 3.0   # trail 更宽
else:
    activation = stop_pct * 3.0
    trail_width = stop_pct * 2.0
```

| stop=3% | trend 阶段 | reversal runner |
|----------|-----------|-----------------|
| 激活 | 盈利 9% | 盈利 12% |
| trail | 回撤 6% 走 | 回撤 9% 走 |
| 20%涨幅结果 | +14% | +11% (仍在场) |

### 3. 盈亏比 — _evaluate_context()

```python
reward_risk = 2.6 if stage in ("breakout", "deep_reversal") else 2.2
```

反转/突破给更高 RR 目标。

## ⚠️ 止损宽度 ↔ 仓位大小的联动

止损放宽后 `notional = risk / stop_pct` 自动缩小。**必须同步提 risk_pct 否则仓位同比例缩水。**

正确的 `risk_pct` 应动态跟随分数：
```python
risk_pct = clamp(2.0 + (score - 55) * 0.06, 2.0, 4.0)
```

这样高分信号（配宽止损的 reversal）自动获得更多风险预算，对冲止损放宽效应。
