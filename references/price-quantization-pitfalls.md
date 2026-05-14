# 价格量化陷阱：超低价币的止损/止盈归零与方向错位

## 问题场景

Binance 超低价合约（price < $0.01，如 JCTUSDT、XANUSDT、AKTUSDT）的 tick_size 可能与价格本身量级接近甚至更大。例如：

- JCTUSDT: price ≈ 0.004, tick_size = 0.01
- AINUSDT: price ≈ 0.13, tick_size = 0.01

`quantize_price()` 使用 ROUND_DOWN，会导致止损/止盈归零或方向错位。

## `_quantize()` 实现

```python
def _quantize(value: float, step: float) -> float:
    if value <= 0 or step <= 0:
        return value
    dec_value = Decimal(str(value))
    dec_step = Decimal(str(step))
    return float((dec_value / dec_step).to_integral_value(rounding=ROUND_DOWN) * dec_step)
```

## 三个典型故障模式

### 模式 1：止损/止盈归零

```python
# JCTUSDT: price=0.004358, tick_size=0.01
stop_distance = 0.004358 * 0.022 = 0.000096
stop_price = quantize_price(0.004358 - 0.000096, tick_size=0.01)
# = _quantize(0.004262, 0.01)
# = (0.004262 / 0.01 = 0.4262 → ROUND_DOWN → 0) * 0.01 = 0
```

结果：`stop_price=0` → Go 校验 `!StopPrice.IsPositive()` → reject

### 模式 2：TP 方向错位（LONG 时 TP 低于入场价）

```python
# AINUSDT: price=0.13241, tick_size=0.01, stop_pct=0.022
stop_distance = 0.002913
tp1 = quantize_price(0.13241 + 0.002913 * 1.5, tick_size=0.01)
# = _quantize(0.13678, 0.01)
# = (0.13678 / 0.01 = 13.678 → ROUND_DOWN → 13) * 0.01 = 0.13
# 0.13 < 0.13241(price) → TP 在入场价下方！
```

对于 LONG，TP 必须在入场价之上；对于 SHORT，TP 必须在入场价之下。

### 模式 3：量化吃掉盈亏比

```python
# KITEUSDT: price=0.22184, tick_size=0.01
stop_price = 0.21                    # 量化后止损
actual_risk = 0.22184 - 0.21 = 0.01184

# 按公式：tp1 = 0.22184 + 0.01184 * 2.2 = 0.247888
tp1 = quantize_price(0.247888, tick_size=0.01)
# = (0.247888 / 0.01 = 24.7888 → ROUND_DOWN → 24) * 0.01 = 0.24

reward = 0.24 - 0.22184 = 0.01816
ratio = 0.01816 / 0.01184 = 1.534  # 远低于 min_reward_risk=2.2
```

### 模式 4：TP1 == TP2 阶梯崩溃（非量化问题，是比例逻辑 bug）

**症状**：ladder 止盈的两个 target 价格完全一样，用户反馈“两个目标止盈价格都是 0.01 一样？这还叫移动止盈吗”。

**根因**：不是量化问题，是 `basic_trade_params()` 的 `tp1_ratio` 计算：

```python
# ❌ 错误写法（会崩溃）
tp1_ratio = max(reward_risk, 1.5)   # max(2.2, 1.5) = 2.2

# 导致：
tp1 = _quantized_or_fallback(price + actual_risk * 2.2, ...)  # TP1=2.2x
tp2 = _quantized_or_fallback(price + actual_risk * 2.2, ...)  # TP2=2.2x
# → 三个参数全部相同 → 必然输出相同价格
```

`max(reward_risk, 1.5)` 在 `reward_risk ≥ 1.5` 时（始终：2.2 或 2.6）直接等于 `reward_risk`，TP1 和 TP2 的 `value`、`fallback`、`min_value` 三个参数完全一致，无论什么币都是相同输出。

**修复**：

```python
# ✅ TP1 固定 1.5x（近端），TP2 用 reward_risk（远端）
tp1_ratio = 1.5
tp1 = _quantized_or_fallback(price + actual_risk * 1.5, ...)       # TP1=1.5x
tp2 = _quantized_or_fallback(price + actual_risk * reward_risk, ...) # TP2=2.2x
```

同时，策略 `_apply_execution_constraints()` 的 `min_reward_risk` 必须匹配 TP1：

```python
# ✅ min_reward_risk = 1.5（Go 用第一个 target 校验）
"min_reward_risk": "1.5"
# ❌ 不要用 max(reward_risk, 1.5) → 会是 2.2，卡住 TP1
```

**验证**：本地跑 `basic_trade_params()` 后检查 `take_profit.targets[0].price != targets[1].price`。任何价格/杠杆组合都应如此。

## SDK 防御函数：`_quantized_or_fallback()`

```python
def _quantized_or_fallback(
    value: float,
    fallback: float,
    context: dict,
    *,
    min_above: float | None = None,   # 结果必须 > min_above
    max_below: float | None = None,   # 结果必须 < max_below
    min_value: float | None = None,   # 结果必须 >= min_value
) -> float:
```

参数说明：

| 参数 | 用途 | 示例 |
|------|------|------|
| `min_above` | TP 不能低于入场价（LONG） | `min_above=price` |
| `max_below` | TP 不能高于入场价（SHORT） | `max_below=price` |
| `min_value` | 量化后仍然满足最小比例要求 | `min_value=price + actual_risk * ratio` |

逻辑：
1. 先量化 `value`
2. 检查方向（`min_above`/`max_below`）和最小值（`min_value`）
3. 任一失败 → 回退到未量化的 `fallback`
4. fallback 也施加方向/最小值约束

## `basic_trade_params()` 中的正确用法

```python
# 1. 先量化止损，再取实际距离
stop_price = _quantized_or_fallback(price - stop_distance, price * 0.99, context, max_below=price)
actual_risk = max(price - stop_price, stop_distance)

# 2. 用 actual_risk 算 TP 最小允许值（TP1=1.5x, TP2=reward_risk）
tp1_min = price + actual_risk * 1.5
tp2_min = price + actual_risk * reward_risk

# 3. 量化 TP，带方向+最小值保护
tp1 = _quantized_or_fallback(
    price + actual_risk * 1.5,              # 理想值（TP1=1.5x）
    price * (1.0 + 1.5 * stop_pct),          # fallback
    context,
    min_above=price,
    min_value=tp1_min,
)
tp2 = _quantized_or_fallback(
    price + actual_risk * reward_risk,       # 理想值（TP2=reward_risk）
    price * (1.0 + reward_risk * stop_pct),  # fallback
    context,
    min_above=price,
    min_value=tp2_min,
)
```

## 性能注记

- `_quantized_or_fallback` 回退到未量化值时，价格可能不是交易所接受的 tick_size 整数倍
- 对于 testnet 这无影响（执行层会再量化）
- 生产环境建议后端执行层做最终量化，策略层只需确保值在合理方向
