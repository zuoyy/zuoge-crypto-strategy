# budget_missing 全量诊断

## 症状

`strategy_decision_logs` 全部 reason 都是 `strategy_risk_budget_missing`，无其他 gate 触发。上下文 API 返回正常 budget 数据，但策略侧全部拒。

## 根因链路

```
Binance API 连接断 → 账户数据无法刷新
                → context overlay 失败（Go 服务）
                → NATS 投递的 context 缺少 strategy_account_fit
                → 策略 _strategy_budget_complete() 返回 False
                → 全部 NO_TRADE
```

## 快速诊断

```sql
-- 1. 确认全量 budget_missing
SELECT reason, COUNT(*) FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '5 minutes'
GROUP BY reason;

-- 2. 查 API context 里 budget 是否正常（对比）
curl -s -H "Authorization: Bearer $AGENT_API_KEY" \
  "http://127.0.0.1:18000/api/v1/agent/strategy/context/ADAUSDT?strategy_id=workflow_distilled_funnel" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); fit=d.get('strategy_account_fit',{}); print({k:fit.get(k) for k in ['account_equity','allocated_equity','remaining_total_budget_pct']})"

-- 3. 查 worker 日志的 Binance 连接
tail -50 /opt/homebrew/var/crypto-trader/logs/worker.stdout.log | grep -i 'EOF\|binance\|account'
```

如果步骤 2 正常（API 有 budget）但步骤 1 全量 missing → NATS 投递路径断裂，根因通常是步骤 3（Binance 连接问题）。

## 修复

```bash
# 重启 worker 强制重连 Binance + 重新初始化 context overlay
pkill -9 -f "/opt/homebrew/var/crypto-trader/current/worker"
sleep 3
# launchd KeepAlive 自动拉起
```

重启后等待 15-30 秒让 overlay 重新初始化，验证：
```sql
SELECT reason, COUNT(*) FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '2 minutes'
  AND reason != 'strategy_risk_budget_missing'
GROUP BY reason;
```

如果出现 `book_not_supporting_*`、`neutral_probe_too_weak` 等正常 gate 原因 → 修复成功。

## 区分 budget_missing vs 账户满仓死锁

| 症状 | budget_missing | 满仓死锁 |
|------|---------------|---------|
| 其他 gate 触发 | 无（100% missing） | 有（部分 signal 过 gate 后被 budget reject） |
| context API | budget 正常 | budget 正常 |
| total_exposure | 正常 | > 100% |
| 修复方式 | 重启 worker | 等仓位平仓或提 max_total |
