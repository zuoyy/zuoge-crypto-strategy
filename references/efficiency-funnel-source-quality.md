# 效率漏斗 — 源头质量决定决策量

## 问题

策略每小时产出 50 万+ 条 decision log，但 SIGNAL 仅 ~200 条（0.02%）。大量 NO_TRADE 来自 context 评估阶段的拒绝——这些 candidate 本就不该进入 context 评估。

## 漏斗模型

```
discover() → N candidates → context评估 → M次决策 → S条信号
    ↑                          ↑
  源头筛选                   99.98%噪音
```

**核心原则：源头收紧 > gate 加码。** discover() 多放一个弱 candidate，context delta 回调就会触发评估链（依赖检查 → context overlay → trade_gate → sizing），每个 candidate 每秒可能触发多次评估，产生成百上千条 decision log。

## 诊断方法

```sql
-- 看拒绝分布，判断问题在源头还是 gate
SELECT reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY reason ORDER BY cnt DESC;

-- 如果 strategy_budget_missing 占比 >30% → 源头过度宽松
-- 如果 neutral_probe_too_weak 占比高 → score floor 可调
-- 如果 breakout_without_1h_4h_confirmation 占比高 → structure bias gate 可调
```

## 收紧策略

### 1. 提高 candidate score floor

```
discover score floor: 40 → 55
```

40 分 candidate 几乎不可能通过 context 评估（neutral_probe floor 85），纯浪费评估资源。

### 2. 减少 candidate limit

```
candidate limit: 16 → 8
```

少而精的 candidate 减少 context delta 订阅量，降低 NATS 负载。

### 3. 动态 TTL

```
score ≥ 70 → TTL 300s（好候选多存活）
score 55-70 → TTL 120s（中等候选快速淘汰）
score < 55 → 不进入候选池
```

弱候选快速淘汰，减少 context 回调次数。

### 4. 方向一致性预筛选

在 discover() 阶段就对齐 side 和 book 方向：
- 做多 candidate 要求 book 不严重偏卖
- 做空 candidate 要求 book 不严重偏买

这可以砍掉进入 context 后被 `book_not_supporting_*` 拒绝的大量 candidate。

## 不要做的事

- **不要在 context 评估阶段加更多 gate** — gate 越严，decision log 越多（每条拒绝都写一条 log），效率更低
- **不要降低 signal 产出门槛来提升 signal 数** — 弱信号执行后胜率更低，得不偿失
