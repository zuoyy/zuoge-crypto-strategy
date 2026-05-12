---
name: zuoge-crypto-strategy
description: "用于编写、校验、回测、提交实时策略候选，并在明确要求时发布当前策略目录到生产实时策略服务。"
---

# zuoge-crypto-strategy

本技能面向渐进式漏斗实时策略架构。策略分两阶段：

- `discover(universe)` 消费 `strategy.universe.delta` 的全市场轻量数据，只输出 side-aware candidates。
- `build_signals_from_context(context)` 消费已预热的 `strategy.context.delta.{symbol}`，只在完整行情依赖满足后输出标准 `StrategySignalEvent`。

候选池由系统托管但按策略隔离，候选唯一键为 `(strategy_id, symbol, side)`。同一 symbol 可以同时被不同策略或不同方向选中；完整行情订阅由系统按 symbol/dependency 合并，方向冲突交给 risk/execution。

## 开始前必须先做

1. 确认项目根目录。优先使用当前工作区；否则读取 `ZUOGE_CRYPTO_PROJECT_ROOT`；目录内必须存在 `cmd/crypto-skill/main.go` 和 `strategy/runtime/strategy_sdk.py`。
2. 切换到项目根目录工作。候选策略登记、检查、测试、回测、报告和提交评审都通过 `crypto-skill` 直接操作本地源码目录和本地开发数据库。
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
10. 只有 check/test/backtest 都通过时，`crypto-skill candidate submit-review --candidate <candidate_id>`，然后停止等待人工审批。

`candidate new` 只用于首次把候选策略文件登记到策略中心并取得 `candidate_id`。如果用户要求检查、测试或回测一个已经登记过的策略，先用 `crypto-skill candidate list` 或 `crypto-skill candidate show --candidate <candidate_id>` 找到已有 `candidate_id`，然后直接运行 `check`、`test`、`backtest`、`report`；不要为同一个 `strategy_id` 和 `version` 重复执行 `candidate new`。

## 发布当前策略目录

只有当用户明确要求“发布当前策略目录”“更新生产策略目录”或“执行策略目录发布”时，才执行本节。

发布含义：

- 只发布项目根目录下当前 `strategy/` 目录。
- 使用仓库内既有生产脚本，不手写替代发布流程。
- 不审批候选策略，不启用候选策略，不绕过策略中心。
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

该命令会通过仓库内既有生产脚本创建新的生产发布目录、同步当前 `strategy/` 目录、检查 Python 策略依赖，并刷新 `com.crypto-trader.realtime-strategy` 服务。发布完成后，向用户报告发布编号、当前发布链接和命令输出里的日志路径。

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
- 不审批候选、不启用候选、不禁用策略、不执行实盘部署开关。
- 除“发布当前策略目录”一节允许的生产脚本外，不执行其他发布命令。
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
