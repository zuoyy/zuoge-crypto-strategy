# Agent API

所有 skill/agent 请求必须使用：

- Base URL: `ZUOGE_CRYPTO_BASE_URL`
- Auth: `Authorization: Bearer <ZUOGE_CRYPTO_API_KEY>`
- Prefix: `/api/v1/agent`

候选策略登记、检查、测试、回测、报告和提交评审不通过 Agent API 完成；这些操作由 `crypto-skill` 直接使用本地源码目录和本地开发数据库。Agent API 只用于查询运行态能力目录、实时 context、订阅状态和决策日志。

允许端点：

- `GET /api/v1/agent/strategy/capabilities`
- `GET /api/v1/agent/market/subscriptions`
- `GET /api/v1/agent/strategy/candidates`
- `GET /api/v1/agent/strategy/context`
- `GET /api/v1/agent/strategy/context/{symbol}`
- `GET /api/v1/agent/strategy/decision-logs`

禁止端点：

- 审批评审
- 启用候选
- 实盘部署开关
- 禁用策略
- 直接发布策略信号
- 风控、执行、交易所操作

如果返回 401/503，停止。不要从静态示例推导系统能力。

发布当前策略目录不通过 Agent API 完成。用户明确要求时，回到项目根目录执行 `crypto-skill strategy deploy-current`，且不要额外调用上述禁止端点。
