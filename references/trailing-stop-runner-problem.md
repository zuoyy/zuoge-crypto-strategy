# 移动止盈吃不到大肉的诊断

2026-05-17 胜率诊断中发现：历史最大单笔盈利仅 ~$80（约 4-8%），从无 10%+ 赢单。

## 根因

退出机制三条路，但移动止盈把 TP2 架空：

```
1. 止损触发        → 亏钱走
2. 移动止盈         → 盈利 1.5×stop 后激活，回撤 1×stop 就走
3. TP 阶梯止盈      → TP1=1.5R，TP2=2.2R（但被移动止盈抢先平了）
```

以 stop=3% 为例的典型走势：

| 价格 | 移动止盈 | 结果 |
|------|---------|------|
| 入场 | 未激活 | |
| +4.5% (1.5×stop) | **激活**，锚定当前价 | |
| +8% | 跟踪到 +5% | |
| 回撤 3% (1×stop) | **触发平仓 @ +5%** | TP2(6.6%) 形同虚设 |
| +20% | 已离场 | **吃不到** |

**币涨 20%，策略 5% 就被甩下车。**

## 代码位置

```python
# _apply_stage_exits()  line 760-770
stop_pct = max(float(state.get("stop_pct", 0.012)), 0.009)
activation = stop_pct * 1.5    # 1.5x stop 就激活
exits["trailing_stop"] = {
    "enabled": True,
    "activation_mode": "after_profit_pct",
    "activation_profit_pct": strategy_sdk.fmt(activation),
    "trail_mode": "percent",
    "trail_value": strategy_sdk.fmt(stop_pct),  # 跟 1×stop
    "move_to_break_even": True,
}
```

关键参数：
- `activation_profit_pct`: 1.5 × stop → 激活过早
- `trail_value`: 1 × stop → 回撤容忍太小

## 修复方案

### A: 放宽 trail_value（最简）

```python
# trail 从 1×stop → 2×stop
exits["trailing_stop"]["trail_value"] = strategy_sdk.fmt(stop_pct * 2)
```

涨 8% 后允许回撤 6%，不轻易甩下车。

### B: 延迟激活

```python
# 激活从 1.5×stop → 3×stop
activation = stop_pct * 3
```

前期让利润跑，不急着锁。

### C: TP2 后切 runner 模式

TP2 触发后剩余仓位用更宽 trail（3×stop），先落袋一部分，剩下的博趋势。需要 SDK 支持。

### 建议优先级

A > B > C。A 一行改动，风险可控。
