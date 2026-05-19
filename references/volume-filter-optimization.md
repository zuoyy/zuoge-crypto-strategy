# 24h 成交量过滤优化

## 问题诊断

策略中成交量过滤分布在 **4 道关卡**，各有缺陷：

| 关卡 | 位置 | 问题 |
|------|------|------|
| Discover 硬门禁 | `discover()` line 47 | `$100M` binary kill，无动态感知 |
| Universe 评分 volume_score | `_universe_score_components()` | 对数缩放满分 28，但纯静态量 |
| Trade Gate 二次过滤 | `_trade_gate()` | 门槛 `$60M` **低于** discover `$100M`→一致性错误 |
| Liquidity Quality | `_liquidity_quality()` | 线性 `35+vol/10M`，`$650M`即封顶 |

## 优化方案（3 维度）

### 1. 过滤质量 — 跨 discover cycle 动量追踪

每轮 discover 刷新 `_volume_history[symbol]`，对比上一轮的 `quote_volume`：

```python
prev_volume = self._volume_history.get(symbol, 0.0)
volume_change_pct = 0.0
if prev_volume > 0:
    volume_change_pct = (quote_volume - prev_volume) / prev_volume * 100.0
self._volume_history[symbol] = quote_volume
```

命中打分（`_universe_score_components`）：
- `volume_change_pct > 10%`（缩量萎缩）→ `volume_momentum = -2`
- `volume_change_pct < -10%`（放量增长）→ `volume_momentum = +2`
- `volume_momentum` 加入 `score` 计算：`38 + volume_score + move_score + setup_bias + volume_momentum - funding_penalty`

### 2. 流动性动态感知 — context 评估时对比候选 snapshot

`_evaluate_context` 中，将 context ticker 的实时 `quote_volume` 与候选快照比较：

```python
candidate_volume = strategy_sdk.number(candidate.get("quote_volume"), 0.0)
if quote_volume > 0 and candidate_volume > 0:
    volume_change_since_discovery = (quote_volume - candidate_volume) / candidate_volume * 100.0
    if volume_change_since_discovery < -10.0:
        liquidity_quality *= 0.85   # 缩量→降级
    elif volume_change_since_discovery > 10.0:
        liquidity_quality = min(100.0, liquidity_quality * 1.05)  # 放量→低度升级
```

作用：即使 discover 时 $120M 通过，到执行时只剩 $80M 也会自动降分。

### 3. 一致性修复 — Trade Gate 对齐

```python
# 旧: 60_000_000 — 低于 discover $100M，容忍缩水 40%
# 新: 100_000_000 — 对齐 discover 门槛
if state["quote_volume"] and state["quote_volume"] < 100_000_000:
    return False, "liquidity_too_thin"
```

### 4. Liquidity Quality 幂律缩放

线性 `35 + vol/10M` 在 $650M 封顶，高流通币无区分度。改用幂律：

```python
return strategy_sdk.clamp(35.0 + (quote_volume / 10_000_000.0) ** 0.6 * 5.0, 35.0, 100.0)
```

| Vol | 旧值 | 新值 |
|-----|------|------|
| $100M | 45 | 55 |
| $200M | 55 | 65 |
| $500M | 85 | 86 |
| $1B | 100(cap) | 100(cap) |
| $5B | 100(cap) | 100(cap) |

幂律维持 $100M~$1B 的连续梯度，不像线性那样在 $650M 突然失去分辨率。

## 管线总览（改后）

```
Discover                   评分 volume_score(28)    Trade Gate           Liquidity Quality
$100M 硬砍                  + volume_momentum(±2)   $100M 二次过滤        幂律缩放 35~100
volume_change_pct 追踪                                                  ⇅ 动态调整（缩量85%/放量105%）
```

## 关键原则

- **源头收紧 > gate 加码**：discover 的 volume_momentum 能在候选池层面就砍掉缩量币，比等 trade gate 再拒更高效
- **对称惩罚**：缩量的 penalty（-2）比放量的 bonus（+2）更敏感——缩量是 danger signal，放量只是 confirm
- **幂律优于线性**：金融数据用 log/power-law 缩放比线性更自然，避免头部币全得 100 分导致权重失衡
