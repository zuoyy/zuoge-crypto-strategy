# 策略胜率诊断与优化 Playbook

2026-05-15 实战诊断 workflow_distilled_funnel 0.1.0。

## 优化方法论（优先级顺序）

1. **Step 1**: 拉总盘（signals + fills 算胜率/盈亏比/期望值）
2. **Step 2**: 逐笔看亏损原因（联查 `signal_reason`，看 stage/change/book 模式）
3. **Step 3**: ⚠️ **先查评分公式**——是否有结构性问题（如奖励追涨）——再调 gate 阈值。不要只调阈值，要修公式。
4. **Step 4**: 对照 stage 分类器——是否 90%+ 交易落入 `neutral_probe`
5. **Step 5**: 加缺失的过滤器（超买/超卖、大趋势确认、市场 regime）

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
- `signal_reason` 中的 **change** 值（>8% 同向 = 追涨/杀跌）
- `signal_reason` 中的 **book** 值（负数做多 = 盘口反向）

### Step 3: ⚠️ 先查评分公式，再调 gate 阈值

常见结构性陷阱：
- **`flow_score` 奖励追涨**：`signed_change * N` 线性系数让拉得最凶的币得最高分
- **`candidate_score`/`move_score` 奖励大波动**：`log1p(abs(change))` 不区分方向
- **volume_score 只看量不看价**：高量拉升 = 高量出货，当前评分无法区分

调阈值（`neutral_probe` score floor, `directional_book` gate）之前，先修公式。

### Step 4: 对照 stage 分类器

`_stage()` 如果 90%+ 交易落入 `neutral_probe`，说明强 setup 条件太苛刻。
逐一检查 `accepted_breakout`、`expansion_continuation`、`pullback_reaccept` 的触发条件是否与实际行情匹配。

## 第一次优化（2026-05-15）

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

## 第二次优化：追涨杀跌（2026-05-15）

### 现象

用户反馈"买入就开始大跌"。检查 3 笔活跃持仓全部浮亏：
- SWARMSUSDT 入场 0.01459 → 现价 0.01307（-10.5%）
- TACUSDT 入场 0.02195 → 现价 0.02022（-7.9%）
- SQDUSDT 入场 0.04323 → 现价 0.04221（-2.4%）

### 诊断链

1. 查当前价 vs 入场价 → 全部大跌
2. 查 `signal_reason` 中的 `change` 值 → 全部 +15~20%（已拉完）
3. 查 `directional_book` → SQDUSDT book=-0.026, TACUSDT book=-0.042（盘口卖单为主）
4. 结论：**策略在高位追涨**。

### 修复（三层防御）

| # | 位置 | 修改 |
|---|------|------|
| 1 | **`discover()`** | `change > 10%` 或 `< -10%` → 直接跳过 |
| 2 | **`_trade_gate`** 盘口 | long 要求 `book ≥ 0`，short 要求 `book ≤ 0` |
| 3 | **`_trade_gate`** 追涨 | long + `change > 8%` → 拒；short + `change < -8%` → 拒 |

## 第三次优化：结构性问题（2026-05-15）

### ⚠️ 关键教训

用户明确指出"你就优化的这个吗？？？？"——前两轮只调了 gate 阈值，但没有检查**评分公式本身是否奖励了错误行为**。

### 发现

`flow_score` 公式直接奖励追涨：

```python
# 旧代码
flow_score = clamp(50.0 + directional_book * 180.0 + signed_change * 4.0, 0, 100)
#                                         ↑ +15% 涨 → +60 分，直接满分！
```

币涨得越凶分数越高，这是策略总是选中山顶币的根因。

### 修复（4 处结构修改）

| # | 位置 | 修改 | 拦截效果 |
|---|------|------|----------|
| 1 | **`flow_score`** | `signed_change * 4` → `_change_bonus()`：≤5% 线性奖励，5-15% 衰减到 0，≥15% 不奖励 | 追涨币不再得高分 |
| 2 | **超买过滤** | long 时 `position_1h/4h > 0.82` → 拒；short 时 `< 0.18` → 拒 | 不买区间顶点的币 |
| 3 | **4h 趋势确认** | long 要求 `trend_4h > -0.8%`；short 要求 `< 0.8%` | 不与 4h 大趋势对抗 |
| 4 | **BTC 市场 regime** | `discover()` 中 BTC 跌 >1.5% 不做多；BTC 涨 >1.5% 不做空 | 顺势而为 |

`_change_bonus` 实现：

```python
@staticmethod
def _change_bonus(signed_change: float) -> float:
    a = abs(signed_change)
    if a <= 5.0:   return signed_change           # 线性奖励
    if a >= 15.0:  return 0.0                     # 不奖励追涨
    sign = 1.0 if signed_change >= 0 else -1.0
    return sign * (5.0 - (a - 5.0) * 0.5)         # 5→15 线性衰减
```

### 防御层总览（7 层，以 TACUSDT +19.9% / book=-0.042 为例）

| 关卡 | 拦截 |
|------|------|
| discover ±10% 天花板 | ❌ 19.9% > 10% |
| BTC regime（若 BTC 跌） | ❌ 不做多 |
| 盘口方向 book≥0 | ❌ -0.042 |
| 超买 position_1h/4h | 若 >0.82 → ❌ |
| 4h trend | 若 < -0.8% → ❌ |
| flow_score 衰减 | 只给 0 分 |
| 追涨 ±8% gate | ❌ 19.9% > 8% |

## 通用诊断语句

```sql
-- 1. 拉总盘
WITH trades AS (
  SELECT f.signal_id, f.symbol, SUM(f.realized_pnl) as pnl,
    MIN(f.filled_at) as opened, MAX(f.filled_at) as closed
  FROM fills f WHERE f.signal_id LIKE 'sig-workflow%'
  GROUP BY f.signal_id, f.symbol
)
SELECT CASE WHEN pnl>0 THEN 'WIN' ELSE 'LOSS' END as r,
  COUNT(*), ROUND(AVG(pnl)::numeric,2) as avg_pnl
FROM trades GROUP BY 1;

-- 2. 亏损明细（联查 signal_reason 找模式）
SELECT s.signal_id, s.symbol, s.side, s.signal_reason,
  ROUND(SUM(f.realized_pnl)::numeric,2) as pnl
FROM signals s JOIN fills f ON f.signal_id=s.signal_id
WHERE s.signal_id LIKE 'sig-workflow%'
GROUP BY s.signal_id, s.symbol, s.side, s.signal_reason
HAVING SUM(f.realized_pnl) < 0 ORDER BY SUM(f.realized_pnl);

-- 3. 查当前活跃持仓是否追涨
SELECT p.symbol, p.position_side, s.signal_reason
FROM position_plan_runtimes p
JOIN signals s ON s.signal_id = p.signal_id
WHERE p.runtime_status = 'active';
-- 看 signal_reason 中 change=XX% — 如果 >8% 且方向与 side 同向，即追涨/杀跌

-- 4. 门控拦截分布（看哪个 gate 拦截最频繁）
SELECT decision, reason, count(*) as cnt
FROM strategy_decision_logs
WHERE created_at > now() - interval '1 hour'
GROUP BY decision, reason ORDER BY cnt DESC LIMIT 10;
```
