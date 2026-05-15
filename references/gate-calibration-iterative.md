# Gate 迭代校准工作流

## 核心原则

**query → identify → fix → requery → observe chain reaction**

每次放宽一个 gate 后，必须重新查数据观察连锁反应——放宽 A 门可能导致 B 门成为新瓶颈。

## 标准迭代循环

### 1. 查当前拒绝分布（30min 窗口）

```sql
SELECT reason, COUNT(*) as cnt,
       ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) as pct
FROM strategy_decision_logs
WHERE created_at > NOW() - INTERVAL '30 minutes'
  AND decision = 'NO_TRADE'
GROUP BY reason ORDER BY cnt DESC LIMIT 15;
```

### 2. 排序修复优先级

按影响排序，但注意：
- **结构性问题优先**（stage 分类、评分公式）→ 影响信号质量
- **gate 阈值其次**（book/spread/structure_bias）→ 影响信号数量
- **效率问题最次**（add gate 噪音、signal_limit）→ 不伤信号

### 3. 每次只改一类 gate，部署后观察 15 分钟

改完后立即：
```sql
-- 重新查拒绝分布，看连锁反应
SELECT reason, COUNT(*) as cnt FROM strategy_decision_logs
WHERE created_at > NOW() - INTERVAL '15 minutes'
GROUP BY reason ORDER BY cnt DESC LIMIT 15;
```

### 4. 连锁反应典型模式

| 放宽 A | 新瓶颈 B | 原因 |
|--------|----------|------|
| book gate ±0.03→±0.05 | spread_too_wide 翻倍 | 更多 candidate 过 book 但卡 spread |
| breakout gate 分拆 | neutral_probe_too_weak 上升 | 更多 signal 进 neutral_probe |
| BTC regime 一刀切→梯度 | book_not_supporting_long 出现 | long candidate 活了但 book 不行 |

## 本轮迭代实录

### Round 1: gate 分拆（四合一）
- 查数据：breakout 28k、book 35k、add 36k、neutral 40k
- 改：breakout 按 stage 分拆（0.0 vs -0.08）、book ±0.03→±0.05、add 1.5%→1.0%、expire 60s→90s
- 观察：breakout 从 17% → 消失、book 35k→1.7k ✅
- 连锁反应：spread_too_wide 后来翻倍

### Round 2: 阶段多样性
- 查数据：2 个 stage 出信号（trend 89%、expansion 11%）、0 long
- 改：BTC regime 梯度惩罚、sweep_reclaim 实现、early_trend bonus 0→2
- 观察：book_not_supporting_long 出现（82），long candidate 复活 ✅

### Round 3: spread + 信号分散
- 查数据：spread_too_wide 26k（新瓶颈）、前 3 币占 58%
- 改：spread 20→25（非 breakout）、币级 300s 冷却、early_trend 顺序+book 修复
- 观察：待验证

## 禁忌

- ❌ 不要同时改结构性 gate（如 stage 逻辑）和阈值 gate——无法归因
- ❌ 不要在没查数据前改——先看分布再动手
- ❌ 不要只改不验——15 分钟后必须 re-query
- ❌ 不要宽到信号质量崩塌——中性 gate 比放开更强 gate 优先保持
