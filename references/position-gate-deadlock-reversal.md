# 仓位轮换死锁 — 市场反弹时零多单

2026-05-18 诊断。市场反弹时用户发现策略"没出多单"，原因是仓位 gate + 方向偏斜 + flow_score 三重封锁。

## 症状

```
决策分布（5分钟）：
  short  NO_TRADE  too_many_short_positions      121,492
  long   NO_TRADE  too_many_high_beta_positions     3,413

方向偏斜：122,357 short  vs  3,413 long  ≈ 36:1
阶段分布：short 有 breakout/deep_reversal，long 只有 neutral_probe
```

## 诊断路径

### 1. 确认仓位状态

```sql
-- 当前持仓
SELECT venue, symbol, position_side, quantity, notional, updated_at
FROM execution_position_basis;

-- API 快照
GET /api/v1/agent/portfolio/snapshot  — 查 equity、open_positions、total_exposure
GET /api/v1/agent/positions           — 查每个仓位的 unrealized_pnl
```

### 2. 确认两个 gate 同时封死

```sql
SELECT reason, side, evidence_json->>'stage' as stage, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '5 minutes'
GROUP BY reason, side, stage
ORDER BY cnt DESC;
```

关键 gate 检查链（`_portfolio_concentration_gate()`）：

```python
# 代码位置：strategy/strategies/candidates/workflow_distilled_funnel_0_1_0.py
# 行 ~614-639

MAX_DIRECTIONAL_POSITIONS = 2
MAX_HIGH_BETA_POSITIONS = 2
MAX_HIGH_BETA_TOTAL_EXPOSURE_PCT = 180.0

# 方向封顶（行 626-628）
side_count = sum(1 for pos in open_positions
    if pos.get("side") or pos.get("position_side") == state["side"])
if not opposite_side and (not has_position or same_side) and side_count >= 2:
    → too_many_{side}_positions

# 高β封顶（行 634-637）
high_beta_count = sum(1 for pos in open_positions if _is_high_beta_position(pos))
current_is_high_beta = abs(signed_change) >= 4.0 or stop_pct >= 0.04
if current_is_high_beta and not has_position and high_beta_count >= 2:
    → too_many_high_beta_positions

# _is_high_beta_position（行 648-650）：
# 所有非 BTC/ETH 的 USDT 永续合约 → 高β
def _is_high_beta_position(pos):
    symbol = str(pos.get("symbol") or "").upper()
    return symbol.endswith("USDT") and not symbol.startswith(("BTC", "ETH"))
```

### 3. 确认 flow_score 杀死 long 侧

```sql
SELECT
  AVG((evidence_json->>'candidate_score')::numeric)::numeric(6,2) as avg_cscore,
  AVG((evidence_json->>'flow_score')::numeric)::numeric(6,2) as avg_fscore,
  AVG((evidence_json->>'score')::numeric)::numeric(6,2) as avg_score,
  AVG((evidence_json->>'signed_change')::numeric)::numeric(6,2) as avg_sc,
  AVG((evidence_json->>'directional_book')::numeric)::numeric(6,2) as avg_db,
  COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND side = 'long'
  AND created_at > now() - interval '5 minutes';
```

典型 long reversal 候选：
```
setup:      distilled_reversal_long
setup_score: 99.79  ← 完美反转候选
flow_score:   0.00  ← 被动量公式打零分
score:       66.89  ← 远低于 gate 门槛（~82）
signed_change: -28% ← 正确的深跌反转
direction_book: -0.45 ← 卖方主导（对的时机）
```

## 三重封锁拆解

| 层 | 内容 | 阻断 | 根因 |
|-------|------|------|------|
| **discover()** | 36:1 short:long 产出偏斜 | 候选源头就偏 | discover() 在市场反弹时仍只找下跌币 |
| **position gate** | `too_many_high_beta_positions` | 3,413 long 候选全拦 | MAX_HIGH_BETA_POSITIONS=2 不区分方向，持有 2 个 short alt 时反向开多被拒 |
| **flow_score** | long 侧 flow_score=15.14 | gate 放行也过不了分 | 反转感知 fix 只做了 short 侧（breakout/deep_reversal 出现），long 侧仍未修复 |

## 修复方向

### 验证已部署方案（2026-05-18 上线并验证）

实际部署的固定值（而非仅方向描述）：

**Fix 1**: `MAX_HIGH_BETA_POSITIONS = 2 → 3`

**Fix 2**: `_portfolio_concentration_gate()` 高β cap 增加 opposite_side 豁免
```python
opposite_side_exempt = opposite_side and high_beta_count >= MAX_HIGH_BETA_POSITIONS
if current_is_high_beta and not has_position and high_beta_count >= MAX_HIGH_BETA_POSITIONS and not opposite_side_exempt:
    return False, "too_many_high_beta_positions"
```

**Fix 3**: `_stage()` long deep_reversal 条件放松
```
旧: pos_1h < 0.25 and directional_book > 0.02  and bias > -0.15
新: pos_1h < 0.80 and directional_book > -0.35 and bias > -0.25
```

**Fix 4**: `_stage()` long pullback_reversal 条件放松
```
旧: pos_1h < 0.40 and directional_book > 0.01  and bias > -0.12
新: pos_1h < 0.80 and directional_book > -0.25 and bias > -0.20
```

#### 验证结果

修复上线后 35 秒内：
```
long deep_reversal     416条  avg_score=89.04  ← NEW! 之前0
long pullback_reversal   6条  avg_score=88.79  ← NEW! 之前0
long neutral_probe    2,591条  avg_score=66.41  ← 未变，正常
→ ZEREBROUSDT long $0.023865 已执行
```

**关键认知**：long 侧深跌 5%+ 的币（signed_change -11% 到 -28%），book 自然极卖空（db=-0.30 到 -0.45）。等待 db>0 才入场＝永远不入。必须允许 db 为负，用 `signed_change < -5.0` 作为主要过滤。

### 固定策略（解除死锁）

- **MAX_HIGH_BETA_POSITIONS=2 → 3**：与 `max_positions=3` 对齐，让反向方向有进入空间
- **或** 在 `_portfolio_concentration_gate()` 中给 `opposite_side` 豁免 high_beta cap：
  ```python
  # 已持 short 时允许开 long，不计入高β上限
  if opposite_side and not has_position:
      pass  # 绕过 high_beta_count 检查
  ```

### 第二波修复：单一方向死锁（long 侧满仓后仍浪费优质机会）

即使 dual-gate deadlock 解除，`MAX_DIRECTIONAL_POSITIONS=2` 在 $99 账户上仍然过于保守：

```
修复前（5分钟）：
  long  too_many_long_positions   44,276  ← MAX_DIRECTIONAL_POSITIONS=2 挡住

修复后（MAX_DIRECTIONAL_POSITIONS=3）：
  long  too_many_long_positions    1,227  ← 降 97%，仅有竞争窗口残留
  → DOGEUSDT short 立即成交并浮盈 +$0.14
```

**方向限制必须适配账户规模**：

| 账户规模 | MAX_DIRECTIONAL_POSITIONS 建议 | 总名义本金上限（avg $30/单） |
|----------|:------------------------------:|:--------------------------:|
| $50-100  | 3 | $90 = 90-180% equity |
| $200-500 | 2 | $60 = 12-30% equity |
| $500+    | 2 | $60 = <12% equity |

经验公式: `MAX_DIRECTIONAL_POSITIONS * avg_notional <= equity * 1.5`
$99 账户: 3 * $30 = $90 < $99 * 1.5 = $148 → 合理.

### 反转入场仓位天然小（用户常见困惑）

用户问"为什么仓位好低"——这是数学必然:

```
risk_pct = 2.1%  →  target_risk = $99 * 2.1% = $2.08
stop_pct = 7.2%  →  desired_notional = $2.08 / 0.072 = $27-33
```

| 阶段 | stop_pct | 原因 |
|------|:--------:|------|
| breakout | 4% (tight) | 动量入场，应即走 |
| deep_reversal | 7-8% (wide) | 反转需要"转"的空间 |
| neutral_probe | 6-8% | 弱信号 + 宽止损 |

**反转 vs 动量 = 宽止损 vs 紧止损 = 小仓位 vs 大仓位**, 不是 bug.

### Gate 连锁反应模式（每次修复都会有新 bottleneck）

修复中间 gate 时下层 gate 自动成为新瓶颈:

```
Fix 1: too_many_high_beta_positions 解除
  → 44,276 个 long 候选被 too_many_long_positions 挡住（方向上限）

Fix 2: MAX_DIRECTIONAL_POSITIONS=2→3
  → 剩余被 same_side_add_disabled_by_risk_config（max_add_count=0）挡住
```

每个 gate 解除后必须重新 query decision_logs 确认新瓶颈。预期行为，不要惊慌回滚。

### 加仓限制（same_side_add_disabled_by_risk_config）

live 策略 risk_allocations 设 `max_add_count=0`，有持仓时加仓被拒。$99 小账户不需要加仓（仓位本身已很小）。

### 中期（评分修复）

- **long 侧 flow_score 反转感知**：`stage == "deep_reversal"` 时用 `abs()` 包裹 directional_book 和 change_bonus
- long 侧需要有与 short 侧对称的 stage 分类（`deep_reversal` / `pullback_reversal`），而不仅仅是 `neutral_probe`

### 长期（避免死锁）

- **仓位轮换**应在亏损时也能触发（当前只轮换盈利仓），或增加"最大亏损时间"强制退出

## 相关参考

- [flow-score-reversal-handicap.md](flow-score-reversal-handicap.md) — 公式与 abs() 修复方案
- [position-rotation-deadlock.md](position-rotation-deadlock.md) — 分数天花板 vs 绝对阈值的死锁
- [position-rotation.md](position-rotation.md) — PnL 梯度轮换
- [btc-regime-graduated-penalty.md](btc-regime-graduated-penalty.md) — 市场 regime 门禁
