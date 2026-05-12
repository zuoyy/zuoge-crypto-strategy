# 安全边界

AI 策略是只读决策层。`discover()` 只能消费实时 `strategy.universe.delta` 并返回候选；`build_signals_from_context()` 只能消费预热后的 `strategy.context.delta.{symbol}` 并返回候选 `StrategySignalEvent`。

策略代码禁止：

- `requests`、`urllib`、`httpx`、`socket`、`websocket`
- `open()`、`Path`、`os`、`shutil`、`subprocess`
- `psycopg`、`sqlite3`、`sqlalchemy`
- `binance`、`ccxt`、exchange client
- `NATSClient`、`publish_signal`、`strategy.signals`

skill 禁止：

- 写 enabled 策略目录
- approve / deploy / disable
- 使用 WebUI cookie
- 调用非 `/api/v1/agent/...` 路由
- 在 API key 无效时伪造 capabilities、delta、backtest 或 report

发布边界：

- `submit-review` 只是提交人工审批。
- enabled、风控、执行仍由 Strategy Center / Go Trading Core 控制。
- 策略信号必须经过 Go signal validation、strategyingress、risk、execution 主链路。
