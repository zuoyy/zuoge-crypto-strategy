# BTC Regime 梯度惩罚

## 问题

旧版 `discover()` 中 BTC regime gate 是二元的：

```python
if side == "long" and btc_change < -1.5:
    continue  # BTC dropping, don't long alts
if side == "short" and btc_change > 1.5:
    continue  # BTC pumping, don't short alts
```

**问题**：BTC 跌 1.51% → 全杀 long；BTC 涨 1.51% → 全杀 short。±1.5% 以外的区间成了单边市场，导致信号一面倒。极端不专业。

## 修复：梯度惩罚替代硬杀

新增 `_btc_regime_penalty(btc_change: float) -> dict | None`：

```python
@staticmethod
def _btc_regime_penalty(btc_change: float) -> dict[str, float | None] | None:
    a = abs(btc_change)
    if a > 5.0:
        return None  # 极端行情，全部停信号
    if a <= 2.0:
        return {"long": 0, "short": 0}  # 正常市场，无惩罚
    
    # 2% < |btc| ≤ 5%: 梯度惩罚
    # penalty = 0 at 2%, penalty = 12 at 5% (linear)
    penalty = (a - 2.0) / 3.0 * 12.0
    
    if btc_change < 0:
        return {"long": penalty, "short": 0}   # BTC 跌，罚 long
    else:
        return {"long": 0, "short": penalty}   # BTC 涨，罚 short
```

返回值语义：
- `None`（顶层）：极端行情，整个 symbol 跳过
- `dict[side] = None`：该方向硬杀
- `dict[side] = 0`：无惩罚
- `dict[side] = float`：setup_bias 扣分

## 惩罚映射表

| BTC 变化 | abs(BTC) | long 惩罚 | short 惩罚 | 说明 |
|----------|----------|-----------|------------|------|
| -1.0% | 1.0% | 0 | 0 | 正常市场 |
| -2.0% | 2.0% | 0 | 0 | 阈值边界 |
| -3.0% | 3.0% | 4 | 0 | long 逆风，扣 4 分 |
| -4.0% | 4.0% | 8 | 0 | long 强逆风，扣 8 分 |
| -5.0% | 5.0% | 12 | 0 | long 极逆风，扣 12 分 |
| -6.0% | 6.0% | — | — | 极端行情，全部停 |
| +3.0% | 3.0% | 0 | 4 | short 逆风，对称 |
| +6.0% | 6.0% | — | — | 极端行情，全部停 |

## 使用方式（在 discover 中）

```python
btc_penalty = self._btc_regime_penalty(btc_change)
if btc_penalty is None:
    continue  # 极端行情，整个 symbol 跳过

for side, setup_id, setup_bias in side_rows:
    side_penalty = btc_penalty.get(side, 0)
    if side_penalty > 0:
        setup_bias = setup_bias - side_penalty  # 降权候选
    # setup_bias 进入 universe_score_components 影响最终 score
```

## 效果

- **保留了多样性**：BTC 跌 3% 时 long 候选仍然可能存活（高分候选扣 4 分后仍可能过 55 分 floor）
- **不会全杀**：只有极端行情（|BTC|>5%）才停全部信号
- **梯度平滑**：线性缩放避免阈值附近的行为突变
- **方向对称**：同样的数学逻辑用于 long 和 short

## 代码位置

- `_btc_regime_penalty()` — 策略文件中新增
- `discover()` 行 60-69 — 调用点
