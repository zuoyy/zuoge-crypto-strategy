# 加仓评估冷却模式

## 问题

`_trade_gate()` 中同向持仓的加仓检查有 7 层 gate。当账户有 5-6 个活跃仓位时，每个 context 评估都会为每个候选币触发加仓检查。如果仓位浮盈不足（最常见的失败原因 `add_requires_min_float_profit`），每次评估都会跑完 7 层然后被拒——浪费巨大。

**实测数据**：10 分钟内 210,426 次评估，其中 99,528 次（47%）耗在 `add_requires_min_float_profit` 拒绝上。

## 方案：加仓拒绝冷却

为每个 `symbol:side` 键维护一个 `_add_rejected_until` 字典——记录上次加仓被拒的时间戳 + 冷却时长。冷却期内直接返回 `add_in_cooldown`，跳过全部 7 层 gate 检查。

### 初始化

```python
self._add_rejected_until: dict[str, float] = {}  # symbol:side → cooldown_end_timestamp
```

### 冷却检查（加在加仓 gate 最前面）

```python
import time as _time
_add_key = f"{state['symbol']}:{pos_side}"
_add_until = self._add_rejected_until.get(_add_key, 0)
if _add_until > 0 and _time.time() < _add_until:
    return False, "add_in_cooldown"
```

### 拒绝时记录冷却

每个加仓 gate 拒绝分支后追加：
```python
self._add_rejected_until[_add_key] = _time.time() + 300  # 5 分钟冷却
```

## 效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| `add_requires_min_float_profit` | 99,528/10min (47%) | ~1,500/10min (6%) |
| 总评估量/min | ~21,000 | ~2,900 |
| 节省 | — | **86%** |

## 注意事项

- 冷却时长 300 秒足够让仓位产生新的浮盈变化，避免无限期跳过
- 冷却键用 `symbol:side` 而非 `symbol`——同币不同方向的信号应独立评估
- `import time` 需要放在方法体内（`build_signals_from_context` 已有，`_trade_gate` 需单独 import）
