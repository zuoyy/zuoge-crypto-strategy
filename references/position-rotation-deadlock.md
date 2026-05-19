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

## 修复方案（已部署）

### ✅ 采用：PnL-梯度 + 阶段感知（2026-05-17）

```python
# Gradient: the more profit we're giving up, the lower the replacement bar
if weakest_pnl_pct < 2.0:
    required_score = 84.0      # 小盈 → 需要显著改善
elif weakest_pnl_pct < 5.0:
    required_score = 82.0      # 中等盈 → 任意过门信号即可
else:
    required_score = 80.0      # 大盈 → 放手轮换

# Stage-aware bonus: reversal stages have higher upside
stage_bonus = 0.0
if stage in ("deep_reversal", "pullback_reversal"):
    stage_bonus = 2.0
elif stage == "trend_continuation":
    stage_bonus = 1.0

effective_score = new_score + stage_bonus
if effective_score < required_score:
    return None  # not worth rotating
```

**为什么不用 entry_score 比较**：position 表不存进场分，无法直接比较。用 PnL 幅度作为替代——浮盈越大，让出的机会成本越低。

**为什么保留 `prefer <2.5%` 逻辑**：总是优先选最小盈仓换出（落袋为安），只在没有小盈仓时才换大盈仓。

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
