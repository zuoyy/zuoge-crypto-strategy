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

## 选择被替换持仓

遍历 `GET /api/v1/agent/positions?strategy_id=<id>` 返回的所有持仓：

| 条件 | 逻辑 |
|------|------|
| pnl_pct < 0.5% | 跳过（利润太薄或亏损，不割肉） |
| pnl_pct ≥ 2.5% | 跳过（让利润奔跑，不换大赢） |
| 0.5% ≤ pnl_pct < 2.5% | 候选，选 pnl_pct 最小的 |

同 symbol 的持仓直接跳过（走加仓逻辑而非轮换）。

## 轮换执行

1. 生成平仓信号：市价关被选中持仓的**全部数量**
2. 信号列表返回 `[rotation_close_signal, new_open_signal]`
3. CLOSE 信号的 `intent = CLOSE_<side>`，`symbol` 覆写为目标 symbol

## 安全边界

| 规则 | 说明 |
|------|------|
| 不割肉 | pnl < 0.5% 不碰，浮亏持仓不会被轮换 |
| 不换大赢 | pnl ≥ 2.5% 让利润奔跑 |
| 不替己 | 同 symbol 走 add gate |
| 不 flooding | `_fetch_strategy_positions()` 有 15s TTL 缓存 |
| API 故障容错 | `_fetch_strategy_positions()` 失败时返回过期缓存，不阻塞信号 |

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
