# 仓位轮换 —— 落袋为安

## 设计哲学

**落袋为安是第一原则。** 当策略持仓满且出现高质量新信号时，主动止盈最弱的浮盈持仓来释放仓位 slot，而不是死守小盈让强信号流失。

不要因为"已经持有"就拒绝更好的机会——小幅盈利的持仓占用 slot 的代价比提前止盈更大。

## 触发条件

```
slots_remaining ≤ 0        # 策略满仓，无空余 slot
  AND score ≥ 85           # 新信号足够强，值得替换
  AND 非加仓场景            # 同向走 add，不触发轮换
  AND 存在可替换持仓        # 浮盈 0.5%~2.5% 的小盈仓位
```

## 选择被替换持仓（动态优先级）

遍历 `GET /api/v1/agent/positions?strategy_id=<id>` 返回的所有持仓：

**两阶段选择（始终换最弱，不写死阈值）：**

| 阶段 | 条件 | 逻辑 |
|------|------|------|
| 1 | 收集所有浮盈 ≥ 0.5% 的持仓 | 排除亏损和微利 |
| 2a | 存在 pnl < 2.5% 的小盈持仓 | 选其中 pnl% 最小的（优先止小盈） |
| 2b | 全部 pnl ≥ 2.5% | 选所有浮盈中 pnl% 最小的（即使 +5%） |

同 symbol 的持仓直接跳过（走加仓逻辑而非轮换）。

**核心原则**：始终换最弱的。如果全都是大盈，就换相对最弱的那个。不因固定阈值而拒绝轮换。

## 轮换执行

1. 生成平仓信号：市价关被选中持仓的**全部数量**
2. 信号列表返回 `[rotation_close_signal, new_open_signal]`
3. CLOSE 信号的 `intent = CLOSE_<side>`，`symbol` 覆写为目标 symbol

⚠️ **关键要求**：`max_signals_per_candidate` 必须 ≥ 2，否则框架 `[:signal_limit]` 会截断双信号列表，丢弃开仓信号。manifest 中设置 `"max_signals_per_candidate": 2`。

## 安全边界

| 规则 | 说明 |
|------|------|
| 不割肉 | pnl < 0.5% 不碰，浮亏持仓不会被轮换 |
| 不换大赢 | pnl ≥ 2.5% 让利润奔跑 |
| 不替己 | 同 symbol 走 add gate |
| 不 flooding | `_fetch_strategy_positions()` 有 15s TTL 缓存 |
| API 故障容错 | `_fetch_strategy_positions()` 失败时返回过期缓存，不阻塞信号 |
| manifest 双信号 | `max_signals_per_candidate: 2` 防止轮换截断 |

## 已知陷阱

### 1. cross-symbol price_ref 错位（严重）

`_build_rotation_close` 过去调用 `signal_envelope(context=当前context)`，但当前 context 是**新信号 symbol** 的数据（如 COINUSDT），而平仓信号需要关闭**另一个 symbol**（如 GTCUSDT）。

**后果**：`signal_envelope` 从当前 context 计算 `price_ref`（COINUSDT 价格），Go 后端收到后比对 GTCUSDT 市价 → `price_deviation_exceeded` 拒绝。决策日志有 ROTATE，但 signals 表无对应记录。

**修复**：不使用 `signal_envelope`，手动构建平仓信号 dict，`price_ref` 从 `pos.mark_price`（positions API 返回的持仓市价）取值，fallback `pos.avg_entry_price`。

### 2. signal_limit 截断

`realitime_main.py` 的 `_remaining_signal_limit` 对所有信号列表做 `[:signal_limit]` 截断。当 `max_signals_per_candidate=1` 时，`[rotation_close, main_signal]` → 只发第一个（close），开仓信号被丢弃。

**修复**：manifest 设置 `max_signals_per_candidate: 2`。

### 3. execution_constraints 缺失与错放层级

market close 信号（stop_loss.mode=none, take_profit.mode=none）**仍然需要** `execution_constraints.max_slippage_pct`，否则 Go backend 拒绝：
> `"price protection is required via acceptable_range or execution_constraints.max_slippage_pct"`

⚠️ **关键陷阱**：`execution_constraints` 必须放在 **`trade_params` 内部**，不是信号顶层。`basic_trade_params()` 返回的结构是 `trade_params.execution_constraints`，Go backend 在 trade_params 内部查找。放在信号顶层的 `execution_constraints` 会被忽略。

**正确结构**（手动构建时）：
```json
{
  "trade_params": {
    "entry": { ... },
    "exits": { ... },
    "sizing": { ... },
    "execution_constraints": {           // ← 在 trade_params 里面！
      "max_slippage_pct": "0.003",
      "min_reward_risk": "1.0",
      "quote_staleness_seconds": 20
    }
  }
}
```

**错误**（信号顶层，backend 不认）：
```json
{
  "trade_params": { ... },
  "execution_constraints": { ... }       // ← 信号顶层，被忽略！
}
```

排查：所有 `acceptable_range: price protection required` 拒绝且 signal_reason 不包含 `rotation` 的，极大概率是 placement 错误。

### 4. 排查步骤

当怀疑轮换不工作时，按以下链路逐级验证：

```
decision_logs (ROTATE?) → signals 表 (close signal?) → strategy_signal_rejects (price_deviation?) → position_plan_runtimes (持仓仍在?)
```

```sql
-- 1. 查是否有轮换决策
SELECT reason, created_at FROM strategy_decision_logs
WHERE reason LIKE '%ROTATE%' ORDER BY created_at DESC LIMIT 5;

-- 2. 查平仓信号是否入库
SELECT signal_id, symbol, status, signal_reason FROM signals
WHERE strategy_id = 'workflow_distilled_funnel'
  AND (signal_reason LIKE '%rotation%' OR signal_reason LIKE '%close%')
ORDER BY created_at DESC LIMIT 10;

-- 3. 平仓信号被拒绝的原因（重点关注 price_deviation_exceeded）
SELECT reason_code, reason, rejected_at, signal_id
FROM strategy_signal_rejects
WHERE rejected_at > NOW() - INTERVAL '4 hours'
  AND reason_code = 'price_deviation_exceeded'
ORDER BY rejected_at DESC LIMIT 10;

-- 4. 目标持仓是否仍在
SELECT symbol, runtime_status, remaining_position_ratio
FROM position_plan_runtimes WHERE runtime_status = 'active';
```

## 代码位置

- `_fetch_strategy_positions()` — 调用 Agent API 获取全策略持仓，15s 缓存
- `_maybe_rotate_position()` — 轮换评估逻辑
- `_build_rotation_close()` — 构建平仓信号
- `build_signals_from_context()` 中 `return [signal]` 前调用 `_maybe_rotate_position()`

## context overlay 依赖

需要 `context.strategy_account_fit.open_position_slots_remaining` 和 `context.strategy_account_fit.max_add_count`。无需 backend 额外改动，因为 `slots_remaining` 和 `max_add_count` 已在 context 中。

## 与加仓的关系

| 场景 | 行为 |
|------|------|
| 新 symbol + 有 slot | 正常开仓 |
| 新 symbol + 满 slot | 轮换（关最弱止盈 + 开新） |
| 同 symbol 同 side | 加仓（走 7 层 add gate） |
| 同 symbol 反 side | 反手（走 reversal gate） |
