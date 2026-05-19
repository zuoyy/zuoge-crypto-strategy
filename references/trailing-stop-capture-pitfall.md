# Trailing Stop 利润吞噬陷阱

2026-05-18 APRUSDT 短空实证：trailing_stop 激活过晚+宽度过宽，10% 行情只吃到 1.6%。

## 现象

```
APRUSDT deep_reversal short
入场 $0.15951 → 最低 $0.14347（跌 10.05%）
TP1   $0.140225（跌 12.1%）— 没碰到
平仓 $0.15810  — 只赚 $0.49（0.88%）
```

## 根因

旧代码：
```python
if stage in ("deep_reversal", "pullback_reversal"):
    activation = stop_pct * 1.25   # 9.375%
    trail_width = stop_pct * 1.25  # 9.375%
```

trail 激活需要价格先走 9.375%（等于入场后的 9.375% profit 才启动）。启动后 trail 宽度又是 9.375%——意味着价格再跌多少都得留下 9.375% 的利润给市场。最差时刻：10.05% 行情 → trail 只捕捉 1.6%。

## 核心规则

**trail_width > stop_pct 时，trail 永远吃不到 profit**。因为 trail 宽度大于止损，进场价格级别的噪音就能触发回撤平仓。

> `trail_width ≤ stop_pct × 0.5`：行情走 10% 能吃 5%+，宽幅能保护持仓不被噪音扫掉。

## 修复

```python
# deep_reversal / pullback_reversal
activation = stop_pct * 0.5     # 3-4% profit 就激活
trail_width = stop_pct * 0.5    # 紧跟 3-4%
# breakout / trend_continuation
activation = stop_pct * 0.4
trail_width = stop_pct * 0.4
```

修复后 APRUSDT 场景：trail at $0.14885 → 吃 6.7% profit（vs 原 1.6%）。

## 阶段差异化

deep_reversal 需要比 breakout 稍宽的 trail（0.5x vs 0.4x），因为反转入场后短期反向波动更大。

## 验证方法

从 signal payload 读 exits.trailing_stop 的值，计算：

```python
best_price = ...  # 最高浮盈时的价格
entry_price = ... # 入场价
trail_trigger = best_price * (1 + trail_width)  # for long; for short: best_price * (1 - trail_width)
captured_profit = (entry_price - trail_trigger) / entry_price  # for short
```

如果 `captured_profit << (best_price - entry_price) / entry_price`，trail 太宽。
