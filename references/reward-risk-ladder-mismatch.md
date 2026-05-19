# 盈亏比梯子约束 mismatch

**诊断日期**: 2026-05-19
**症状**: 策略 `workflow_distilled_funnel` 产生 SIGNAL，但 `signals` 表全部 `status=rejected`，`strategy_signal_rejects` 表无对应记录。

## 根因

`basic_trade_params()` 的 TP 梯子是两档：
- TP1: 1.5× risk（硬编码 `tp1_ratio = 1.5`），50% 仓位
- TP2: `reward_risk` × risk（如 2.2×），50% 仓位

Go 后端 `RewardRiskRatio()` **只用第一档止盈 TP1 做校验**。之前把 `min_reward_risk` 设成了**加权平均**（1.85），但后端比较的是：
```
min_reward_risk = 1.85 (加权平均 — 错误)
后端公式: (TP1价 - 入场价) / (入场价 - 止损价) >= min_reward_risk
实际 TP1 RR = 1.50
后端检查: 1.50 ≥ 1.85 → REJECT
```

## 修复

改 `basic_trade_params()` 中把 `min_reward_risk` 设为 `tp1_ratio`（= 1.5）：

```python
tp1_ratio = 1.5
# 后端 RewardRiskRatio() 只用第一档止盈做校验
min_reward_risk = tp1_ratio  # 不是加权平均!
```

**关键洞察**: 后端只看 TP1，不看第二档。TP1（50% 仓位）在 1.5× 止盈，TP2 只是提高剩余仓位的盈亏比。后端逻辑是"最小 RR 必须达标"，不是"平均 RR 必须达标"。

## 教训

1. **不要给后端行为拍脑袋**。TP 梯子有加权平均 = 1.85 不代表后端也用这个值。必须读 Go 源码确认：`grep -rn "RewardRiskRatio\|min_reward_risk" internal/`
2. Worker 是独立 Go 二进制（`/opt/homebrew/var/crypto-trader/current/worker`），与 Python 策略进程分开。只重启 realtime_main 不够。

## 验证 SQL

```sql
SELECT created_at, symbol, side, status,
       payload_json->'trade_params'->'execution_constraints'->>'min_reward_risk' as min_rr
FROM signals
WHERE strategy_id = 'workflow_distilled_funnel'
ORDER BY created_at DESC LIMIT 5;

SELECT 
    payload_json->'price_ref' as entry,
    payload_json->'trade_params'->'exits'->'stop_loss'->>'stop_price' as stop,
    payload_json->'trade_params'->'exits'->'take_profit'->'targets'->0->>'price' as tp1,
    payload_json->'trade_params'->'execution_constraints'->>'min_reward_risk' as min_rr
FROM signals
WHERE signal_id = '<id>' LIMIT 1;
```
