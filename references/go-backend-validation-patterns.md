# Go 后端校验模式速查

用于排查 Python 策略→Go 后端信号链的 reject 原因。

## 核心原则

**不要从 DB 字段反推后端行为。** `signals.status`、`strategy_signal_rejects.reason_code` 只能告诉你"被拒了"，
不能告诉你"为什么被拒"的完整逻辑。正确做法：直接读 Go 源码。

## 常用 Go 源码搜索路径

```bash
# 项目根目录下
cd /Users/zuo/Documents/projects/crypto-trader

# RewardRiskRatio 计算逻辑
grep -rn "RewardRiskRatio\|min_reward_risk" internal/

# intent 识别（mapIntent 函数）
grep -rn "mapIntent\|func.*[Ii]ntent" internal/strategyingress/

# 信号验证
grep -rn "func.*[Vv]alidat" internal/strategyingress/

# TP / RR / 信号执行处理
grep -rn "take_profit\|TakeProfit\|reward_risk\|min_reward_risk" internal/signal internal/risk internal/execution

# 止损处理
grep -rn "stop_loss\|StopLoss\|stop_price" internal/
```

## 已知后端校验行为

| 校验点 | 位置 | 行为 |
|-------|------|------|
| RewardRiskRatio | `internal/signal` / `internal/risk` / `internal/execution` | 以当前 Go 源码为准；不要假设旧 worker 包仍存在 |
| Intent 映射 | `internal/strategyingress/service.go` | `mapIntent()` 函数 |
| 信号验证 | `internal/strategyingress/service.go` | 格式验证、字段完整性 |
| 账户上下文 | `internal/realtimemarket` / `internal/risk` | 策略 context 和风险评估会使用 `strategy_risk_allocations` / `strategy_account_fit` |
| 复盘上下文 | `pkg/contracts/strategy_signal.go` / `internal/strategyingress/service.go` / `internal/signal/proposal.go` | `review_context` 会进入 `signals.payload_json.review_context`，用于 AI 复盘归因 |

## Worker 独立进程

Worker（`/opt/homebrew/var/crypto-trader/current/worker`）是独立 Go 二进制，与 realtime_main（Python 策略进程）分开。只重启 realtime_main 不够。两个都要重启。

```bash
pkill -9 -f realtime_main
pkill -9 -f '/current/worker'
```
