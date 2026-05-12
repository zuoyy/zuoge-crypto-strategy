---
name: zuoge-crypto-strategy
description: "Use this skill to author, validate, backtest, and submit realtime crypto-trader Python strategy candidates that use the progressive funnel runtime: discover all-market candidates, request full symbol context, then emit standard signals only after warmup."
---

# zuoge-crypto-strategy

本 skill 面向渐进式漏斗实时策略架构。策略分两阶段：

- `discover(universe)` 消费 `strategy.universe.delta` 的全市场轻量数据，只输出 side-aware candidates。
- `build_signals_from_context(context)` 消费已预热的 `strategy.context.delta.{symbol}`，只在完整行情依赖满足后输出标准 `StrategySignalEvent`。

候选池由系统托管但按策略隔离，候选唯一键为 `(strategy_id, symbol, side)`。同一 symbol 可以同时被不同策略或不同方向选中；完整行情订阅由系统按 symbol/dependency 合并，方向冲突交给 risk/execution。

## 必须先做

1. 确认项目根目录。优先使用当前工作区；否则读取 `ZUOGE_CRYPTO_PROJECT_ROOT`；目录内必须存在 `cmd/crypto-skill/main.go` 和 `strategy/runtime/strategy_sdk.py`。
2. 切换到项目根目录工作，读取环境变量 `ZUOGE_CRYPTO_BASE_URL` 和 `ZUOGE_CRYPTO_API_KEY`。
3. 调用 `GET ${ZUOGE_CRYPTO_BASE_URL}/api/v1/agent/strategy/capabilities`，认证头为 `Authorization: Bearer <ZUOGE_CRYPTO_API_KEY>`。
4. 如果项目根目录无法确认、API key 未配置、无效、服务返回 401/503，停止并说明无法读取策略能力目录，不要伪造字段表或策略结果。

只能调用 `/api/v1/agent/...` 路由。不要使用 WebUI cookie，不要调用普通 `/api/v1/...` 路由。

## 可移植安装

本 skill 可以复制到 Codex、Claude、Cursor 或其他 AI 的 skill 目录使用。安装位置不要求在项目仓库内，但执行策略任务时必须进入项目根目录，且命令 `crypto-skill` 必须可用；如不可用，使用 `go run ./cmd/crypto-skill ...` 等价执行。

所有相对引用都以本 skill 目录为基准读取，例如 `references/`、`templates/`、`generated/`。不要把 skill 安装目录误认为项目根目录。

## 工作流

在项目根目录内工作：

1. `crypto-skill capabilities show --format json`
2. 按需读取 [references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)。
3. `crypto-skill research create --focus "<研究目标>"`
4. 基于 capabilities 和 SDK 编写候选策略到 `strategy/strategies/candidates/`。
5. `crypto-skill candidate new --strategy <id> --version <x.y.z> --file <candidate.py> --research <research_id>`
6. `crypto-skill candidate check --candidate <candidate_id>`
7. `crypto-skill candidate test --candidate <candidate_id>`
8. `crypto-skill candidate backtest --candidate <candidate_id>`
9. `crypto-skill candidate report --candidate <candidate_id>`
10. 只有 check/test/backtest 都通过时，`crypto-skill candidate submit-review --candidate <candidate_id>`，然后停止等待人工审批。

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

## 禁止

- 不写 `strategy/strategies/enabled/`。
- 不 approve、不 deploy、不 enabled、不 deploy-live、不 disable。
- 不发布 `strategy.signals`，不调用 NATS publish。
- 策略代码内不访问网络、数据库、交易所、文件系统、subprocess。
- 不读 `.env`、secrets、keys。
- 不直接调用 risk/execution/exchange API。
- 不在 `discover()` 里访问完整盘口、深度、逐笔成交或生成 signal。

## 可观测接口

- `GET /api/v1/agent/market/subscriptions` 查看 active leases、聚合 streams 和 warmup 状态。
- `GET /api/v1/agent/strategy/candidates` 查看策略私有候选池状态。

## 参考

- 详细流程：[references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)
- 安全边界：[references/safety-boundaries.zh-CN.md](references/safety-boundaries.zh-CN.md)
- Agent API：[references/agent-api.zh-CN.md](references/agent-api.zh-CN.md)
- 模板：[templates/dynamic_strategy.py](templates/dynamic_strategy.py)
- 单测模板：[templates/unit_test.py](templates/unit_test.py)
- 本地缓存 schema：[generated/capabilities.json](generated/capabilities.json)
