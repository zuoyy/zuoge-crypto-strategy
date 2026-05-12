# 安全边界

AI 策略是只读决策层。`discover()` 只能消费实时 `strategy.universe.delta` 并返回候选；`build_signals_from_context()` 只能消费预热后的 `strategy.context.delta.{symbol}` 并返回候选 `StrategySignalEvent`。

策略代码禁止：

- `requests`、`urllib`、`httpx`、`socket`、`websocket`
- `open()`、`Path`、`os`、`shutil`、`subprocess`
- `psycopg`、`sqlite3`、`sqlalchemy`
- `binance`、`ccxt`、exchange client
- `NATSClient`、`publish_signal`、`strategy.signals`

技能禁止：

- 写 enabled 策略目录
- 审批候选、启用候选、禁用策略、执行实盘部署开关
- 使用网页控制台 cookie
- 调用非 `/api/v1/agent/...` 路由
- 在 API key 无效时伪造 capabilities、delta、backtest 或 report

发布边界：

- `submit-review` 只是提交人工审批。
- 用户明确要求“发布当前策略目录”时，只允许执行 `crypto-skill strategy deploy-current`。
- 发布当前策略目录只同步当前 `strategy/` 目录并刷新 `com.crypto-trader.realtime-strategy` 服务，不代表审批、启用或实盘开关变更。
- enabled、风控、执行仍由 Strategy Center / Go Trading Core 控制。
- 策略信号必须经过 Go signal validation、strategyingress、risk、execution 主链路。
