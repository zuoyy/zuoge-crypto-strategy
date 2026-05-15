# 新架构下的信号数据传递与参数设计

这份文档给 `$zuoge-crypto-strategy` 使用，说明 AI 编写候选策略时，信号数据如何从全市场轻量数据一路传递到 `StrategySignalEvent`，以及 `trade_params` 应该如何填写。

当前架构下，AI 只写候选策略代码；实时运行时负责订阅、预热、发布和入库。策略代码只在 `build_signals_from_context(context)` 里返回标准 `StrategySignalEvent` 字典。

主链路：

```text
market-gateway
  -> market.*
  -> feature-engine
  -> strategy.universe.delta
  -> Strategy.discover(universe)
  -> strategy private candidate pool
  -> subscription/warmup by required_dependencies
  -> strategy.context.delta.{symbol}
  -> Strategy.build_signals_from_context(context)
  -> StrategySignalEvent
  -> strategy.signals
  -> strategyingress
  -> signal proposal
  -> risk
  -> execution
```

AI 禁止直接发布 `strategy.signals`，禁止调用风控、执行、交易所或非 Agent API。AI 的职责是让候选策略在本地 check/test/backtest 中生成正确的 candidates、signals 和 decision logs。

## 1. 两阶段策略接口

新策略必须实现：

```python
class Strategy:
    strategy_id = "..."
    strategy_version = "..."

    def discover(self, universe: dict) -> list[dict]:
        ...

    def build_signals_from_context(self, context: dict) -> list[dict]:
        ...
```

`discover(universe)` 只消费全市场轻量数据，只输出候选，不输出 signal。候选告诉系统：哪个 `symbol + side` 值得预热、需要哪些完整行情依赖、候选多久有效。

`build_signals_from_context(context)` 只消费已预热的单 symbol 完整 context。只有依赖满足、行情新鲜、账户/风险上下文允许、策略条件成立时，才返回 `StrategySignalEvent`。没有交易时返回空列表，并写 `decision_logs`。

## 2. discover 输出 candidate

候选最小结构：

```json
{
  "strategy_id": "workflow_distilled_funnel",
  "symbol": "BTCUSDT",
  "side": "long",
  "score": "73.5",
  "reason": "universe filter passed ...",
  "required_dependencies": ["l1_book", "l2_book_top", "mark_price", "ticker_24h", "kline:1m"],
  "ttl_seconds": 240
}
```

推荐使用 `strategy_sdk.candidate(...)` 生成候选，不要手写复杂 envelope。

字段规则：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `strategy_id` | 是 | 必须等于策略类上的 `strategy_id`。 |
| `symbol` | 是 | 交易所合约符号，例如 `BTCUSDT`。系统会按 symbol 聚合订阅。 |
| `side` | 是 | 只能是 `long` 或 `short`。候选池唯一键是 `(strategy_id, symbol, side)`。 |
| `score` | 是 | 候选阶段的相对评分，建议 `0..100`。 |
| `reason` | 是 | 说明为什么该 symbol/side 进入候选池。 |
| `required_dependencies` | 是 | 完整 context 需要预热的依赖。常见值见下一节。 |
| `ttl_seconds` | 是 | 候选有效期。过期后系统释放相关订阅租约。 |
| `candidate_id` | 否 | 可省略，系统可生成。 |
| `setup_id` | 否 | 同一策略内区分 setup，例如 `breakout`、`pullback`。 |
| `warmup` | 否 | 预热策略，例如最大数据年龄、K 线根数、超时时间。 |

常见 `required_dependencies`：

- `l1_book`
- `l2_book_top`
- `agg_trade`
- `mark_price`
- `ticker_24h`
- `kline:1m`
- `kline:5m`
- `kline:15m`
- `kline:1h`
- `kline:4h`

`discover()` 不允许读取完整盘口、深度、逐笔成交或 symbol 级完整 context；这些只能在预热完成后的 `build_signals_from_context()` 使用。

## 3. context 输入

`build_signals_from_context(context)` 收到的是单 symbol 的实时上下文，来自 `strategy.context.delta.{symbol}`。它包含候选、行情快照、衍生品数据、特征、账户适配信息、风险限制和实时约束。

常用字段：

| 字段 | 说明 |
| --- | --- |
| `context_id` | 本次上下文 ID，会进入 signal 的 `context_id`。 |
| `symbol` | 当前 symbol。 |
| `market_seq` | 行情序号。signal 必须携带，ingress 会拒绝太旧的序号。 |
| `computed_at` | context 计算时间。 |
| `context_status` | 通常应为 `ready`；`degraded`、`expired`、`partial` 等不应交易。 |
| `max_data_age_ms` | 允许依赖的最大年龄。 |
| `max_slippage_bps` | signal 层最大允许价格偏离，单位 bps。 |
| `candidate` | 当前候选，含 `side`、`score`、`required_dependencies`、`setup_id`。 |
| `quote_dependency` | 关键报价依赖状态，必须 fresh。 |
| `snapshot.l1` | 最佳买卖价、mid、spread。 |
| `snapshot.book_top` | 顶层深度、买卖盘不平衡。 |
| `snapshot.mark` | 标记价、资金费率。 |
| `snapshot.ticker` | 24h 行情、成交量、涨跌幅。 |
| `snapshot.klines` | 各周期 K 线。 |
| `snapshot.freshness_ms` | 各依赖的实时年龄。 |
| `microstructure` | spread、盘口不平衡等微结构特征。 |
| `trade_flow` | 逐笔成交聚合特征。 |
| `derivatives` | 合约衍生品特征，例如 funding。 |
| `ticker` | 24h ticker 派生字段。 |
| `features` | feature-engine 计算的复合特征。 |
| `strategy_id` | 当前策略视角；请求 Agent context 时必须传入。 |
| `owned_position` | 当前策略拥有的持仓；策略 close/reverse/add 只能基于它。 |
| `owner_runtime` | 当前策略拥有的 runtime 归属与状态。 |
| `foreign_owner_conflict` | 其他策略是否占用该 symbol。 |
| `strategy_account_fit` | 当前策略资金池、持仓槽位、剩余额度等策略可用信息。 |
| `risk_limits` | 运行时风险限制。 |
| `symbol_metadata` | tick size、step size、min notional 等交易规格。 |

策略进入交易判断前必须做：

1. `strategy_sdk.is_warm(context)`，确认候选声明的依赖已满足。
2. 检查 `context_status` 是否为 `ready`。
3. 检查 `quote_dependency.status` 是否为 `fresh`。
4. 从 `candidate.side` 取方向，不要自行凭空改方向。
5. 用 `strategy_sdk.price_for_side(context, side)` 取可执行参考价。
6. 对策略自有持仓、冷却、资金池敞口和行情质量做策略侧 gate；不要管理 `owner_strategy_id` 不等于当前策略的仓位。

如果任一条件不满足，返回 `[]`，并记录 `WAIT_WARMUP`、`DEGRADED_SKIP`、`NO_TRADE` 或 `VALIDATION_FAILED`。

## 4. StrategySignalEvent 顶层结构

策略返回的 signal 是 `StrategySignalEvent`。推荐用 `strategy_sdk.signal_envelope(...)` 生成，不要手写顶层 envelope。

示例：

```json
{
  "schema_version": "1.0",
  "signal_id": "sig-workflow_distilled_funnel-a1b2c3d4e5f6",
  "source": "Hermes",
  "strategy_id": "workflow_distilled_funnel",
  "strategy_version": "0.1.0",
  "context_id": "ctx-20260514010101",
  "symbol": "BTCUSDT",
  "intent": "OPEN_LONG",
  "price_ref": "63540",
  "quote_side": "ask",
  "market_seq": 123456,
  "data_freshness_ms": 42,
  "max_data_age_ms": 15000,
  "max_slippage_bps": 30,
  "signal_time": "2026-05-14T01:01:01Z",
  "expire_ms": 15000,
  "confidence": "0.82",
  "reason": "accepted_breakout long score=82.1 spread_bps=4.2",
  "trade_params": {},
  "data_dependencies": {
    "l1_book": "2026-05-14T01:01:00.958Z",
    "l2_book_top": "2026-05-14T01:01:00.965Z",
    "mark_price": "2026-05-14T01:01:00.972Z"
  },
  "trace_id": "trace-sig-workflow_distilled_funnel-a1b2c3d4e5f6"
}
```

字段规则：

| 字段 | 必填 | AI 怎么填 |
| --- | --- | --- |
| `schema_version` | 是 | 固定 `1.0`，由 `signal_envelope` 填。 |
| `signal_id` | 是 | 唯一 ID，由 `signal_envelope` 生成。 |
| `source` | 是 | 固定 `Hermes`，除非系统另有约定。 |
| `strategy_id` | 是 | 当前策略 ID。 |
| `strategy_version` | 是 | 当前策略版本。 |
| `context_id` | 是 | 从 `context.context_id` 传递。 |
| `symbol` | 是 | 从 `context.symbol` 传递。 |
| `intent` | 是 | `OPEN_LONG`、`OPEN_SHORT`、`CLOSE_LONG`、`CLOSE_SHORT`、`REVERSE_LONG`、`REVERSE_SHORT`。常规开仓/反手可用 `strategy_sdk.intent_for_side(side, context)`。 |
| `price_ref` | 是 | 当前 signal 的报价参考。多头通常 ask，空头通常 bid。 |
| `quote_side` | 是 | `bid`、`ask`、`mid` 或 `mark`。多头开仓通常 `ask`，空头开仓通常 `bid`。 |
| `market_seq` | 是 | 从 context 传递，必须大于 0。ingress 会拒绝太旧的序号。 |
| `data_freshness_ms` | 是 | 本 signal 依赖数据的最大年龄。可用 `strategy_sdk.data_freshness_ms(context)`。 |
| `max_data_age_ms` | 是 | 从 context 或策略约束传递，必须大于 0。 |
| `max_slippage_bps` | 是 | signal 层最大价格偏离，必须大于 0。 |
| `signal_time` | 是 | UTC 时间，由 `signal_envelope` 填。 |
| `expire_ms` | 是 | signal 有效毫秒数，短线常用 `15000`。 |
| `confidence` | 否 | 字符串小数或 JSON number，范围 `0..1`。省略时后端按 `0` 处理，可能被风控阈值拒绝；策略信号建议总是填写。 |
| `reason` | 是 | 简明交易理由，最多 500 字符。ingress 会映射成内部 `signal_reason`，为空会被 Go signal validator 拒绝。 |
| `trade_params` | 是 | 完整交易计划，见下文。 |
| `data_dependencies` | 否 | 依赖时间审计信息，值必须是 RFC3339 时间戳字符串；不要填毫秒 age 字符串。 |
| `trace_id` | 是 | 链路追踪 ID，由 `signal_envelope` 生成。 |
| `published_at` | 否 | 系统发布元数据，Go 合约可接收；策略代码不要手填。 |
| `nats_publish_ack_time` | 否 | 系统 NATS ack 元数据，Go 合约可接收；策略代码不要手填。 |

ingress 会用这些实时字段做最后防线：

- `quote_side` 必须合法。
- `data_freshness_ms >= 0`，`max_data_age_ms > 0`，`max_slippage_bps > 0`。
- 当前 L1 必须存在且没有超过 `max_data_age_ms`。
- 当前 `market_seq - signal.market_seq` 不能太大。
- 当前实时价格相对 `price_ref` 的偏离不能超过 `max_slippage_bps`，且如果 `trade_params.execution_constraints.max_slippage_pct` 更严格，会采用更严格值。
- 如果有 L2 top，入场方向对应的深度不能缺失。

注意：`trade-plan-signal.schema.json` 描述的是策略侧 `StrategySignalEvent`，不是 REST/manual 的旧 `TradePlanSignal` 请求。REST/manual 请求里的 `skill_name`、`skill_version`、`side`、`position_intent`、`replace_existing_position`、`created_at`、`expires_at` 等字段由 `strategyingress` 根据 `StrategySignalEvent` 映射或生成；AI 候选策略不要直接填写这些旧 proposal 顶层字段。

## 5. intent 与内部 position_intent 的映射

`StrategySignalEvent.intent` 会在 `strategyingress` 转成内部 `signal.Proposal.position_intent`：

| `intent` | `side` | 内部 `position_intent` |
| --- | --- | --- |
| `OPEN_LONG` | `long` | `open` |
| `OPEN_SHORT` | `short` | `open` |
| `CLOSE_LONG` | `long` | `close` |
| `CLOSE_SHORT` | `short` | `close` |
| `REVERSE_LONG` | `long` | `reverse` |
| `REVERSE_SHORT` | `short` | `reverse` |

策略通常不直接填写旧 proposal 顶层的 `position_intent`、`replace_existing_position`、`skill_name`、`created_at`、`expires_at`。这些由 ingress 根据 `StrategySignalEvent` 转换或生成。

如果当前 context 表示已有反向持仓，`strategy_sdk.intent_for_side(side, context)` 会生成 reverse intent。策略必须确认这是明确反手逻辑，不能因为 side 冲突就盲目交易。

## 6. trade_params 总结构

`trade_params` 是 `StrategySignalEvent` 内嵌的正式交易计划。它会被 ingress 原样解码成内部 `signal.TradeParams`，再交给 signal validator、risk 和 execution。

```json
{
  "entry": {},
  "exits": {},
  "sizing": {},
  "margin": {},
  "position_management": {},
  "execution_constraints": {}
}
```

推荐优先使用 `strategy_sdk.basic_trade_params(...)` 生成基础结构，再按策略需要微调止损、止盈、仓位和约束。不要省略 `entry`、`exits`、`sizing`、`position_management`、`execution_constraints`。

所有价格、金额、数量和比例建议用字符串小数或 JSON number；Go 端 `decimal.Decimal` 可解析字符串小数。百分比字段用小数表达：`0.02` 表示 2%，`0.003` 表示 0.3%。

## 7. entry 入场计划

`entry` 描述何时触发、用什么订单类型、有效多久。

```json
{
  "trigger": {
    "type": "breakout",
    "trigger_price": "63520"
  },
  "price": {
    "order_type": "limit",
    "limit_price": "63540",
    "acceptable_range": {
      "min": "63520",
      "max": "63620"
    }
  },
  "timing": {
    "expire_after_seconds": 900
  }
}
```

`entry.trigger.type` 只能是：

- `immediate`
- `touch_price`
- `breakout`
- `pullback_into_range`

规则：

- `touch_price` 和 `breakout` 必须提供 `trigger_price`。
- `pullback_into_range` 必须提供 `trigger_range.min` 和 `trigger_range.max`，且 `min < max`。
- `entry.price.order_type` 只能是 `market` 或 `limit`。
- `limit` 必须提供 `limit_price`。
- 必须至少提供一种价格保护：`entry.price.acceptable_range`，或正数 `execution_constraints.max_slippage_pct`。
- `entry.timing.expire_after_seconds` 必须大于 0。

方向性规则：

- `side=long` 时，`limit_price` 不能高于 `acceptable_range.max`。
- `side=short` 时，`limit_price` 不能低于 `acceptable_range.min`。

入场参考价优先级：

1. `entry.price.limit_price`
2. `entry.trigger.trigger_price`
3. `entry.price.acceptable_range` 中点
4. `entry.trigger.trigger_range` 中点
5. signal 顶层 `price_ref`

## 8. exits 退出计划

`exits` 必须包含：

```json
{
  "stop_loss": {},
  "take_profit": {},
  "trailing_stop": {},
  "time_stop": {}
}
```

### stop_loss

| 字段 | 规则 |
| --- | --- |
| `mode` | `price`、`percent`、`none`。 |
| `stop_price` | `mode=price` 时必填，必须大于 0。 |
| `loss_pct` | `mode=percent` 时必填，必须大于 0。 |

默认必须有止损。只有 close intent 或非常明确的特殊场景才使用 `none`。`sizing.mode=risk_budget` 不能搭配 `stop_loss.mode=none`。

方向性规则：

- 多头止损价必须低于入场参考价。
- 空头止损价必须高于入场参考价。

### take_profit

| 字段 | 规则 |
| --- | --- |
| `mode` | `fixed_price`、`ladder`、`none`。 |
| `targets[].price` | `fixed_price` 或 `ladder` 时必填。 |
| `targets[].close_ratio` | 必须大于 0。非最后目标的 close_ratio 总和不能超过 1。最后一个目标的 close_ratio 建议设为 `1.0`（哨兵值），映射到 Binance `ClosePosition=true` 全平剩余，不计入 sum check。`fixed_price` 模式必须 `close_ratio=1`。 |

`fixed_price` 必须正好 1 个 target，且 `close_ratio=1`。

`ladder` 至少 1 个 target，并且 `position_management.allow_partial_exit=true`。

`take_profit.mode=none` 时不要填写 `targets`；如果填写了 target，Go validator 仍会校验 `price > 0`、`close_ratio > 0` 和总和不超过 1。

方向性规则：

- 多头止盈价必须高于入场参考价，多目标价格递增。
- 空头止盈价必须低于入场参考价，多目标价格递减。

### trailing_stop

不启用时：

```json
{
  "enabled": false
}
```

启用时：

- `enabled=true`
- `activation_mode` 必须是 `immediate`、`after_profit_pct` 或 `after_tp_hit`
- `activation_mode=after_profit_pct` 时必须有 `activation_profit_pct`
- `trail_mode` 必须是 `percent` 或 `price_delta`
- `trail_value` 必须大于 0
- `step_mode=step` 时必须有正数 `step_value`
- `move_to_break_even` 可选

### time_stop

不启用时：

```json
{
  "enabled": false
}
```

启用时必须有正整数 `max_holding_minutes`。

## 9. sizing 仓位大小

`sizing` 是正式下单规模请求，不是备注。

| `mode` | 必填字段 | 说明 |
| --- | --- | --- |
| `target_notional` | `target_notional` | 策略指定目标名义金额。 |
| `risk_budget` | `target_risk_amount` | 策略指定最大可亏金额，系统按止损距离推算仓位。必须有可计算止损。 |
| `fixed_quantity` | `target_quantity` | 策略指定币数量/合约数量。 |

可选边界：

- `min_notional`
- `max_notional`
- `min_quantity`
- `max_quantity`
- `allow_downsize`

范围规则：

- `min_notional <= target_notional <= max_notional`
- `min_quantity <= target_quantity <= max_quantity`
- `target_notional <= max_notional`
- `target_quantity <= max_quantity`
- 如果允许风控缩量，建议 `allow_downsize=true`

策略应从 `context.strategy_account_fit`、`context.risk_limits` 和 `context.symbol_metadata` 推导仓位，不要写死超出策略资金池/风控能力的金额。

## 10. margin 杠杆

当前默认只支持 cross：

```json
{
  "mode": "cross",
  "leverage": "1"
}
```

规则：

- `mode=isolated` 会被当前校验拒绝。
- `open` 和 `reverse` 可以带正数 `leverage`。
- `close` 不能带 leverage。
- 如果策略省略 leverage，validator 可使用运行时默认杠杆。

## 11. position_management

```json
{
  "allow_add_position": false,
  "max_add_count": 0,
  "allow_partial_exit": true,
  "allow_reverse_on_opposite_signal": false,
  "same_symbol_cooldown_minutes": 60
}
```

字段规则：

- 普通开仓默认 `allow_add_position=false`。
- `allow_add_position=false` 时 `max_add_count=0`。
- `take_profit.mode=ladder` 时 `allow_partial_exit=true`。
- `allow_reverse_on_opposite_signal` 是未来运行时偏好，不代表当前 signal 的反手授权；当前反手由 `StrategySignalEvent.intent=REVERSE_LONG/REVERSE_SHORT` 表达。
- `same_symbol_cooldown_minutes` 是本计划退出后的同标的冷却时间。

## 12. execution_constraints

```json
{
  "max_slippage_pct": "0.003",
  "min_reward_risk": "1.5",
  "quote_staleness_seconds": 15
}
```

规则：

- 如果没有 `entry.price.acceptable_range`，必须提供正数 `max_slippage_pct`。
- `max_slippage_pct` 与 signal 顶层 `max_slippage_bps` 都会参与实时价格偏离检查；更严格者生效。
- `max_slippage_pct`、`min_reward_risk` 必须大于等于 0；作为价格保护时 `max_slippage_pct` 必须大于 0。
- `min_reward_risk` 使用第一个止盈目标和初始止损计算。
- `quote_staleness_seconds` 约束可接受报价年龄，必须大于等于 0。高频信号建议 5-30 秒。

⚠️ **close 信号必须带 execution_constraints**：即使 `stop_loss.mode=none`、`take_profit.mode=none`，close 信号（intent=CLOSE_LONG/CLOSE_SHORT）仍然需要 `execution_constraints.max_slippage_pct > 0`，否则 Go 后端会在 signal validation 阶段拒绝 `"price protection is required via acceptable_range or execution_constraints.max_slippage_pct"`。market close 信号建议 `max_slippage_pct: "0.003"`。

## 13. 推荐策略代码模式

```python
def build_signals_from_context(self, context: dict) -> list[dict]:
    self.decision_logs = []

    if not strategy_sdk.is_warm(context):
        self._log(context, "WAIT_WARMUP", "required dependencies are not warm")
        return []

    if str(context.get("context_status") or "ready").lower() != "ready":
        self._log(context, "DEGRADED_SKIP", "context is not ready")
        return []

    quote_dependency = context.get("quote_dependency") or {}
    if str(quote_dependency.get("status") or "fresh").lower() != "fresh":
        self._log(context, "DEGRADED_SKIP", "quote dependency is not fresh")
        return []

    candidate = context.get("candidate") or {}
    side = str(candidate.get("side") or "").lower()
    if side not in ("long", "short"):
        self._log(context, "VALIDATION_FAILED", "candidate side is missing")
        return []

    price = strategy_sdk.price_for_side(context, side)
    if price <= 0:
        self._log(context, "NO_TRADE", "missing executable price", side=side)
        return []

    # 策略自己的结构、动量、成交、账户和风控判断放在这里。
    if not self._setup_passed(context, side, price):
        self._log(context, "NO_TRADE", "setup not passed", side=side)
        return []

    trade_params = strategy_sdk.basic_trade_params(
        context,
        side,
        price,
        risk_pct=1.0,
        stop_pct=0.012,
        reward_risk=2.2,
        max_slippage_pct=0.003,
        quote_staleness_seconds=20,
    )

    signal = strategy_sdk.signal_envelope(
        strategy_id=self.strategy_id,
        strategy_version=self.strategy_version,
        context=context,
        side=side,
        confidence=0.78,
        reason="setup passed with fresh context and executable price",
        trade_params=trade_params,
        source="Hermes",
        expire_ms=15000,
    )
    self._log(context, "SIGNAL", signal["reason"], side=side, signal_id=signal["signal_id"])
    return [signal]
```

## 14. 提交前自检清单

AI 编写或修改策略后，必须逐项检查：

- `discover()` 只输出 candidate，不生成 signal。
- candidate 有 `strategy_id`、`symbol`、`side`、`score`、`reason`、`required_dependencies`、`ttl_seconds`。
- `required_dependencies` 覆盖策略实际读取的数据。
- `build_signals_from_context()` 先检查 warmup、context status、quote dependency。
- signal 使用 `strategy_sdk.signal_envelope(...)` 生成。
- signal 顶层有 `market_seq`、`quote_side`、`data_freshness_ms`、`max_data_age_ms`、`max_slippage_bps`。
- `price_ref` 来自当前 context 的可执行报价，不是过期 K 线价或主观价。
- `intent` 与 candidate side、当前持仓关系一致。
- `trade_params.entry` 有触发条件、订单类型、有效期和价格保护。
- `trade_params.exits` 有可计算止损；`risk_budget` 不搭配 `stop_loss.mode=none`。
- 多头止损低于入场参考价，止盈高于入场参考价；空头相反。
- ladder 止盈方向正确，最后一档 `close_ratio=1.0`（全平剩余），非最后目标 close_ratio 总和不超过 1。
- `sizing` 的目标字段与 `mode` 匹配，min/max 范围不冲突。
- `allow_add_position=false` 时 `max_add_count=0`。
- `take_profit.mode=ladder` 时 `allow_partial_exit=true`。
- `execution_constraints.max_slippage_pct` 与 signal 顶层 `max_slippage_bps` 不冲突。
- 无交易时也写 `decision_logs`，决策值使用 `NO_TRADE`、`WAIT_WARMUP`、`DEGRADED_SKIP` 或 `VALIDATION_FAILED`。

## 15. 最简口径

AI 只需要记住：

- `discover()` 负责从全市场轻量数据挑候选和声明依赖。
- 系统负责候选池、行情订阅、依赖预热和 context 投递。
- `build_signals_from_context()` 负责把已预热 context 转成 `StrategySignalEvent`。
- `StrategySignalEvent` 顶层描述实时性、方向、报价和审计；`trade_params` 描述入场、退出、仓位、杠杆、持仓管理和执行约束。
- AI 不直接提交旧 proposal，不发布 NATS，不绕过 strategyingress/risk/execution。
