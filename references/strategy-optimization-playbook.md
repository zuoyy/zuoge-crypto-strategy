# 策略胜率诊断与优化 Playbook

2026-05-15 实战诊断 workflow_distilled_funnel 0.1.0。

## 诊断四步法

### Step 1: 拉总盘

```sql
-- 按信号分组统计 PnL
WITH trades AS (
  SELECT f.signal_id, f.symbol, SUM(f.realized_pnl) as pnl
  FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
  GROUP BY f.signal_id, f.symbol
)
SELECT CASE WHEN pnl>0 THEN 'WIN' ELSE 'LOSS' END, COUNT(*), ROUND(AVG(pnl),2)
FROM trades GROUP BY 1;
```

### Step 2: 逐笔看亏损原因

```sql
-- 亏损交易 -> signals 表联查入场阶段
SELECT s.signal_id, s.symbol, s.side, s.signal_reason,
       ROUND(SUM(f.realized_pnl)::numeric,2) as pnl
FROM signals s JOIN fills f ON f.signal_id=s.signal_id
WHERE s.signal_id LIKE 'sig-workflow%'
GROUP BY s.signal_id, s.symbol, s.side, s.signal_reason
HAVING SUM(f.realized_pnl) < 0
ORDER BY SUM(f.realized_pnl);
```

关键检查：
- `signal_reason` 中的 **stage**（neutral_probe 占比过高 = 阶段分类器太严）
- **入场到出场时长**（几分钟内止损 = stop 太紧或方向错了）

### Step 3: 对照 gate 参数

| 参数 | 作用 | 常见问题 |
|------|------|----------|
| `directional_book` threshold | 拦截盘口反向 | 太松（如 -0.12）放行轻度反向 |
| `neutral_probe` score floor | 弱信号最低分 | 太低导致大量弱信号入场 |
| `stop_pct` | 止损宽度 | 太窄被正常波动扫损 |
| `reward_risk` | 盈亏比 | RR 够但胜率太低 = 入场条件太松 |

### Step 4: 对照 stage 分类器

`_stage()` 如果 90%+ 交易落入 `neutral_probe`，说明强 setup 条件太苛刻。
逐一检查 `accepted_breakout`、`expansion_continuation`、`pullback_reaccept` 的触发条件是否与实际行情匹配。

## 本次优化（2026-05-15）

### 发现

- 30 笔交易，全部 `neutral_probe`（无强 setup 触发）
- 亏损交易 `directional_book` 全部为负（-0.023 ~ -0.118），盘口反向但 gate 未拦截
- 胜率 13.3%（4/30），RR 9.36:1，期望值 +$3.33/笔

### 修复

| 参数 | 旧值 | 新值 | 理由 |
|------|------|------|------|
| `directional_book` gate | < -0.12 | < **-0.05** | 拦截所有盘口反向交易 |
| `neutral_probe` score floor | 74 | **82** | 弱信号需要更高评分 |
| `neutral_probe` stop_pct | 0.9%~2.2% | **×1.5 (1.35%~2.8%)** | 弱信号给更多呼吸空间 |

### 预期

- 胜率 13% → 25-35%（截掉最弱的 70% 交易）
- 交易频率降低（可接受：宁缺毋滥）
