---
name: zuoge-crypto-strategy
description: "用于编写、校验、回测并自动投递实时策略候选到生产收件箱；生产审批和启用由人工完成。"
---

# zuoge-crypto-strategy

本技能面向渐进式漏斗实时策略架构。策略分两阶段：

- `discover(universe)` 消费 `strategy.universe.delta` 的全市场轻量数据，只输出 side-aware candidates。
- `build_signals_from_context(context)` 消费已预热的 `strategy.context.delta.{symbol}`，只在完整行情依赖满足后输出标准 `StrategySignalEvent`。

候选池由系统托管但按策略隔离，候选唯一键为 `(strategy_id, symbol, side)`。同一 symbol 可以同时被不同策略或不同方向选中；完整行情订阅由系统按 symbol/dependency 合并，方向冲突交给 risk/execution。

## 开始前必须先做

1. 确认项目根目录。优先使用当前工作区；否则读取 `ZUOGE_CRYPTO_PROJECT_ROOT`；目录内必须存在 `cmd/crypto-skill/main.go` 和 `strategy/runtime/strategy_sdk.py`。
2. 切换到项目根目录工作。候选策略登记、检查、测试、回测、报告和候选包投递都通过 `crypto-skill` 操作；本地阶段使用本地源码目录和本地开发数据库。
3. 本地候选操作不要求 API 服务启动；如果当前 shell 没有 `DATABASE_URL`，`crypto-skill` 会读取项目根目录 `.env.dev`。默认只允许连接库名以 `_dev` 结尾的开发库。
4. 只有查询实时能力目录、实时 context、订阅状态、决策日志这类运行态信息时，才调用 Agent API。

调用 API 时只能使用 `/api/v1/agent/...` 路由。不要使用网页控制台 cookie，不要调用普通 `/api/v1/...` 路由。

## 可移植安装

本技能可以复制到 Codex、Claude、Cursor 或其他工具的技能目录使用。安装位置不要求在项目仓库内，但执行策略任务时必须进入项目根目录，且命令 `crypto-skill` 必须可用；如不可用，使用 `go run ./cmd/crypto-skill ...` 等价执行。

所有相对引用都以本技能目录为基准读取，例如 `references/`、`templates/`、`generated/`。不要把技能安装目录误认为项目根目录。

## 工作流

在项目根目录内工作：

1. 需要运行态能力目录时，`crypto-skill capabilities show --format json`。
2. 按需读取 [references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)。
3. `crypto-skill research create --focus "<研究目标>"`
4. 基于 capabilities 和 SDK 编写候选策略到 `strategy/strategies/candidates/`。
5. `crypto-skill candidate new --strategy <id> --version <x.y.z> --file <candidate.py> --research <research_id>`
6. `crypto-skill candidate check --candidate <candidate_id>`
7. `crypto-skill candidate test --candidate <candidate_id>`
8. `crypto-skill candidate backtest --candidate <candidate_id>`
9. `crypto-skill candidate report --candidate <candidate_id>`
10. 只有 check/test/backtest 都通过时，`crypto-skill candidate publish --candidate <candidate_id>`，把候选包投递到生产 Strategy Center 收件箱，然后停止等待人工审批。

`candidate new` 只用于首次把候选策略文件登记到策略中心并取得 `candidate_id`。如果用户要求检查、测试或回测一个已经登记过的策略，先用 `crypto-skill candidate list` 或 `crypto-skill candidate show --candidate <candidate_id>` 找到已有 `candidate_id`，然后直接运行 `check`、`test`、`backtest`、`report`；不要为同一个 `strategy_id` 和 `version` 重复执行 `candidate new`。

## 自动投递候选包到生产

默认完成校验和报告后执行：

```bash
crypto-skill candidate publish --candidate <candidate_id>
```

该命令读取 `ZUOGE_CRYPTO_BASE_URL` 和 `ZUOGE_CRYPTO_PUBLISH_TOKEN`。投递只把候选包送到生产收件箱，不会审批、不会启用、不会写 enabled、不会触发下单。投递成功后停止，由人在生产 Strategy Center 提交/审批 review。

若用户只要求离线交付候选包，可执行：

```bash
crypto-skill candidate export --candidate <candidate_id> --output tmp/ai-skill/<name>.json
```

## 发布当前策略目录

只有当用户明确要求“发布当前策略目录”“更新生产策略目录”或“执行策略目录发布”时，才执行本节。

发布含义：

- 只发布项目根目录下当前 `strategy/` 目录。
- 使用仓库内既有生产脚本，不手写替代发布流程。
- 不审批候选策略，不启用候选策略，不绕过策略中心。
- 不携带或覆盖生产持久 enabled 策略状态。
- 不调用风控、执行、交易所或下单接口。
- 不需要读取策略能力目录，也不需要调用 Agent API。

执行前必须确认：

1. 当前目录是项目根目录，且存在 `cmd/crypto-skill/main.go`、`deploy/macos/deploy-strategy.sh`、`strategy/realtime_main.py`。
2. 工作区没有会被误当作本次发布内容的未知策略改动；如有不确定改动，先向用户说明。
3. 用户的请求目标确实是发布当前 `strategy/` 目录，而不是提交候选评审或启用某个候选。

执行命令：

```bash
crypto-skill strategy deploy-current
```

该命令会通过仓库内既有生产脚本创建新的生产发布目录、同步当前 `strategy/` 运行时代码、检查 Python 策略依赖，并刷新 `com.crypto-trader.realtime-strategy` 服务。生产 enabled 策略从持久目录加载，不随 release 目录切换。发布完成后，向用户报告发布编号、当前发布链接和命令输出里的日志路径。

**sudo 不可用时的后备方案**：`deploy-current` 需要 sudo 操作 launchd。若 sudo 不可用，可直接复制修改的文件到当前 release 目录，然后 kill 进程让 launchd 自动重启：

```bash
cp strategy/runtime/strategy_sdk.py /opt/homebrew/var/crypto-trader/current/strategy/runtime/
kill $(pgrep -f realtime_main | head -1)   # KeepAlive=true → 自动重启
```

## Telegram 策略简报开关

当用户要求开启、关闭或查看 Telegram 策略运行简报时，只通过 `crypto-skill strategy telegram ...` 控制，不手工编辑环境文件。

```bash
crypto-skill strategy telegram status
crypto-skill strategy telegram enable
crypto-skill strategy telegram disable
```

该命令通过 Agent API 更新数据库里的系统设置 `telegram_strategy_report_enabled`，运行中的 realtime-strategy 会轮询系统设置并实时生效，不需要重启。

即使开关已开启，如果当前没有加载到运行中的 enabled 策略，系统也不会推送空的 Telegram 简报。

## 策略标准

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

`discover()` 规则：

- 只能使用 all-market 轻量字段，例如 quote volume、24h change、mark price、funding rate。
- 只能返回 candidate，不能生成 signal。
- 每个 candidate 必须包含 `strategy_id`, `symbol`, `side`, `score`, `reason`, `required_dependencies`, `ttl_seconds`。
- `side` 只能是 `long` 或 `short`。
- 完整行情依赖必须通过 `required_dependencies` 声明，例如 `l1_book`, `l2_book_top`, `mark_price`, `agg_trade`, `kline:1m`。
- 可选声明 `candidate_id`、`setup_id` 和 `warmup`；系统会按 `(strategy_id, symbol, side)` 管理候选状态。

`build_signals_from_context()` 规则：

- 必须先用 `strategy_sdk.is_warm(context)` 或等价逻辑确认依赖满足。
- 运行时只会在候选状态 ready 后调用；策略仍必须二次防御。
- 只能基于完整 symbol context 生成 signal。
- 信号必须包含实时字段：`market_seq`, `quote_side`, `data_freshness_ms`, `max_data_age_ms`, `max_slippage_bps`。
- 必须使用 `strategy/runtime/strategy_sdk.py`，不要手写复杂 signal envelope。
- 必须记录 `decision_logs`，无交易也要明确 `NO_TRADE`、`WAIT_WARMUP`、`DEGRADED_SKIP` 或 `VALIDATION_FAILED` 原因。

当任务涉及组装、解释或校验 `StrategySignalEvent` / `trade_params` 时，先读取 [references/trade-plan-signal-parameter-design.md](references/trade-plan-signal-parameter-design.md)。该文档说明新架构下信号数据从 `discover(universe)`、候选池、依赖预热、`strategy.context.delta.{symbol}` 到 `build_signals_from_context(context)` 和 `strategy.signals` 的传递顺序，以及 entry/exits/sizing/margin/position management/execution constraints 的填写规则。机器可读 schema 存放在 [generated/trade-plan-signal.schema.json](generated/trade-plan-signal.schema.json)。

## 信号提交表单实战坑位

修改已登记候选策略以适配 `StrategySignalEvent.trade_params` 表单时，除读取 `references/trade-plan-signal-parameter-design.md` 外，还要做一次 schema 级清理：

- `trade_params` 是严格表单结构；不要在 `sizing`、`entry`、`exits`、`position_management`、`execution_constraints` 内保留 schema 未声明的辅助字段。
- 如果使用 `sizing.mode="risk_budget"`，必须保留正数 `target_risk_amount`，并删除策略内部备注字段如 `risk_pct`、`stop_pct`；这些不是正式提交表单字段。
- `entry.price.acceptable_range` 是最稳的价格保护；即使有 `execution_constraints.max_slippage_pct`，短线策略也建议显式填写 `acceptable_range.min/max`。
- 限价入场必须同时填 `entry.price.limit_price`，并保证 long 时不高于 `acceptable_range.max`、short 时不低于 `acceptable_range.min`。
- `trailing_stop.activation_profit_pct`、`trail_value`、`execution_constraints.max_slippage_pct` 等百分比字段使用小数比例：`0.012` 表示 1.2%，不要写成 `1.2`。
- ladder 止盈时必须设置 `position_management.allow_partial_exit=true`；普通开仓默认 `allow_add_position=false` 且 `max_add_count=0`。
- `allow_reverse_on_opposite_signal` 只是未来偏好，不代表本次反手授权；保守策略默认 `false`，真正反手由 signal 顶层 `intent=REVERSE_*` 表达。
- `expire_ms` 默认值改为 **60000（60s）**；原 15000（15s）在 NATS 投递 + ingress 消费 + 行情校验链路中经常不够，导致 `market_seq_too_old` 和 `signal_expired`。SDK `signal_envelope()` 已更新默认值。
- **杠杆**：不再硬编码。`basic_trade_params()` 调用 `pick_leverage(context, *, volatility_pct, score, stage)` 在 `context.risk_limits` 的 `[min_leverage, max_leverage]` 范围内按币种动态选杠杆。保守阶段（sweep/reversal/neutral）用最小杠杆；高分+低波动→加杠杆；高波动→降杠杆。策略可通过 `leverage_score`/`leverage_stage`/`leverage_vol_pct` 参数传递信号上下文。后端必须在 `strategyRiskLimits` 暴露 `min_leverage`/`max_leverage` 字段（从 `risk.Limits` 读取），否则 `pick_leverage()` fallback 5x–20x。**杠杆必须为整数**（Binance 要求），`pick_leverage()` 返回 `int`，`margin.leverage` 强制 `str(int(...))`。
- **盈亏比与量化**：超低价币 tick_size 粗于价格波动时，`quantize_price()` 可能同时破坏止损和止盈方向/比例。SDK `_quantized_or_fallback()` 新增 `min_above`/`max_below`/`min_value` 三个边界保护，`basic_trade_params()` 使用 `actual_risk`（量化后真实止损距离）而非公式距离计算 TP。详见 [references/price-quantization-pitfalls.md](references/price-quantization-pitfalls.md)。\n  - **⚠️ TP 阶梯崩溃**：`tp1_ratio = max(reward_risk, 1.5)` 在 `reward_risk ≥ 1.5` 时等于 `reward_risk`，导致 TP1 == TP2。修复：`tp1_ratio = 1.5` 固定近端；`min_reward_risk = \"1.5\"` 匹配 Go 校验。这**不是量化问题**，是纯逻辑 bug，详见 [references/price-quantization-pitfalls.md](references/price-quantization-pitfalls.md) 模式 4。
- **持仓感知**：策略不应盲开仓。SDK 提供 `position_snapshot(context)` 提取持仓完整信息（side/qty/entry_price/unrealized_pnl/notional）。策略必须在 `build_signals_from_context()` 中读取持仓，在 `_trade_gate` 中做：同向持仓→允许加仓但检查敞口和浮亏；反向持仓→仅强 setup+高分允许反手。详见 [references/position-aware-trading-plan.md](references/position-aware-trading-plan.md)。
- **`max_notional` 瓶颈（risk_budget 模式）**：`basic_trade_params()` 按现金口径（`equity * pct / 100`）算 `max_notional`（如 $50），但 Go 后端 `computeSizing()` 在 risk_budget 模式下算出 `notional = target_risk_amount / stop_pct`（如 $4,180）后，会用 `max_notional` 做硬上限（line 404）。结果 $4,180 被压回 $50，下单量极小。修复：切到 risk_budget 后，**必须重新计算 `max_notional`**，至少设为 `risk_amount / stop_pct * 1.3`，上限 `equity * leverage * max_symbol_exposure_pct / 100`。详见 [references/risk-budget-sizing-pitfall.md](references/risk-budget-sizing-pitfall.md)。

推荐在候选策略里集中放一个 `_apply_signal_form_contract(...)`/同类函数，统一补齐 `entry`、清理 `sizing`、规范 `position_management` 和 `execution_constraints`，再调用 `strategy_sdk.signal_envelope(...)`。

## 持仓感知交易计划

策略的 `build_signals_from_context()` 收到 `context` 后，**必须先读取持仓信息**再做交易决策。系统已在 context 中暴露：

| 字段 | 来源 | 内容 |
|------|------|------|
| `context.position` | `strategyPositionSnapshot` | 完整持仓快照：side / qty / entry_price / unrealized_pnl / notional / leverage |
| `context.account_fit` | `strategyAccountFit` | 账户适配摘要：remaining budgets / exposure / open slots |
| `context.risk_limits` | `strategyRiskLimits` | 运行时风控：min_leverage / max_leverage / max_order_notional_pct 等 |

### 标准模式

```python
def build_signals_from_context(self, context: dict) -> list[dict]:
    # … warmup / status / quote checks …

    # 1. 读持仓
    position = strategy_sdk.position_snapshot(context)

    # 2. 读标的行情
    price = strategy_sdk.price_for_side(context, side)

    # 3. 策略评分（市场结构，不涉及持仓）
    state = self._evaluate_context(context, side, candidate, price)

    # 4. 交易门控（市场 + 账户 + 持仓三重检查）
    ok, reason = self._trade_gate(state, context, position)
    if not ok:
        return []  # + decision_log

    # 5. 仓位预算（同向加仓时减半）
    risk_pct = self._risk_budget_pct(context, state, position)

    # 6. 交易参数（含动态杠杆）
    trade_params = strategy_sdk.basic_trade_params(
        context, side, price,
        # … stop/TP/slippage …
        leverage_score=state["score"],
        leverage_stage=state["stage"],
        leverage_vol_pct=abs(state.get("signed_change", 3.0)),
    )

    # 7. 表单规整（含持仓感知的 position_management）
    self._apply_signal_form_contract(trade_params, context, state, side, price, risk_pct, position)
    # … signal_envelope …
```

### 持仓冲突门控规则

在 `_trade_gate()` 中按以下优先级判断：

1. **无持仓** → 正常开仓，走原来的市场结构门控。

2. **同向持仓**（position.side == signal.side）：
   - 允许加仓，但必须检查：
     - `symbol_exposure_pct < max_symbol_exposure_pct * 0.75`，否则拒绝 `same_side_already_near_max_exposure`
     - 浮亏不超过 notional 的 3%，否则拒绝 `do_not_add_to_losing_position`
   - `_risk_budget_pct()` 将 budget 减半（`* 0.5`），`_account_gate()` 地板从 0.25 降到 0.15
   - `position_management.allow_add_position = true`，`max_add_count = 1`，`cooldown` 缩短到 15min

3. **反向持仓**（position.side != signal.side）：
   - 只允许强 setup 反手：stage ∈ {accepted_breakout, sweep_reclaim, low_reversal_long, high_reversal_short}
   - 且 score ≥ 78
   - signal intent 由 `strategy_sdk.intent_for_side()` 自动生成 `REVERSE_*`
   - `position_management.allow_reverse_on_opposite_signal = true`

### SDK 持仓工具

| 函数 | 返回 |
|------|------|
| `position_snapshot(context)` | `{side, qty, quantity, entry_price, notional, unrealized_pnl, leverage, margin_type, has_position}` |
| `pick_leverage(context, *, volatility_pct, score, stage)` | `float` — 在 `[min_leverage, max_leverage]` 范围内按风险画像选杠杆 |

详见 [references/position-aware-trading-plan.md](references/position-aware-trading-plan.md)。

## 禁止

- 不写 `strategy/strategies/enabled/`。
- 不审批候选、不启用候选、不禁用策略、不执行实盘部署开关。
- 不调用生产 approve、deploy、disable 路由；允许的生产写入仅限 `crypto-skill candidate publish` 投递候选包。
- 除“发布当前策略目录”一节允许的生产脚本外，不执行其他发布命令。
- 不发布 `strategy.signals`，不调用 NATS publish。
- 策略代码内不访问网络、数据库、交易所、文件系统、subprocess。
- 不读 `.env`、secrets、keys。
- 不直接调用 risk/execution/exchange API。
- 不在 `discover()` 里访问完整盘口、深度、逐笔成交或生成 signal。

## 可观测接口

- `GET /api/v1/agent/market/subscriptions` 查看 active leases、聚合 streams 和 warmup 状态。
- `GET /api/v1/agent/strategy/candidates` 查看策略私有候选池状态。

## 信号推送故障排查

当用户反馈“策略发了信号但后端没收到”时，按以下链路逐级排查。不要跳步猜测。

### 1. 查链路四段

```
Python strategy → NATS (strategy.signals) → Go ingress → PostgreSQL
```

每段单独验证。

### 2. Python 侧 — 信号是否成功生成并发布

```bash
# 看 realtime-strategy 日志里有没有 {"published": ...}
grep '"published"' /opt/homebrew/var/crypto-trader/logs/realtime-strategy.stdout.log | tail -5
```

如果没有 `published`，看有没有 `VALIDATION_FAILED`、`NO_TRADE` 或 `strategy_context_overlay_failed`。

### 3. NATS 侧 — 消息是否进 stream

```bash
# 查看 stream 消息数
nats stream ls

# 查 subject 分布（strategy.signals vs strategy.signals.dead）
nats stream subjects STRATEGY_SIGNALS

# 查看 consumer 状态（已消费数、未处理数、等待 pull 数）
nats consumer info STRATEGY_SIGNALS strategy-ingress
```

关键信号：
- `strategy.signals` 和 `strategy.signals.dead` 消息数接近 → 大量信号被拒
- consumer `Unprocessed: 0` 但 stream 有新消息 → consumer 可能已停止消费
- `Waiting Pulls: 1` → ingress 正在等待，正常

### 4. Go 侧 — 看 reject 表

```sql
-- ⚠️ 坑：worker 用 production 库，不是 dev 库
-- Worker env: DATABASE_URL=postgres://.../crypto_trader
-- Dev env:    DATABASE_URL=postgres://.../crypto_trader_dev

SELECT reason_code, COUNT(*) as cnt
FROM strategy_signal_rejects
GROUP BY reason_code ORDER BY cnt DESC;

-- 看具体 reject 内容
SELECT reason_code, reason, signal_id, payload_json::text
FROM strategy_signal_rejects
ORDER BY rejected_at DESC LIMIT 3;
```

### 5. 常见 reject 原因及修复

| reason_code | 含义 | 修复方向 |
|---|---|---|
| `invalid_json` | `data_dependencies` 字段类型不匹配 | 见下方专项说明 |
| `signal_validation_failed` | strategy_id 未注册 或 stop_price=0 | 注册策略；见下方止损量化坑 |
| `market_seq_too_old` | 行情序列号已过期 | 加大 `expire_ms`（推荐 60s），优化 ingress 消费延迟 |
| `price_deviation_exceeded` | 价格偏离超限 | 调整 `max_slippage_bps` 或入场价 |
| `min_leverage` | 杠杆低于系统最小允许值 | 确认后端 `strategyRiskLimits` 暴露了 `min_leverage`；SDK `pick_leverage()` 确保了 ≥ min_leverage |
| `min_reward_risk` | 盈亏比低于执行门槛 | 检查量化是否吃掉 TP 距离（见价格量化陷阱）；确认 `tp1_ratio ≥ execution_constraints.min_reward_risk` |

### 6. ⚠️ `data_dependencies` 格式坑

信号 JSON 中 `data_dependencies` 字段的值**必须是 RFC3339 时间戳字符串**，不能是整数。

Go struct 定义：`DataDependencies map[string]time.Time`
- ✅ `{"kline": "2026-05-14T05:48:04.427959Z"}`
- ❌ `{"kline": "74"}` → `invalid_json`

**根因**：当 `strategy_sdk.data_dependency_times()` 遇 account overlay API 调用失败时（日志出现 `strategy_context_overlay_failed`），可能误取 `snapshot.freshness_ms` 的整数值填入 `data_dependencies`，导致 Go 反序列化失败。

**修复方向**：在 `strategy_sdk.py` 的 `data_dependency_times()` 中确保只输出合法的 RFC3339 时间戳；overlay 失败时宁可返回空 `{}`，不要填入非时间戳值。

### 6b. ⚠️ `stop_price=0` — 超低价币量化归零

超低价币（price < 0.01 USDT）的 tick_size 可能大于价格本身，导致 `quantize_price()` 将止损价、止盈价量化归零。Go 校验 `StopPrice.IsPositive()` 失败，reject `stop_price is required when stop loss mode is price`。

**修复方向**：使用 SDK 新建的 `_quantized_or_fallback(value, fallback, context)` 替代 `quantize_price()` — 先量化，结果 ≤0 时使用 fallback 的未量化值。`basic_trade_params()` 的 long/short 分支已内置此防御。

### 6c. 策略注册后的 Settings 刷新

修改 `app_setting_configs.allowed_signal_skills` 后运行时不会自动生效。需触发：

```bash
nats pub settings.changed '{"version":1}'
```

或直接 kill & 自动重启 worker 进程（KeepAlive 模式下 launchd 自动拉起）。

### 7. 排查时易犯错误

- **查错数据库**：worker 用 `crypto_trader`（production），不是 `crypto_trader_dev`。检查 worker 的 `DATABASE_URL` env var 确认。
- **只查 signals 表**：信号可能全进了 `strategy_signal_rejects` 而 `signals` 表为空。两表都要查。
- **忽略 NATS subject 分布**：`nats stream subjects` 能直接看出 signal 和 dead letter 的比例，是判断“全被拒”还是“根本没到”的最快方式。

## 参考

- 详细流程：[references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)
- 安全边界：[references/safety-boundaries.zh-CN.md](references/safety-boundaries.zh-CN.md)
- Agent API：[references/agent-api.zh-CN.md](references/agent-api.zh-CN.md)
- StrategySignalEvent 与 trade_params 参数传递规则：[references/trade-plan-signal-parameter-design.md](references/trade-plan-signal-parameter-design.md)
- 信号推送被拒诊断：[references/signal-rejection-diagnosis.md](references/signal-rejection-diagnosis.md)
- 价格量化陷阱（超低价币止损/止盈归零与方向错位）：[references/price-quantization-pitfalls.md](references/price-quantization-pitfalls.md)
- 模板：[templates/dynamic_strategy.py](templates/dynamic_strategy.py)
- 单测模板：[templates/unit_test.py](templates/unit_test.py)
- 本地缓存 schema：[generated/capabilities.json](generated/capabilities.json)
- trade_params schema：[generated/trade-plan-signal.schema.json](generated/trade-plan-signal.schema.json)
