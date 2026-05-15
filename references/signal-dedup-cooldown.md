# Signal 重复推送冷却模式

## 症状

同一 symbol+side 在同一秒内被推送 6-8 个重复信号（score/参数完全相同，仅 signal_id 不同）。后端全部拒绝，污染信号日志。

## 根因

`build_signals_from_context()` 每次行情刷新都被调用。同一个 candidate 的 context 更新 → 再次评估 → 再次生成相同信号 → 再次推送。策略没有内部冷却机制。

## 修复模式

在 `__init__` 中加 `_last_signal_at: dict[str, float]` 字典，key 为 `"SYMBOL:side"`：

```python
def __init__(self) -> None:
    self.decision_logs: list[dict] = []
    self._last_signal_at: dict[str, float] = {}  # "SYM:side" → timestamp

def build_signals_from_context(self, context: dict) -> list[dict]:
    import time as _time
    candidate = context.get("candidate") or {}
    _sym = str(candidate.get("symbol") or context.get("symbol") or "")
    _side = str(candidate.get("side") or "").lower()
    _key = f"{_sym}:{_side}"
    _last = self._last_signal_at.get(_key, 0)
    if _last > 0 and _time.time() - _last < 120:
        return []  # still in cooldown
    # ... normal logic ...
    # After generating signal:
    signal["intent"] = self._intent_for_owned_position(side, position)
    self._last_signal_at[_key] = _time.time()  # record cooldown timer
```

## 设计要点

- **冷却时长**：120 秒。足够覆盖 normal trade gate 周期，但不会错过真正的重新入场机会
- **close signal 豁免**：平仓信号不受冷却限制（`_maybe_close_position` 返回的 close signal 不走主信号路径）
- **key 格式**：`"ACTUSDT:short"`，区分同币种的多空方向
- **进程重启后清空**：`_last_signal_at` 是内存字典，重启后自然清空

## 验证

修复前 ACTUSDT short 在同秒内推送 8 个重复信号。修复后每个 symbol/side 每 120 秒最多 1 个信号。
