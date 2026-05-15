# 加仓预算动态联动 max_add_count

## 原则

加仓预算不写死（不固定 50%），而是从后端 `strategy_risk_allocations.max_add_count` 动态读取，按公式递减。

## 公式

```
add_ratio = 1 / (1 + max_add_count)
budget = base_budget × add_ratio
```

| max_add_count | add_ratio | 单次加仓预算 | 总加仓预算上限 |
|:---:|:---:|------|------|
| 1 | 1/2 = 50% | 首次 × 50% | 50% |
| 2 | 1/3 ≈ 33% | 首次 × 33% | 67% |
| 3 | 1/4 = 25% | 首次 × 25% | 75% |

`max_add_count` 越大 → 单次加仓预算越小 → 总曝光受控。

## 实现

### 策略侧 — 从 context 动态读取

```python
# _apply_position_management: 读取 max_add_count 决定是否允许加仓
strategy_fit = context.get("strategy_account_fit") or {}
if "max_add_count" in strategy_fit:
    max_add = int(strategy_fit["max_add_count"])
elif slots_remaining > 0:
    max_add = int(slots_remaining)  # fallback
allow_add = max_add > 0

# _risk_budget_pct: 联动加仓预算
sf = context.get("strategy_account_fit") or {}
if "max_add_count" in sf:
    add_slots = int(sf["max_add_count"])
else:
    add_slots = int(sf.get("open_position_slots_remaining", 0))
if add_slots <= 0:
    add_slots = 1
add_ratio = 1.0 / (1.0 + float(add_slots))
budget = max(0.10, budget * add_ratio)
```

### 后端侧

需要 `context.strategy_account_fit` 中包含 `max_add_count` 字段：

```go
// strategy_risk_allocations 表已有 max_add_count 列
// context overlay 需透传此字段
fit["max_add_count"] = allocation.MaxAddCount
```

## 更进一步：金字塔递减序列

如果后端在 `owned_position` 中暴露 `add_count`（已加次数），可以做到真正的金字塔递减：

```python
# 第N次加仓 = 首次 × 1/2^(N+1)
add_seq = position.get("add_count", 0)  # 0-based
ratio = 1.0 / (2 ** (add_seq + 1))
# add_seq=0 → 50%, add_seq=1 → 25%, add_seq=2 → 12.5%
budget = max(0.05, budget * ratio)
```
