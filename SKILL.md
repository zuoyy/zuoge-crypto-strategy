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

## 策略胜率优化方法论

⚠️ **先修评分公式，再调 gate 阈值。** 不要只调 `neutral_probe` score floor 或 `directional_book` gate——先检查评分公式本身是否奖励了错误行为。

常见结构性陷阱：
- **`flow_score` 奖励追涨**：`signed_change * N` 线性系数让拉得最凶的币得最高分。应改为奖金衰减（≤5% 线性，5-15% 衰减到 0，≥15% 不奖）。
- **`candidate_score`/`move_score` 奖励大波动**：`log1p(abs(change))` 不区分方向，涨 20% 和跌 20% 得同分。
- **缺少超买/超卖过滤**：`position_in_range` 来自 1h/4h kline，>0.82 不做多，<0.18 不做空。
- **缺少大趋势确认**：做多要求 4h `trend_return_pct > -0.8%`，做空要求 `< 0.8%`。
- **缺少市场 regime**：BTC 跌 >1.5% 时不做多 alts；BTC 涨 >1.5% 时不追空。

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

只有当用户明确要求"发布当前策略目录""更新生产策略目录"或"执行策略目录发布"时，才执行本节。

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

## 信号推送故障排查

当用户反馈"策略发了信号但后端没收到"时，按以下链路逐级排查。不要跳步猜测。

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

### 4. Go 侧 — 看 reject 表

```sql
SELECT reason_code, COUNT(*) as cnt
FROM strategy_signal_rejects
GROUP BY reason_code ORDER BY cnt DESC;
```

### 5. 常见 reject 原因

- `invalid_json`: data_dependencies 非 RFC3339 时间戳
- `signal_validation_failed`: strategy_id 未注册或 stop_price=0
- `market_seq_too_old`: expire_ms 太小
- `min_reward_risk`: 盈亏比不达标

### 6. 排查致命错误模式

**account_risk_budget_missing**: overlay 失败 → 全部 NO_TRADE
**SlowConsumer 598万次**: `*` 通配符订阅 → 事件循环饥饿
**signals 全 expired 但有成交**: 查 fills + position_plan_runtimes
**账户满仓死锁**: total_exposure>100%, remaining_budget=0

详见 [references/production-db-quick-diagnosis.md](references/production-db-quick-diagnosis.md)。

## 参考

- 详细流程：[references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)
- 安全边界：[references/safety-boundaries.zh-CN.md](references/safety-boundaries.zh-CN.md)
- Agent API：[references/agent-api.zh-CN.md](references/agent-api.zh-CN.md)
- StrategySignalEvent 与 trade_params：[references/trade-plan-signal-parameter-design.md](references/trade-plan-signal-parameter-design.md)
- 持仓感知交易计划：[references/position-aware-trading-plan.md](references/position-aware-trading-plan.md)
- 生产 DB 快速诊断：[references/production-db-quick-diagnosis.md](references/production-db-quick-diagnosis.md)
- 策略胜率诊断与优化：[references/strategy-optimization-playbook.md](references/strategy-optimization-playbook.md)
- 价格量化陷阱：[references/price-quantization-pitfalls.md](references/price-quantization-pitfalls.md)
- risk_budget sizing 瓶颈：[references/risk-budget-sizing-pitfall.md](references/risk-budget-sizing-pitfall.md)
- close_ratio 尾盘残留：[references/close-ratio-ladder-tail.md](references/close-ratio-ladder-tail.md)
- 模板：[templates/dynamic_strategy.py](templates/dynamic_strategy.py)