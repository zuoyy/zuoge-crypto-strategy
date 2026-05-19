# 方向变换值验证：signed_change / directional_book 三步验证法

`signed_change` 和 `directional_book` 都经过 `direction`（long=+1, short=-1）乘算。比较运算符在这层变换后语义可能反转。**语法对称 ≠ 语义正确。每次审查这两个值的条件时必须执行三步验证。**

## 三步验证法

```
1. 写出原始语义（没有 direction 变换时的含义）
2. 做方向变换：原始语义 × direction
3. 对照代码验证
```

### 示例：做空 deep_reversal（本次实际 bug）

**注释**：「coin pumped 5%+, deeply overbought, book flipping bearish」
**代码**（错误）：
```python
if signed_change > 5.0 and directional_book < -0.02:
```

**三步验证**：
```
1. 原始语义：币涨 5%+（为做空逆向）、卖方主导（book_imbalance < 0）
2. 方向变换：signed_change = change × (-1)，directional_book = book_imbalance × (-1)
3. 验证：
   - 币涨 5%+ → change > 5 → signed_change = -change < -5 → 代码写 signed_change > 5 ❌
   - 卖方主导 → book_imbalance < 0 → directional_book = -book_imbalance > 0 → 代码写 < -0.02 ❌
```

**正确代码**：
```python
if signed_change < -5.0 and directional_book > 0.02:
```

### 示例：book gate 做空（同一次 bug）

**注释**：「short needs seller pressure」
**代码**（错误）：
```python
if state["side"] == "short" and state["directional_book"] > 0.05:
    return False, "book_not_supporting_short"
```

**三步验证**：
```
1. 原始语义：需要卖方主导（book_imbalance < 0），不满足时拒
2. 方向变换：directional_book = book_imbalance × (-1)
3. 验证：
   - 卖方不存在 → book_imbalance > 0 → directional_book < 0
   - 代码拒的是 directional_book > 0.05（卖方存在时）❌
```

**正确代码**：
```python
if state["side"] == "short" and state["directional_book"] < -0.05:
    return False, "book_not_supporting_short"
```

## 诊断信号

当策略某一侧**全部落在 `neutral_probe` 阶段**时，立即怀疑 `_stage()` 中该侧的 signed_change/directional_book 条件写反：

```sql
SELECT evidence_json->>'stage' as stage, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND side = 'short'
  AND created_at > now() - interval '10 minutes'
GROUP BY stage;
```

如果 100% 落在 `neutral_probe`，说明所有 reversal/continuation 条件均不匹配 → 方向变换错误。

## 涉及字段清单

| 字段 | 变换方式 | long 含义 | short 含义 |
|------|---------|----------|-----------|
| `signed_change` | `change × direction` | change | -change |
| `directional_book` | `book_imbalance × direction` | book_imbalance | -book_imbalance |
| `bias` | `signed_trend/4 + range_bias` | 含 direction | 含 direction |
| `funding_penalty` | 不乘 direction，语义: side + funding sign 组合判断 | 正 funding = longs pay → 罚 | 负 funding = shorts pay → 罚 |

### 扩展案例：`_funding_penalty` 方向陷阱

`funding_penalty` 不经过 direction 变换，但它的语义是方向相关的！不能只看绝对值的正负。

```python
# 正确语义：
#   side == "long" 且 funding > 0.0015 → 做多资金费高 → 罚做多
#   side == "short" 且 funding < -0.0015 → 做空资金费高 → 罚做空
```

**常见写反**（实际生产 bug）：
```python
# 错误：把"positive funding = longs pay"记反了，以为正资金费对做空不利
if side == "short" and funding > 0.0015:   # ❌ 正资金费时罚做空 — 但正资金费是对做空有利的
    return 8.0 + (funding - 0.0015) * 4000.0
```

**三步验证**：
```
1. 原始语义：正资金费 → longs pay shorts → 做空收钱（有利）；负资金费 → shorts pay longs → 做多收钱（有利）
2. 需要罚的是"做哪个方向费用就不划算"：做多 + 正资金费（付钱）不划算；做空 + 负资金费（付钱）不划算
3. 代码验证：long + funding > 0.0015 → 罚 ✓  /  short + funding < -0.0015 → 罚 ✓
```

## 检测方法

每次审查 `_stage()`、`_trade_gate()` 中涉及上表字段的条件时，对**每一个 short 侧的条件**执行三步验证。不要因为 long 侧正确就推断 short 侧也对。
