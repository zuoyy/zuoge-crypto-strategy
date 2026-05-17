# 仓位轮换死锁诊断

## 诊断链路

当轮换从未触发时，逐层排查：

```
1. 查持仓状态 → slots=0？（满仓才可能轮换）
2. 查 decision_logs → ROTATE 出现次数
3. 查信号分数分布 → 能否达到轮换门槛
4. 查 stage 分布 → 当前市场产出什么阶段
5. 查 stage_bonus → 负加成是否压死了天花板
```

## 死锁模式：绝对阈值 + 分数天花板

**症状**：持仓满、轮换逻辑正确，但 `ROTATE` decision_logs = 0。

**根因**：轮换门槛是绝对分数阈值（如 score ≥ 85），但当前市场只产出低分阶段。

**示例**（2026-05-17 实盘）：
- 策略 `workflow_distilled_funnel` 满仓 3/3
- 牛市行情只触发 `neutral_probe` 阶段
- `neutral_probe` 的 `stage_bonus = -3.0`
- 分数被压在 82-84 区间
- 轮换门槛 85 → 永远不触发
- 轮换逻辑 = 死代码

## 修复方向

### ❌ 不加：降低绝对阈值
```python
if state["score"] < 82:  # 从 85 降到 82
```
问题：弱信号也轮换 → 频繁换仓 → 手续费磨损

### ✅ 应做：相对质量比较
```python
# 新信号 vs 最弱持仓的入场分 + 浮盈补偿
quality_gap = state["score"] - weakest_entry_score
min_pnl_pct = weakest_pnl_pct  # 已有浮盈
if quality_gap > 5 and min_pnl_pct > 0.5:
    rotate()
```
核心原则：**新信号必须显著优于旧仓位的进场质量，且旧仓位已有浮盈可落袋。**

### 变体：阶段加权比较
```python
stage_weights = {"deep_reversal": 1.5, "pullback_reversal": 1.3, "trend_continuation": 1.1, ...}
new_quality = state["score"] * stage_weights.get(state["stage"], 1.0)
old_quality = weakest_entry_score * stage_weights.get(weakest_entry_stage, 1.0)
if new_quality > old_quality * 1.15 and weakest_pnl_pct > 0.5:
    rotate()
```

## 验证方法

改完后观察：
```sql
-- 轮换触发率
SELECT COUNT(*) FILTER (WHERE decision='ROTATE') as rotates,
       COUNT(*) FILTER (WHERE decision='SIGNAL') as signals
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > NOW() - INTERVAL '1 day';
```

期望：`rotates > 0` 且 `rotates / signals < 0.3`（不过度换仓）
