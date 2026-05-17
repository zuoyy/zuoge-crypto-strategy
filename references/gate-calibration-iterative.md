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
- 观察：spread_too_wide 从 top gate 消失 ✅；book_not_supporting_short 成新瓶颈（连锁反应）

### Round 4: 流动性门槛
- 查数据：22 个币出信号，仅 10 个有成交；SUI（54 信号 0 成交）、SWARMS（42/0）
- 改：discover quote_volume 25M→100M、trade_gate 20M→60M、liquidity_quality divisor 4M→10M
- 观察：candidate 池收缩，信号集中在有流动性的中大盘币

## 仓位轮换诊断循环

轮换涉及跨 symbol 信号，比普通信号多一层复杂度。排查按此顺序：

```
decision_logs ROTATE? → signals 表 close signal? 
  → rejects 表 price_deviation/acceptable_range?
  → position_plan_runtimes 持仓仍在?
```

常见根因层级：
1. **ROTATE 有但 signals 零** → close 信号未过 framework validation（`_validate_signal`）
2. **rejects 表 `price_deviation_exceeded`** → cross-symbol price_ref 错位，close 信号用了新币 context
3. **rejects 表 `acceptable_range required`** → `execution_constraints` 缺失或放错层级（应在 trade_params 内）
4. **signals 表有 close 但 status=expired** → testnet 流动性不足，市价单未成交
5. **position_plan_runtimes 仍 active** → Go backend 未执行平仓（reject 后静默）

## 禁忌

- ❌ 不要同时改结构性 gate（如 stage 逻辑）和阈值 gate——无法归因
- ❌ 不要在没查数据前改——先看分布再动手
- ❌ 不要只改不验——15 分钟后必须 re-query
- ❌ 不要宽到信号质量崩塌——中性 gate 比放开更强 gate 优先保持
