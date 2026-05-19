# discover() 持仓盲视与修复模式

## 问题描述

`discover(universe)` 只接收 `universe` 字典（全市场轻量数据），**无法查询当前持仓**。持仓信息只在 `build_signals_from_context(context)` 中通过 `context["owned_position"]` 和 `context["strategy_positions"]` 可用。

后果：已持有同方向仓位的 symbol 持续被 `discover()` 选出 candidate → 进入候选池 → 每15-60s context delta 触发评估 → `_trade_gate()` 正确拒绝（`same_side_add_disabled_by_risk_config`）→ 重复5-20次直到 TTL 过期。

## 诊断信号

用户问「为什么还反复再推 / 同一个币反复出信号 / 一直NO_TRADE」:

1. 查 `strategy_decision_logs` 中该 symbol 的 reason 分布
2. 如果全是 `same_side_add_disabled_by_risk_config` / `add_requires_min_float_profit` / `add_in_cooldown` → discover() 没有过滤已持仓 symbol
3. 确认 `_positions_cache` 是否为空（首次 context 评估前为空）

## 修复模式

### 模式 A：读取 `_positions_cache`（最小改动）

⚠️ **注意 `max_add_count` 意识**：如果后端允许加仓（`max_add_count > 0`），不应该跳过该 symbol — 让 candidate 通过，交给 _trade_gate 的 7 层加仓门禁去判断。

```python
def _candidate_already_held(self, symbol: str, side: str) -> bool:
    cache_ts, cache_data, cache_fit = self._positions_cache
    if not cache_data:
        return False  # cache empty → first evaluation not yet happened
    # If backend allows adds, don't skip — let the add gate evaluate
    max_add = int(strategy_sdk.number(cache_fit.get("max_add_count"), 0))
    if max_add > 0:
        return False
    for pos in cache_data:
        ps = str(pos.get("symbol") or "").upper()
        pd = str(pos.get("side") or pos.get("position_side") or "").lower()
        if ps == symbol and pd == side:
            return True
    return False
```

在 `discover()` 的 `for side, ...` 循环内插入：

```python
for side, setup_id, setup_bias in side_rows:
    # ── Position-aware candidate skip: same symbol+side already held ──
    if self._candidate_already_held(symbol, side):
        continue
```

**时序问题**：`_positions_cache` 首次为空 → 第一个 discover 周期仍会产出 candidate → 第一次 context 评估后 cache 被填充 → 后续 discover 周期正确跳过。一次浪费可接受。

### 模式 B：给 universe 添加持仓字段（架构级）

让系统在 universe.delta 中注入策略持仓信息（如 `universe["strategy_positions"]`），discover() 直接读取。需改 runtime 代码，影响更大。

### 模式 C：候选池到期后不续期

当 `_trade_gate()` 因同方向持仓拒绝候选时，设 `candidate_ttl = 0` 或降低 score 使其提前过期。需系统级支持。

## 相关缓存问题

### 缓存时间戳

`_fetch_strategy_positions()` 缓存时间戳应使用 `_time.time()` 而非传入的 `cache_ts`：

```python
# 错误：cache_ts 是旧时间，写入后缓存看起来永远新鲜
self._positions_cache = (cache_ts, items)

# 正确：用当前时间标记缓存新鲜度
self._positions_cache = (_time.time(), items)
```

### 缓存 3-tuple 架构

从 v0.1.0 开始 `_positions_cache` 改为 3-tuple 以携带 `strategy_fit`：

```python
self._positions_cache: tuple[float, list[dict], dict] = (0.0, [], {})  # ts, positions, strategy_fit
```

写入时顺便缓存 `strategy_account_fit`：

```python
self._positions_cache = (_time.time(), items, self._strategy_account_fit(context))
```

这使 `_candidate_already_held()` 可以访问 `max_add_count` 等后端参数。

### 缓存为空时序

首次启动时 cache_data=[] → `_candidate_already_held()` 返回 False → 第一个 discover 周期仍产出 candidate → 第一次 context 评估后 cache 被填充 → 后续 discover 正确过滤。

部署后观察 5-10 分钟，确认已持仓 symbol 的 decision_logs 数量骤降：

```sql
SELECT symbol, reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id='workflow_distilled_funnel'
  AND created_at > now() - interval '5 minutes'
GROUP BY symbol, reason
ORDER BY symbol, cnt DESC;
```

已持仓 symbol 应在 discover 阶段被跳过 → decision_logs 中不再出现该 symbol 的 NO_TRADE 记录。
