# 账户未配置导致信号全拒

## 症状

- `strategy_decision_logs` 正常显示 SIGNAL（策略层面通过）
- `signals` 表有信号，全部 `status=rejected`
- `strategy_signal_rejects` 表无对应记录
- Worker 日志：`"rejected": 1` 每轮 poller 循环
- 信号 payload 中 `intent` 字段可能为空（Go ingress 剥离）

## 诊断流程

```sql
-- 1. 确认信号状态
SELECT status, COUNT(*) FROM signals
WHERE strategy_id='workflow_distilled_funnel'
GROUP BY status;

-- 2. 查策略风控配置（如果为空 → 无账户配置）
SELECT strategy_id, venue, account_equity, allocation_pct, max_positions
FROM strategy_risk_allocations
WHERE strategy_id='workflow_distilled_funnel';

-- 3. 查账户快照（如果为空 → 无账户上下文）
SELECT created_at, equity, total_exposure_pct, open_positions
FROM portfolio_snapshots
ORDER BY created_at DESC LIMIT 3;

-- 4. 查 worker 日志
tail -100 /opt/homebrew/var/crypto-trader/logs/worker.stdout.log | grep "signal poller"

-- 5. 查 Go ingress reject（如果 signals 表 status=rejected 但 strategy_signal_rejects 无记录）
--    → 拒绝发生在 worker 层面，不在 ingress 层面
--    → 根因通常是无账户预算
```

## 根因

`strategy_risk_allocations` 表为空 → Go 后端无账户上下文 → overlay 返回空 → signal 无预算 → worker 无账户可执行 → 全部 rejected。

## 修复

需要：
1. 在后台绑定 Binance 账户（API key + secret）
2. 配置 `strategy_risk_allocations`（`account_equity`, `allocation_pct`, `max_positions` 等）
3. 重启 worker 使配置生效

对于新策略首次部署，这是必经步骤。
