# 信号冷却竞态条件（Signal Cooldown Race Condition）

## 问题描述

NATS 异步事件循环中，多协程同时调用 `build_signals_from_context()`。冷却检查为「读旧值→判断→写新值」三步，非原子操作。当多协程在写之前同时读到旧值 `0`，全部通过冷却检查，各自发射信号。

### 生产数据证明

同一标的（`signed_change=-13.5%`）5 分钟内 8 次信号，时间差 0-19 秒，远低于 600 秒冷却：

```
11:00:37  ← 第1次
11:00:56  gap=19s  ← 应被600s冷却阻挡，未被阻挡
11:00:59  gap=3s   ← 3个信号同秒，竞态集中爆发
11:00:59  gap=0s
11:00:59  gap=0s
11:01:00  gap=1s
11:01:11  gap=11s
11:01:45  gap=34s
11:04:14  gap=149s
11:05:02  gap=48s
```

### 诊断 SQL

```sql
WITH signal_times AS (
  SELECT created_at,
         LAG(created_at) OVER (ORDER BY created_at) as prev_at
  FROM strategy_decision_logs
  WHERE strategy_id='workflow_distilled_funnel'
    AND decision='SIGNAL'
    AND created_at > now() - interval '10 minutes'
)
SELECT prev_at, created_at,
       EXTRACT(EPOCH FROM created_at - prev_at)::int as gap_seconds
FROM signal_times WHERE prev_at IS NOT NULL
ORDER BY created_at;
```

期望：所有信号间隙 > 安全阈值（per-key 120s 或 per-symbol 600s）。如果频繁出现 <120s 间隙，冷却失效。

## 根因

NATS 的异步消息处理 + Python 协程 = 非线程安全的实例变量读写：

```python
# 协程A                     # 协程B
_sym_last = cache.get(s)    # → 0
                            # _sym_last = cache.get(s)  # → 0（同一时刻）
if now - _sym_last < 600:   # → False（通过）
                            # if now - _sym_last < 600: # → False（也通过！）
# ... 完整上下文评估 ...     # ... 完整上下文评估 ...
cache[s] = now              # ← 写入
                            # cache[s] = now           # ← 也写入
```

两个信号都发射了。

## 修复

使用 `threading.Lock`（不是 `asyncio.Lock`——策略代码是同步的，锁的调用方可能是线程池或协程）：

```python
import threading

class Strategy:
    def __init__(self):
        ...
        self._signal_lock = threading.Lock()

    def build_signals_from_context(self, context: dict) -> list[dict]:
        ...
        with self._signal_lock:
            _last = self._last_signal_at.get(_key, 0)
            if _last > 0 and _time.time() - _last < 120:
                return []
            _sym_last = self._last_signal_symbol_at.get(_sym, 0)
            if _sym_last > 0 and _time.time() - _sym_last < 600:
                return []

        # ... 完整评估（不持锁，允许并发评估不同 symbol）...

        with self._signal_lock:
            self._last_signal_at[_key] = _time.time()
            self._last_signal_symbol_at[_sym] = _time.time()
```

**为什么不直接用锁包整个函数？** 锁在检查-写之间释放，允许多个 symbol 并发评估。只有在「检查」和「记录」两步时需要原子性，中间的耗时评估不需要。

## 验证方法

部署后等待 10 分钟，重新运行诊断 SQL。如果所有信号间隙 >120s（per-key 冷却生效），修复成功。正确结果示例：

```
gap_seconds
-----------
        152  ← per-symbol 冷却碰到的另一个标的（正常）
        390
        248
         25  ← 不同标的，正常
```

所有间隙 >120 即无竞态问题。
