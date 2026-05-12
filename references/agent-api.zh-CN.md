# Agent API

所有 skill/agent 请求必须使用：

- Base URL: `ZUOGE_CRYPTO_BASE_URL`
- Auth: `Authorization: Bearer <ZUOGE_CRYPTO_API_KEY>`
- Prefix: `/api/v1/agent`

允许端点：

- `GET /api/v1/agent/strategy/capabilities`
- `GET /api/v1/agent/market/subscriptions`
- `GET /api/v1/agent/strategy/candidates`
- `GET /api/v1/agent/strategy/context`
- `GET /api/v1/agent/strategy/context/{symbol}`
- `GET /api/v1/agent/strategy/decision-logs`
- `POST /api/v1/agent/strategy/research`
- `GET /api/v1/agent/strategy/research/{research_id}`
- `POST /api/v1/agent/strategy-candidates`
- `GET /api/v1/agent/strategy-candidates`
- `GET /api/v1/agent/strategy-candidates/{candidate_id}`
- `POST /api/v1/agent/strategy-candidates/{candidate_id}/check`
- `POST /api/v1/agent/strategy-candidates/{candidate_id}/test`
- `POST /api/v1/agent/strategy-candidates/{candidate_id}/backtest`
- `GET /api/v1/agent/strategy-candidates/{candidate_id}/report`
- `POST /api/v1/agent/strategy-candidates/{candidate_id}/submit-review`

禁止端点：

- approve review
- deploy enabled
- deploy live
- disable strategy
- direct signal publish
- risk/execution/exchange 操作

如果返回 401/503，停止。不要从静态示例推导系统能力。
