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

## 第三次优化：信号去重 + sizing 校准（2026-05-15）

### 现象

1. ACTUSDT 同一秒内推送 8 个重复信号，score/参数完全相同，后端全部拒绝
2. 下单金额 $50 notional 过小，修复公式倒置后 $2,556 又过大被拒"超过单笔预算"

### 修复

| # | 问题 | 修复 |
|---|------|------|
| 1 | 重复信号 | `_last_signal_at` 字典，symbol:side 冷却 120s |
| 2 | sizing 公式倒置 | risk_pct 从 notional% 改为 risk%：`desired_notional = target_risk / stop_pct` |
| 3 | 单笔超预算 | 加 20% 净值封顶：`desired_notional = min(desired_notional, equity × 0.20)` |

### 当前门控参数快照

| 参数 | 值 |
|------|-----|
| neutral_probe floor | 85 |
| stage (非 neutral) floor | 72 |
| spread gate | 20 bps |
| 超买 long | pos > 0.88 |
| 4h trend long | < -1.5% |
| discover 天花板 | ±10% |
| 盘口方向 | long: book≥0, short: book≤0 |
| ±8% 追涨/杀跌 gate | 非 breakout 阶段拦截 |
| signal 冷却 | 120s 同 symbol/side |

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
| 超买 position_1h/4h | 若 >0.88 → ❌ |
| 4h trend | 若 < -1.5% → ❌ |
| flow_score 衰减 | 只给 0 分 |
| 追涨 ±8% gate | ❌ 19.9% > 8% |

## 第四次优化：阈值宽松校准（2026-05-15）

### 背景

第三轮部署硬门控后，预期信号量降至原来 20-30%。用户要求适度放宽不伤胜率的门控。

### 修复

| 参数 | 旧值 | 新值 | 理由 |
|------|------|------|------|
| 超买过滤 (long) | `pos > 0.82` | `pos > 0.88` | 允许接近阻力位才拦截 |
| 4h trend (long) | `trend_4h < -0.8%` | `trend_4h < -1.5%` | 容忍轻度 4h 回调 |
| 4h trend (short) | `trend_4h > 0.8%` | `trend_4h > 1.5%` | 对称放宽 |

### 原则

- ✅ 可放宽：超买/超卖、4h trend——不直接决定方向质量
- ❌ 不可碰：盘口方向 book gate、discover ±10% 天花板、止损宽度——碰了胜率崩

## 第五轮：信号收紧（2026-05-15）

### 背景

risk_budget sizing 修复后每笔 notional 从 $50 跃升至 ~$1,500，用户反馈"信号有点多，质量稍微提高一些"。

### 修复

只砍最弱尾巴，不碰结构性 gate：

| 参数 | 旧值 | 新值 | 效果 |
|------|------|------|------|
| neutral_probe score floor | 82 | **85** | 弱信号要求更高 |
| non-neutral score floor | 68 | **72** | 普通信号不放松 |
| spread gate | > 25 bps | > **20 bps** | 只做流动性好的币 |

### 原则

收紧轮次只动「分数门槛」和「点差上限」——不影响结构性 gate（book 方向、discover 天花板、止损宽度）。

## 第六轮：risk_budget sizing 公式倒置（2026-05-15）

### 现象

$5,000 账户，策略三笔持仓 notional 仅 $13–$50（0.26%–0.99% 账户），实际风险 $1（0.02%）。

### 根因

`_apply_risk_budget_sizing()` 中 `risk_pct` 被直接乘到名义金额上，而非风险金额：

```python
# ❌ 旧
desired_notional = equity × risk_pct / 100    # $50
risk_amount      = max_notional × stop_pct     # $1

# ✅ 修复后
target_risk_amount = equity × risk_pct / 100   # $50（风险金额）
desired_notional   = target_risk_amount / stop_pct  # $2,500
```

详见 [risk-budget-formula-inversion.md](risk-budget-formula-inversion.md)。

### 效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 每笔 notional | $13–$50 | ~$1,500（受 effective_order_cap 限制） |
| 实际风险 | $1 (0.02%) | ~$30 (0.6%) |
| 保证金 (11x) | $1–$5 | ~$136 |

## 第七轮：Manifest Hash 静默失败（2026-05-15）

### 发现

修改策略代码后，策略进程正常运转（NATS 连接、订阅都在），但 candidate 池持续为空。排查链路：

```
修改 .py → 重启进程
→ load_enabled_strategies() 校验 code_hash 不匹配
→ 策略静默不加载 → handles = []
→ discover() 从未调用 → candidate 池永远为空
```

### 教训

1. 改策略代码后**必须更新两个 manifest 文件**的 `code_hash` 和 `code_sha256`
2. 验证命令：`python3 -c "from runtime.strategy_manager import load_enabled_strategies; h,e=load_enabled_strategies(); print(len(h),len(e))"`
3. 期望输出 `Handles: 1, Errors: 0`

详见 [manifest-hash-silent-failure.md](manifest-hash-silent-failure.md)。

```
1. 硬门控（宁缺毋滥） → 观察信号量
2. 信号量太低 → 选择性放宽非核心门控
3. 观察 1-2 天 → 重复直到信号量/胜率平衡
```

核心门控（book 方向、discover 天花板、止损）是**不可撼动的底线**。

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
