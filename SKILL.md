---
name: zuoge-crypto-strategy
description: "用于编写、校验、回测并自动投递实时策略候选到生产收件箱；生产审批和启用由人工完成。"
---

# zuoge-crypto-strategy

本技能面向渐进式漏斗实时策略架构。策略分两阶段：

- `discover(universe)` 消费 `strategy.universe.delta` 的全市场轻量数据，只输出 side-aware candidates。
- `build_signals_from_context(context)` 消费已预热的 `strategy.context.delta.{symbol}`，只在完整行情依赖满足后输出标准 `StrategySignalEvent`。

候选池由系统托管但按策略隔离，候选唯一键为 `(strategy_id, symbol, side)`。同一 symbol 可以同时被不同策略或不同方向选中；完整行情订阅由系统按 symbol/dependency 合并，方向冲突交给 risk/execution。

## 🔴 策略源码禁止主动调用 API

策略实现文件（尤其是 `strategy/strategies/candidates/*.py`）只能消费运行时传入的数据：

- `discover(universe)` 只能读取 `universe`。
- `build_signals_from_context(context)` 只能读取 `context`。
- 账户、持仓、预算、风险限制、symbol metadata、owner/runtime 状态等信息，只能来自系统注入的 `context`、NATS context delta 或后端缓存/数据库链路。

**严禁**在策略源码里使用 `requests`、`urllib`、`httpx`、`aiohttp`、Binance SDK/REST、Agent API、网页 API 或任何网络请求去查询账户、持仓、余额、预算、position risk、exchange info 或策略上下文。也不要在策略里调用 `/api/v1/agent/strategy/context/*`。

如果策略缺少账户/预算字段，正确做法是修运行时 context 生产链路或后端缓存读取链路，而不是让策略自己补查 API。策略层必须保持纯函数式、无网络副作用，避免生产环境触发 REST 频控、IP ban 或和 testnet/live 行为不一致。

## 开始前必须先做

1. 确认项目根目录。优先使用当前工作区；否则读取 `ZUOGE_CRYPTO_PROJECT_ROOT`；目录内必须存在 `cmd/crypto-skill/main.go` 和 `strategy/runtime/strategy_sdk.py`。
2. 切换到项目根目录工作。候选策略登记、检查、测试、回测、报告和候选包投递都通过 `crypto-skill` 操作；本地阶段使用本地源码目录和本地开发数据库。
3. 本地候选操作不要求 API 服务启动；如果当前 shell 没有 `DATABASE_URL`，`crypto-skill` 会读取项目根目录 `.env.dev`。默认只允许连接库名以 `_dev` 结尾的开发库。
4. 只有查询实时能力目录、实时 context、订阅状态、决策日志这类运行态信息时，才调用 Agent API。

调用 API 时只能使用 `/api/v1/agent/...` 路由。不要使用网页控制台 cookie，不要调用普通 `/api/v1/...` 路由。

### 🔴 源码 vs 生产：绝对禁止直接改生产部署文件

| 可以改 | 禁止改（除非后备流程） |
|--------|------------------------|
| `$ZUOGE_CRYPTO_PROJECT_ROOT/strategy/strategies/candidates/*.py` | `/opt/homebrew/var/crypto-trader/strategies/enabled/*.py` |
| 项目根目录下所有源码 | `/opt/homebrew/var/crypto-trader/strategies/candidates/*.py` |
| | `/opt/homebrew/var/crypto-trader/releases/*/strategy/**/*.py` |

**规则：所有策略代码修改只发生在项目源码根目录。** 生产部署目录（`/opt/homebrew/var/crypto-trader/`）只在标准管线（`crypto-skill candidate publish` 或 `deploy-current`）或「生产直接修改策略文件的完整步骤」后备流程中由工具自动写入，**永远不手动编辑**。

常见犯规场景：看到 `/opt/homebrew/var/crypto-trader/strategies/candidates/` 下有同名文件就直接改——这是生产部署镜像，不是源码。真正的源码在 `$ZUOGE_CRYPTO_PROJECT_ROOT`。

### 🔴 数据查询：只用生产数据库真实数据，绝不拍脑袋

账户余额、持仓 notional、`max_order_notional_pct` 等后端参数**只能从生产 DB 查询**。代码中的 fallback 默认值（如 `strategy_sdk.number(risk_limits.get("max_order_notional_pct"), 40)` 里的 `40`）不代表后端实际配置。

**错误示例**：推测账户 $500，基于此做仓位分析 → 完全不成立。
**正确做法**：`SELECT equity, total_exposure, open_positions FROM portfolio_snapshots ORDER BY updated_at DESC LIMIT 1`

### 🔴 策略代码与数据边界：禁止调 API 读币安数据

- **修改策略时禁止调 Agent API 读账户/余额/持仓。** 账户数据从生产 DB 查（`portfolio_snapshots`、`strategy_risk_allocations`）。
- **策略代码内禁止直接调 Binance API。** 策略只通过 SDK/context 获取行情数据，不自己访问交易所。
- 策略诊断查运行时状态（candidate pool、context overlay、decision logs）可以用 `/api/v1/agent/...`，但**不用于账户查询**。

## 策略胜率优化方法论

⚠️ **先修评分公式，再调 gate 阈值。**

### 🔴 方向变换值验证（最高优先级）

`directional_book` 和 `signed_change` 经过 `direction`（long=+1, short=-1）变换后，比较运算符语义可能反转。语法对称 ≠ 语义正确。**每次涉及这些值的审查必须执行三步验证**：写出原始语义 → 方向变换 → 验证代码。

**诊断信号**：当某一侧 100% 落在 `neutral_probe` 阶段时，立即怀疑方向条件写反：

```sql
SELECT evidence_json->>'stage' as stage, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND side = 'short'
  AND created_at > now() - interval '10 minutes'
GROUP BY stage;
```

常见结构性陷阱：
- **`_stage()` 做空 reversal 条件写反**：注释写「币涨 5%+」但 `signed_change > 5` 对做空 = 币跌 5%。正解为 `signed_change < -5.0`。
- **`_stage()` reversal book 时机偏晚**：`directional_book > 0.02` 要求卖方已主导才入场做空 reversal，但真 reversal 应在买方仍主导时早入场（`directional_book < -0.02`）。等 book 翻转了才入场说明 reversal 已过半。
- **`_stage()` long 侧 reversal 条件同样偏晚**：long deep_reversal 要求 `directional_book > 0.02 and pos_1h < 0.25`——深跌 28% 的币 book 自然极卖空（db=-0.35~-0.45），且在 1h candle 内反弹后 pos_1h 可达 0.8+。条件永不过，应放宽到 `db > -0.35 and pos_1h < 0.80`。
- **`_trade_gate()` book gate 做空写反**：`directional_book > 0.05` 拒做空→卖方主导被误杀。正解为 `< -0.05`（买方主导时拒）。
- **`_funding_penalty` 方向写反**：正资金费 = longs pay shorts，对做空有利。`funding > 0.0015 AND side=="short"` 是错误惩罚——正资金费该罚做多。正解：`side=="long" AND funding > 0.0015` 罚做多，`side=="short" AND funding < -0.0015` 罚做空。详见 的 funding_penalty 案例。
- **`neutral_probe` 止损放大→止盈膨胀**：`stop_pct × 1.5` 后 TP = stop × 1.5/2.2，导致止盈距离翻倍。
- **`flow_score` 奖励追涨**：`signed_change * N` 线性系数让拉得最凶的币得最高分。应改为奖金衰减（≤5% 线性，5-15% 衰减到 0，≥15% 不奖）。参见。
- **`_stage()` 方向错误——动量追涨而非反转抄底**：所有阶段都要求 `signed_change > 0`（币已按信号方向移动），本质是追涨杀跌。专业做法是反转优先：币大跌→抄底做多，币大涨→摸顶做空。
- **`candidate_score`/`move_score` 奖励大波动**：`log1p(abs(change))` 不区分方向，涨 20% 和跌 20% 得同分。
- **flow_score 反转惩罚**：`flow_score = 50 + directional_book * 180 + change_bonus * 3` 本质动量跟随评分，反转候选的 directional_book 和 change_bonus 均逆向 → flow_score 打 20-30 分。即使 stage 分类正确（deep_reversal +10），总分也不到 trade gate 门槛 72。
- **缺少超买/超卖过滤**：`position_in_range` 来自 1h/4h kline，>0.88 不做多，<0.12 不做空。
- **缺少大趋势确认**：做多要求 4h `trend_return_pct > -1.5%`，做空要求 `< 1.5%`。
- **缺少市场 regime**：BTC 跌 >1.5% 时不做多 alts；BTC 涨 >1.5% 时不追空。

### "全部 NO_TRADE / 全部 neutral_probe" 诊断流程

当 decision_logs 显示 100% neutral_probe 且无其他 stage 时，按此 4 层链路排查：

1. **查 stage 分布 + side 分布**
   ```sql
   SELECT side, evidence_json->>'stage' as stage, COUNT(*) as cnt
   FROM strategy_decision_logs
   WHERE strategy_id='workflow_distilled_funnel'
     AND created_at > now() - interval '5 minutes'
   GROUP BY side, stage;
   ```

2. **查 signed_change 分布**（识别候选来源——做空大跌币 vs 做空大涨币）
   ```sql
   -- short 侧: signed_change > 0 = 做空大跌币（动量跟随候选）
   --          signed_change < 0 = 做空大涨币（反转候选）
   -- 如果 100% positive → 市场在跌，无涨的币可供反转做空
   SELECT CASE 
     WHEN (evidence_json->>'signed_change')::numeric < -5.0 THEN 'deep_reversal_range'
     WHEN (evidence_json->>'signed_change')::numeric < -2.0 THEN 'pullback_range'
     WHEN (evidence_json->>'signed_change')::numeric < 0 THEN 'negative_mild'
     ELSE 'positive' END as sc_range,
   COUNT(*) as cnt
   FROM strategy_decision_logs 
   WHERE strategy_id='workflow_distilled_funnel'
     AND side='short'
     AND created_at > now() - interval '5 minutes'
   GROUP BY sc_range;
   ```

3. **查 bottleneck gate**
   ```sql
   SELECT reason, COUNT(*) as cnt
   FROM strategy_decision_logs
   WHERE strategy_id='workflow_distilled_funnel'
     AND created_at > now() - interval '5 minutes'
   GROUP BY reason ORDER BY cnt DESC LIMIT 5;
   ```

4. **trace 具体候选的分值链**（从 SIGNAL 事件的 evidence_json 提取）
   - 人工计算 `score = candidate*0.30 + setup*0.25 + liq*0.15 + flow*0.20 + stage_bonus - penalties`
   - 如果 flow_score 贡献分远低于其他项 → flow_score reversal handicap
   - 对比 trade_gate 中的得分检查点（neutral_probe score < 83 → reject）

### 阈值校准节奏

门控从紧到松的迭代模式：
1. **第一轮**：部署硬门控（0.82 超买、±0.8% trend），宁缺毋滥
2. **观察 1-2 天**：信号量是否过低
3. **第二轮**：适度放宽（0.82→0.88、±0.8%→±1.5%）——只放宽不伤害胜率的门；**绝不碰** 盘口方向 gate、discover ±10%、止损宽度
4. 重复观察→调参直到信号量/胜率平衡
5. **第三轮**（信号过多时收紧）：neutral_probe floor 82→85、stage floor 68→72、spread gate 25→20bps。只砍最弱尾巴，不碰结构性 gate。

**第四轮（gate 微调 + 效率优化）：**
- `breakout` structure_bias gate: 0.05 → 0.0（只要求不为负，释放 ~17% 拒绝）
- `neutral_probe` score floor: 85 → 82（从 17% 拒绝中回收边缘信号）
- 信号冷却 key 从 `symbol:side` 细化为 `symbol:side:setup_id`，不同 setup 不互锁
- `trailing_stop` 激活从 1.0x → 1.5x stop 距离，利润多跑 50%

**第五轮（gate 分拆 + 时间放宽）：** 当 `breakout_without_1h_4h_confirmation` 仍占 17%+ 拒绝时，把 gate 按 stage 分拆——`accepted_breakout` 保持 bias≥0，`expansion_continuation` 放宽到 bias≥-0.08。book gate 从 ±0.03 扩到 ±0.05（回收 21% 拒绝）。加仓浮盈门槛 1.5%→1.0%。信号过期多时：signal expire_ms 60s→90s + entry expire 45s→60s。关键原则：**不同 stage 不同 gate 阈值**，不要一刀切。

**第六轮（spread 分级 + 冷却 + 流动性）：** spread gate 不要一刀切 20bps——breakout 保持 20，其它放宽到 25。加 per-symbol 300s 冷却防止同一币频繁发信号（前 3 币占 58%→分散）。quote_volume 门槛从 25M→100M（discover）和 20M→60M（trade_gate），配合 liquidity_quality divisor 4M→10M。一个 gate 放宽后其他 gate 会成为新瓶颈——这是正常连锁反应，不要惊慌回滚。

**第七轮（BTC regime 柔性化 + stage 多样性）：** BTC 跌 >1.5% 全杀 long 太粗糙。改为梯度惩罚：BTC 0-2% 不罚，2-5% 按比例扣 setup_bias（0→12），>5% 极端才全停。`sweep_reclaim` stage 从死代码复活（多头深跌+超卖+book 翻多，空头急涨+超买+book 翻空）。`early_trend` bonus 0→2，book ≥0.0→≥-0.01，且排在 trend_pressure_build 之后只捡 [0.2,0.5] 过渡区。pullback_reaccept book 0.05→0.03。

**第八轮（flow_score 反转感知 + stage_bonus 重构）：** flow_score 对反转入场施加结构性惩罚（公式 `50 + directional_book*180 + change_bonus*3` 本质动量跟随）。修复：反转阶段用 abs() 代替原 directional_book 和 change_bonus，让逆向持仓系数变为加分。stage_bonus 同步拉高：deep_reversal +10→+15，pullback_reversal +7→+10。

**第十轮（小账户激进步进 + 移动止盈收紧）：** 用户明确要求"以小博大"。3 次同时调整：(1) risk_pct base 1.0→2.0, coeff 0.06→0.08, max 3→8；(2) deep_reversal trailing_stop activation/trail_width 从 ×1.25 收紧至 ×0.5；(3) directional_pos 2→3 + opposite_side 豁免。

**第九轮（亚洲盘分类器 + discover 效率 + 仓位管理）：** 数据驱动的大修。从 6.5M 决策日志/天分析发现三大问题并一次性修复。(1) **亚洲盘（09:00-17:00 北京）100% neutral_probe** → 放松 signed_change 阈值（5.0→2.5 reversal，3.0→1.5 breakout），(2) **discover() 持仓盲视** → `_candidate_already_held()` 检查 `_positions_cache`，`max_add_count>0` 时放行让加仓门禁评估，(3) **short 方向 4.5:1 偏斜** → `_enforce_side_balance()` 在 `_balanced_select()` 中每轮检查 side 分布，>70% 单边时强制补入少数方，(4) **仓位满后候选浪费** → `_discover_limit()` 在持仓≥3 时 limit=2，(5) **时间止损 + 硬冷却** → `_position_entry_at` 追踪开仓时间，>60min 无盈利强制平仓；后因 deep_reversal 需要更多时间验证底部放宽到 240min，再经 **生产持仓分析**（2026-05-19：ATUSDT deep_reversal +3.5% 证明 240min 合理，GOATUSDT/XVGUSDT breakout -2% 证明 240min 太长）收敛为 **stage 差异化**：deep_reversal=240min、breakout=120min、其他=180min。`_hard_cooldown_until` 在亏损平仓后 15-30min 阻断该 symbol 候选，

**第十一轮（前置的持续优化）：** 固定周期检查 `strategy_decision_logs` 的 reason 分布，定位当前占比最高的瓶颈 gate，然后单点修复 → 重新观察 → 修复连锁反应。此方法比凭感觉调整更高效。

**第十二轮（信号冷却竞态 + 分数天花板 + 亚洲盘柔性）：** 生产运行发现 4 个隐藏漏洞：(1) **信号冷却竞态条件**——NATS 异步事件循环中多协程同时调用 `build_signals_from_context()`，所有协程在写 `_last_signal_symbol_at` 之前读取旧值，600 秒冷却完全失效，同一标的 5 分钟内连发 8 次信号。修复：`threading.Lock` 包裹冷却检查+记录，保证检查-写原子性。(2) **score 天花板 100**——高分 deep_reversal 信号一律饱和在 100，无法区分 85 分和 105 分。修复：天花板提升到 120，confidence 上限同步到 0.98。(3) **亚洲盘 book gate 78% 误杀**——低频波纹态的 book_imbalance 噪音导致 `directional_book < 0` 频繁触发。修复：亚洲盘 book 门槛从 `0.0` 放宽到 `-0.015`。(4) **亚洲盘 score floor 过高**——`neutral_probe_too_weak` 拒绝 882 次/分。修复：亚洲盘 neutral_probe floor 85→82，stage floor 72→68。

**第十三轮（独立持仓监控系统 discovery→gate 死代码修复）：** 分析发现 `_candidate_already_held()` 阻断了持仓退出的评估路径。`_maybe_close_position()`（时间止损、亏损退出、反方向退出）只在新候选通过 trade_gate 后触发——但 `_candidate_already_held()` 阻止了已持仓 symbol 产生任何候选 → 持仓管理全是死代码。修复：(1) discover() 完成后额外为每个持仓发出 `position_monitor` 候选（score=1, setup_id="position_monitor", ttl=15s），每 60s 限流一次；(2) `build_signals_from_context()` 在进入正常流程前检测 `setup_id == "position_monitor"` → 跳过 warmup/gate → 直接调用 `_maybe_close_position()`；(3) `_seed_entry_times()` 从 `execution_position_basis.updated_at` 还原 `_position_entry_at`（解决进程重启丢失时间）。

**源头收紧 > gate 加码**：discover() 多放一个弱 candidate，context delta 每秒触发多次评估链。优先从源头砍弱 candidate（score floor、limit、动态 TTL），减少 context 评估总量。

### ⚠️ 修改后必须提交

**任何**对 skill 文件（SKILL.md、references/*、templates/*）的修改后，立即执行：

```bash
cd /Users/zuo/.hermes/skills/zuoge-crypto-strategy && git add -A && git commit -m "skill: <简述>" && git push
```

不要等用户提醒。策略代码修改走项目 repo（**仅读取/编辑，不提交**，见下方 Git 权限边界）。

### ⚠️ Git 权限边界

- **✅ 可以提交推送**：`/Users/zuo/.hermes/skills/zuoge-crypto-strategy`（本 skill 仓库）
- **🚫 禁止提交推送**：`/Users/zuo/Documents/projects/crypto-trader/`（用户项目仓库，由用户自行管理 git）

策略代码可以读取、编辑，但不得 `git add/commit/push`。

## 可移植安装

本技能可以复制到 Codex、Claude、Cursor 或其他工具的技能目录使用。安装位置不要求在项目仓库内，但执行策略任务时必须进入项目根目录，且命令 `crypto-skill` 必须可用；如不可用，使用 `go run ./cmd/crypto-skill ...` 等价执行。

所有相对引用都以本技能目录为基准读取，例如 `references/`、`templates/`、`generated/`。不要把技能安装目录误认为项目根目录。

## 工作流

> **🔴 硬规则 0：修改前先验证——现有代码是否已经满足需求。** 用户说「最后一次止盈全清」≠ 梯子要改。`close_ratio: "1.0"` 本身就是全清哨兵值。**先读代码确认语义，再决定是否改。** 本次 session 的 TP 梯子回滚就是反面教材。
>
> **🔴 硬规则 1：所有策略修改必须走此工作流。** 不要跳过步骤直接修改生产 enabled 文件。若已修改的文件已是生产策略文件，也必须回填到候选目录、补走 check/test/backtest。
> 
> **🔴 硬规则 2：永远只编辑项目源码根目录下的文件。** 源码在 `$ZUOGE_CRYPTO_PROJECT_ROOT/strategy/strategies/candidates/`。`/opt/homebrew/var/crypto-trader/` 下的同名文件是生产部署镜像——管线下游产物，不手动编辑。
>
> **🔴 硬规则 3：生产 DB 值是权威信源。** 不要用代码里的 fallback 默认值（如 `, 40`）去估算后端配置。后端参数（`max_order_notional_pct`、`max_positions` 等）存在 `strategy_risk_allocations` 表，修改前先 `SELECT`。
>
> **🔴 硬规则 4：全文件替换策略代码时，必须保留 `_log` 方法。** 这个独立工具方法在多次重写中被遗漏，导致运行时 `'Strategy' object has no attribute '_log'` 错误，所有决策日志输出中断。`_log` 调用散布在 `build_signals_from_context()` 全流程（13+ 处调用），缺了它策略看似加载了但决策链完全瘫痪。**修复确认步骤**：部署后查 `strategy_decision_logs`，如果数量为零或只有 `'_log' AttributeError`，说明 deploy 失效或 `_log` 缺失。
>
> **🔴 硬规则 5：所有生产部署必须经用户明确确认。** 不可在用户未要求或未确认的情况下，将任何策略代码、Go 后端代码、配置文件复制到生产目录、重启进程或刷新服务。即使改动看起来微小（如修注释、调参数），也必须先说明改动内容、影响范围、并等待用户说"部署"或"可以"。用户说"禁止部署"后立即停止所有部署动作。
>
> **🔴 硬规则 6：不修改 Go 后端代码。** 策略代码是 Python 策略文件（`strategy/strategies/candidates/*.py`）。Go 后端（`cmd/`、`internal/` 目录下的 `.go` 文件）的修改不在本 skill 的工作范围内。如果发现需要 Go 后端配合（如添加 `updated_at` 到 positionMap、修改 context 富化逻辑），向用户说明需求，等待用户自行处理。
>
> **🔴 硬规则 7：策略代码禁止查询生产数据库。** 策略运行在 Python 进程中，不应包含 `subprocess`、`psycopg2` 或任何直接连接到数据库的代码。所有持仓、账户、风控数据必须通过 context overlay（Go 后端 → NATS → Strategy SDK）获取。策略诊断时可以通过 psql 命令行手动查询数据库作为外部工具，但策略自身的 `discover()` 或 `build_signals_from_context()` 代码中不得包含数据库查询。
>
> **🔴 硬规则 8：排查跨语言边界信号链时，先读 Go 源码，不要从 DB 日志反推后端行为。** Python 策略→NATS→Go ingress→DB 的信号链路中，后端验证逻辑在 Go 源码（`internal/strategyingress/service.go`、`internal/worker/`）中定义。**禁止**从 signals.status 或 strategy_signal_rejects 反推后端逻辑——你只能看到"被拒"的事实，看不到"为什么被拒"的源码逻辑。正确做法：`grep -rn "RewardRiskRatio\|mapIntent\|func.*validate" internal/` 定位相关函数直接读 Go 代码。
>
> **🔴 硬规则 8：排查跨语言边界信号链时，先读 Go 源码，不要从 DB 日志反推后端行为。** Python 策略→NATS→Go ingress→DB 的信号链路中，后端验证逻辑在 Go 源码（`internal/strategyingress/service.go`、`internal/worker/`）中定义。**禁止**从 signals.status 或 strategy_signal_rejects 反推后端逻辑——你只能看到"被拒"的事实，看不到"为什么被拒"的源码逻辑。正确做法：`grep -rn "RewardRiskRatio\|mapIntent\|func.*validate" internal/` 定位相关函数直接读 Go 代码。

在项目根目录内工作：

1. 需要运行态能力目录时，`crypto-skill capabilities show --format json`。
2. 按需读取。
3. `crypto-skill research create --focus "<研究目标>"`
4. 基于 capabilities 和 SDK 编写候选策略到 `strategy/strategies/candidates/`。
5. `crypto-skill candidate new --strategy <id> --version <x.y.z> --file <candidate.py> --research <research_id>`
6. `crypto-skill candidate check --candidate <candidate_id>`
7. `crypto-skill candidate test --candidate <candidate_id>`
8. `crypto-skill candidate backtest --candidate <candidate_id>`
9. `crypto-skill candidate report --candidate <candidate_id>`
10. 只有 check/test/backtest 都通过时，`crypto-skill candidate publish --candidate <candidate_id>`，把候选包投递到生产 Strategy Center 收件箱，然后停止等待人工审批。

`candidate new` 只用于首次把候选策略文件登记到策略中心并取得 `candidate_id`。如果用户要求检查、测试或回测一个已经登记过的策略，先用 `crypto-skill candidate list` 或 `crypto-skill candidate show --candidate <candidate_id>` 找到已有 `candidate_id`，然后直接运行 `check`、`test`、`backtest`、`report`；不要为同一个 `strategy_id` 和 `version` 重复执行 `candidate new`。

## 深度代码审查清单（部署前必做）

重大策略修改（尤其是全文件重写）后，部署前逐项验证：

- [ ] **`_log` 方法存在**：`grep -n "def _log" strategy/strategies/candidates/<strategy>.py`——确认返回行号，不为空。
- [ ] **所有 `self._log()` 调用不抛异常**：在测试环境中跑一次 `build_signals_from_context()`，检查 `decision_logs` 非空。
- [ ] **无意外新增的 symbol 过滤器**：`_symbol_allowed()` 中的新条件可能误杀合法交易对（如 `1000/10000` 前缀过滤）。对照已有交易记录验证。
- [ ] **`_positions_cache` 解包正确**：如果增删了 `_positions_cache` 的元组元素（例如从 `(ts, positions)` 改为 `(ts, positions, fit)`），所有读其的 `_fetch_strategy_positions()`、`_candidate_already_held()`、`_discover_limit()` 必须同步更新。
- [ ] **闭包变量作用域检查**：`_balanced_select()` 内的 `_enforce_side_balance()` 闭包修改 `selected` 和 `seen`——确认 `nonlocal` 不需要（Python 闭包在可变对象修改上不需要 `nonlocal`，但赋值需要）。
- [ ] **discover() 调用路径完整**：`self._discover_limit()` 返回整数且 `_balanced_select()` 正确使用。
- [ ] **AST 解析检查**：`python3 -c "import ast; ast.parse(open('...').read())"`——确保无语法错误。
- [ ] **已持仓 symbol 的 candidate 行为正确**：`_candidate_already_held()` 在 `max_add_count=0` 时返回 True，`max_add_count>0` 时返回 False。
- [ ] **硬冷却不阻断首次交易**：`discover()` 中 `_hard_cooldown_until` 初始化时 `{}`→第一次迭代正常通过。
- [ ] **时间止损追踪正确**：`_position_entry_at` 只在 `not position["has_position"]`（新开仓）时记录，加仓时不覆盖。
- [ ] **部署后验证**：`sql="SELECT reason, COUNT(*) FROM strategy_decision_logs WHERE ... GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 5;"`——确认 `_log` 错误不在其中，且策略产生决策。
- [ ] **`_portfolio_concentration_gate` 方向饱和检测**：简化 gate 代码时，`if not has_position and side_count >= MAX_DIRECTIONAL_POSITIONS` 会跳过加仓的方向检测。应同时检查 `has_position and same_side` 的加仓情况。
- [ ] **`strategy_sdk.candidate()` score 截断**：SDK 的 `candidate(score=...)` 内部 clamp 到 [0, 100]，策略传入 >100 的 score 会被截断。策略在 `build_signals_from_context()` 中从多个组件重算最终 score（最高 120），所以影响可控（candidate_score 只贡献 30%）。

## 自动投递候选包到生产

默认完成校验和报告后执行：

```bash
crypto-skill candidate publish --candidate <candidate_id>
```

该命令读取 `ZUOGE_CRYPTO_BASE_URL` 和 `ZUOGE_CRYPTO_PUBLISH_TOKEN`。投递只把候选包送到生产收件箱，不会审批、不会启用、不会写 enabled、不会触发下单。投递成功后停止，由人在生产 Strategy Center 提交/审批 review。

若用户只要求离线交付候选包，可执行：

```bash
crypto-skill candidate export --candidate <candidate_id> --output tmp/ai-skill/<name>.json
```

## ⚠️ 生产直接修改策略文件的完整步骤

> **🚫 禁止直接修改生产文件作为默认操作。** 标准流程永远是「候选文件修改 → check → test → backtest → publish/deploy-current」。本节仅在 `deploy-current` 不可用、sudo 不可用、且候选已通过全部校验时，作为**最后后备手段**使用。不要在未经候选流程验证的情况下直接编辑生产策略。

当需要绕过 `deploy-current` 直接修改生产 enabled 策略文件时（例如紧急 hotfix 且标准管道不可用），必须执行以下**全部**步骤，缺一不可：

```bash
# 1. 从源码复制更新后的策略文件到生产 enabled 目录
cp $ZUOGE_CRYPTO_PROJECT_ROOT/strategy/strategies/candidates/workflow_distilled_funnel_0_1_0.py \
   /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py

# 2. ⚠️ 必须更新 manifest hash ——漏掉这步→策略静默不加载
HASH=$(shasum -a 256 /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py | awk '{print $1}')
python3 -c "
import json, glob
paths = glob.glob('/opt/homebrew/var/crypto-trader/**/workflow_distilled_funnel_0_1_0.manifest.json', recursive=True)
for p in paths:
    with open(p) as f: m = json.load(f)
    m['code_hash'] = '$HASH'
    m['code_sha256'] = '$HASH'
    with open(p, 'w') as f: json.dump(m, f, indent=2)
    print(f'  Updated: {p}')
"

# 3. 清 pycache + 重启
find /opt/homebrew/var/crypto-trader -name '__pycache__' -exec rm -rf {} + 2>/dev/null
pkill -9 -f realtime_main   # KeepAlive=true → 自动重启

# 4. ⚠️ 验证策略加载成功
cd /opt/homebrew/var/crypto-trader/current && \
ENABLED_DIR=$(ps eww $(pgrep -f realtime_main | head -1) | tr ' ' '\\n' | grep STRATEGY_ENABLED_DIR | cut -d= -f2) && \
STRATEGY_ENABLED_DIR="$ENABLED_DIR" python3 -c "
import sys; sys.path.insert(0,'strategy')
from runtime.strategy_manager import load_enabled_strategies
h,e = load_enabled_strategies()
print(f'Handles: {len(h)}, Errors: {len(e)}')
for x in e: print(f'  ✗ {x}')
for x in h: print(f'  ✓ {x.strategy_id}')
"
# 期望输出：Handles: 1, Errors: 0  ✓ workflow_distilled_funnel

# 5. ⚠️ 验证运行时决策产出（当前加载成功 → runtime 产出验证）
sleep 5
psql "\${DATABASE_URL:-postgres://zuo:@localhost:5432/crypto_trader?sslmode=disable}" -c "
SELECT reason, COUNT(*) as cnt, MIN(created_at) as earliest
FROM strategy_decision_logs
WHERE strategy_id='workflow_distilled_funnel'
  AND created_at > now() - interval '10 seconds'
GROUP BY reason ORDER BY cnt DESC LIMIT 5;
"
# 期望：有行返且无 `attribute.*_log` 错误
# 如果 decision_logs 数量为零或只有 `_log` AttributeError，说明 deploy 成功但策略代码有运行时缺陷
```

### 关于 release enabled 目录

旧版本 release 目录（pre-20260518）有 `strategy/strategies/enabled/`，策略管理器通过 `Path(__file__).resolve()` 从 release 内加载。新 release 结构下，`strategy/strategies/enabled/` 已不存在（只有 `archived/`、`candidates/`、`rejected/`）。策略通过 **`STRATEGY_ENABLED_DIR` 环境变量**指向 `/opt/homebrew/var/crypto-trader/strategies/enabled/`。

因此：
- **复制 .py 到** `/opt/homebrew/var/crypto-trader/strategies/enabled/`（顶层 enabled 目录）
- **manifest 更新**用 glob 通配路径自动覆盖所有 release 版本
- **不尝试**复制到 release 目录内的 `strategy/strategies/enabled/`（目录不存在）
- 验证时从 `STRATEGY_ENABLED_DIR` 环境变量读取路径，而非硬编码

### 如何快速诊断"策略未加载"

```bash
cd /opt/homebrew/var/crypto-trader/current && \
ENABLED_DIR=$(ps eww $(pgrep -f realtime_main | head -1) | tr ' ' '\n' | grep STRATEGY_ENABLED_DIR | cut -d= -f2) && \
STRATEGY_ENABLED_DIR="$ENABLED_DIR" python3 -c "
import sys; sys.path.insert(0,'strategy')
from runtime.strategy_manager import load_enabled_strategies
h,e = load_enabled_strategies()
print(f'Handles: {len(h)}, Errors: {len(e)}')
for x in e: print(x)
"
```
如果输出 `code hash mismatch` → manifest 未更新。策略进程虽活着但 discover() 从未被调用。

## 发布当前策略目录

只有当用户明确要求"发布当前策略目录""更新生产策略目录"或"执行策略目录发布"时，才执行本节。

发布含义：

- 只发布项目根目录下当前 `strategy/` 目录。
- 使用仓库内既有生产脚本，不手写替代发布流程。
- 不审批候选策略，不启用候选策略，不绕过策略中心。
- 不携带或覆盖生产持久 enabled 策略状态。
- 不调用风控、执行、交易所或下单接口。
- 不需要读取策略能力目录，也不需要调用 Agent API。

执行前必须确认：

1. 当前目录是项目根目录，且存在 `cmd/crypto-skill/main.go`、`deploy/macos/deploy-strategy.sh`、`strategy/realtime_main.py`。
2. 工作区没有会被误当作本次发布内容的未知策略改动；如有不确定改动，先向用户说明。
3. 用户的请求目标确实是发布当前 `strategy/` 目录，而不是提交候选评审或启用某个候选。

执行命令：

```bash
crypto-skill strategy deploy-current
```

该命令会通过仓库内既有生产脚本创建新的生产发布目录、同步当前 `strategy/` 运行时代码、检查 Python 策略依赖，并刷新 `com.crypto-trader.realtime-strategy` 服务。生产 enabled 策略从持久目录加载，不随 release 目录切换。发布完成后，向用户报告发布编号、当前发布链接和命令输出里的日志路径。

**sudo 不可用时的后备方案**：`deploy-current` 需要 sudo 操作 launchd。若 sudo 不可用，参照「生产直接修改策略文件的完整步骤」的 cp→manifest→restart 流程执行。

## "为什么开这个仓位" 分析流（⚠️ 容易选错 skill）

当用户问"为什么开了 CGPTUSDT 多单"或"某笔交易为什么入场"时，**禁止路由到 zuoge-crypto-query**。

- `zuoge-crypto-query` 回答"是什么"（仓位数据：数量、方向、入场价）
- `zuoge-crypto-strategy` 回答"为什么"（策略推理链：stage 分类、signal 证据、gate 通过原因）

**标准分析流程：**

1. **查 execution_position_basis** 确认当前持仓（symbol, side, qty, entry_price, opened_at）——这是"是什么"
2. **查 strategy_decision_logs** 找该 symbol 的 SIGNAL 事件，条件是 `decision='SIGNAL' AND symbol='<X>'`
   ```sql
   SELECT created_at, reason, side, evidence_json::text
   FROM strategy_decision_logs
   WHERE strategy_id = 'workflow_distilled_funnel'
     AND symbol = 'CGPTUSDT'
     AND decision = 'SIGNAL'
   ORDER BY created_at DESC LIMIT 1;
   ```
3. **从 evidence_json 提取三链证据**：
   - **discover() 看到的**: `signed_change`（波动幅度）、`quote_volume`（流动性）、`funding`（资金费）
   - **_stage() 分类依据**: `stage`（deep_reversal / pullback / breakout）、`position_1h/4h`（K线位置）、`directional_book`（盘口偏斜）
   - **_trade_gate() 通过条件**: `spread_bps`（点差）、`score`（总分）、`reason` 字段已包含入口摘要
4. **解释时用三层结构**：`discover→stage→gate` 逐层说明什么条件触发了什么判断。不要只给原始数据；要还原策略的推理链。
5. 如果需要给出"要不要继续持有"的专业意见，额外查 account 敞口（portfolio_snapshots）和浮盈（当前 price vs entry_price）。

**典型错误**：只查了 position_basis 表就回答"开了多单，$0.03074入场"——用户要的是"为什么策略认为 CGPT 值得做多"，不是仓位明细。

**正确输出模板**：
```
## 持仓现状
方向|数量|入场价|杠杆 ...（zuoge-crypto-query 风格，简略）

## 做多原因
① discover() 发现: signed_change=-16.4%, 流动性 $12.8B → 深跌反转候选
② _stage() 分类: deep_reversal long (pos_1h=0.56, book=0.079→买方支撑)
③ _trade_gate() 放行: score=95.1/120, spread=3.3bps → 全部门禁通过
```

## 信号推送故障排查

当用户反馈"策略发了信号但后端没收到"时，按以下链路逐级排查。不要跳步猜测。

### 1. 查链路四段

```
Python strategy → NATS (strategy.signals) → Go ingress → PostgreSQL
```

每段单独验证。

### 2. Python 侧 — 信号是否成功生成并发布

```bash
# 看 realtime-strategy 日志里有没有 {"published": ...}
grep '"published"' /opt/homebrew/var/crypto-trader/logs/realtime-strategy.stdout.log | tail -5
```

如果没有 `published`，看有没有 `VALIDATION_FAILED`、`NO_TRADE` 或 `strategy_context_overlay_failed`。

### 3. NATS 侧 — 消息是否进 stream

```bash
# 查看 stream 消息数
nats stream ls

# 查 subject 分布（strategy.signals vs strategy.signals.dead）
nats stream subjects STRATEGY_SIGNALS

# 查看 consumer 状态（已消费数、未处理数、等待 pull 数）
nats consumer info STRATEGY_SIGNALS strategy-ingress
```

### 4. Go 侧 — 看 reject 表

```sql
SELECT reason_code, COUNT(*) as cnt
FROM strategy_signal_rejects
GROUP BY reason_code ORDER BY cnt DESC;
```

### 5. 常见 reject 原因

- `invalid_json`: data_dependencies 非 RFC3339 时间戳
- `signal_validation_failed`: strategy_id 未注册或 stop_price=0
- `market_seq_too_old`: expire_ms 太小
- `min_reward_risk`: 盈亏比不达标。Go 后端 `RewardRiskRatio()` 只用第一档止盈 TP1 做校验（不是梯子加权平均）。检查 `basic_trade_params()` 中的 `min_reward_risk` 值——必须等于 `tp1_ratio`（= 1.5），不是加权平均。

### 6. 排查致命错误模式

**account_risk_budget_missing**: overlay 失败 → 全部 NO_TRADE。全量统一 reason（无其他 gate 触发）且 context API 有 budget 数据 → NATS 投递路径断裂，根因通常是 Binance API 连接问题。重启 worker 修复。
**SlowConsumer**: `strategy.universe.delta` 订阅 → `discover()` 执行过慢→事件循环饥饿。修复：`discover()` 入口加 3 秒最小间隔（`_last_discover_at` + 缓存 `_last_discover_result`），CPU 从 87% 降到 10.8%，决策率从 280/s 降到 53/s 但信号持续产出。\n**信号冷却失效（同标的重复信号）**: `strategy_decision_logs` 中同一标的 5 分钟内出现 8 条 SIGNAL。`self._last_signal_symbol_at` 在异步 NATS 事件循环中非线程安全——多协程同时读取 0 后各自发射信号。诊断 SQL：`SELECT created_at, EXTRACT(EPOCH FROM created_at - LAG(created_at) OVER (ORDER BY created_at)) as gap_s FROM strategy_decision_logs WHERE strategy_id='...' AND decision='SIGNAL' AND created_at > now() - interval '10 minutes' ORDER BY created_at;`，如果 gap_s 频繁 <120，冷却失效。修复：`threading.Lock` 包裹冷却 check+write，保证原子性。
- **signals 全 rejected（min_reward_risk 不匹配）**: strategy_decision_logs 有 SIGNAL，但 signals 表 status=rejected → 查 trade_params 中 TP 梯子价格和 min_reward_risk 约束。TP 梯子是两档（1.5x + reward_risk），加权平均 RR 常低于 min_reward_risk。
**账户满仓死锁**: total_exposure>100%, remaining_budget=0
**仓位轮换死锁（市场反弹时零多单）**: MAX_DIRECTIONAL_POSITIONS + MAX_HIGH_BETA_POSITIONS 双封顶，持仓全亏损中→无法开新仓。见
**候选池反复出同一 symbol 且持续 `same_side_add_disabled_by_risk_config` / `add_requires_min_float_profit`**:
→ discover() 未过滤已持仓 symbol。discover() 只有 universe 数据、无权访问 `context["owned_position"]`。修复：在 discover() 中读取 `self._positions_cache`，已持同方向 symbol+side 直接跳过 candidate。

**持仓管理死代码（discover 过滤切断退出路径）**:\n  → `_candidate_already_held()` 在 discover 中过滤已持仓 symbol 后，`build_signals_from_context()` 不会被触发 → `_maybe_close_position()` 永不被调用 → **时间止损、反向退出全部是死代码**。持仓管理的调用必须独立于候选流，不应依赖 discover() 产出候选。\n\n**持仓监控实现 pitfall（process restart 丢失 `_position_entry_at`）**:\n  → `_position_entry_at` 是内存变量，进程重启后被置空 → 时间止损永不会触发。修复：从 `execution_position_basis.updated_at` 通过 DB 查询还原。\n\n**持仓监控实现 pitfall（Go context overlay 缺少 `updated_at`）**:\n  → `positionMap` 在 Go 端不包含 `updated_at` 字段 → context 缓存数据无开仓时间 → `_seed_entry_times()` 无时间可播种 → `_position_entry_at` 永远为空。必须将 DB fallback 查 entry_time 作为冗余路径。见。\n\n**持仓监控实现 pitfall（速率限制器共用字典冲突）**:\n  → 当 discover() 和 build_signals_context() 共用同一个 `_last_position_monitor_at` 字典时，discover 每次发射候选都重置时间戳 → build_signals_context 的速率检查永远看到"刚被 discover 重置"→永远被限流。修复：仅依赖 discover 侧的 60s 发射间隔，不在 build_signals_context 加额外速率限制。见。

**候选池始终为空但无 exception 日志**:
  1. 先验证策略是否加载：`cd <release_dir> && python3 -c "from runtime.strategy_manager import load_enabled_strategies; h,e=load_enabled_strategies(); print(len(h),len(e))"`
  2. 若 `Handles: 0, Errors: 1 → code hash mismatch` → manifest 的 `code_hash` 字段未更新，参照「生产直接修改策略文件的完整步骤」
  3. 若 Handles=1 仍无 candidate → 查 discover() 过滤逻辑（见下方 discovery 死寂排查）

**discover() 持续报错但 SIGNAL 仍有产出**: 候选 TTL（120-300s）+ rate limit 机制让旧候选存活。discover() 失败后 pool 不更新，旧候选逐渐过期后信号停止。诊断方法：
- 查 process start time vs file modification time: `ps -o lstart= -p <PID>` vs `stat -f "%Sm" <file>`。如果进程启动早于文件部署时间 → 文件覆写竞态（策略文件在模块加载期间被覆盖）。
  - 查项目源码 vs 生产文件是否一致：`diff $PROJECT_ROOT/strategy/strategies/candidates/<file>.py /opt/homebrew/var/crypto-trader/strategies/enabled/<file>.py`
  - 确认 SIGNAL 时间戳是否早于当前 >2min：`SELECT created_at FROM strategy_decision_logs WHERE decision='SIGNAL' ORDER BY created_at DESC LIMIT 1;`
- 关键经验：`name 'time' is not defined` 类错误无法在隔离环境复现时，同代码重启后可能自动消失（文件覆写竞态）。

### 运行时健康检查

当用户问"策略是否正常"时，按 的 6 层检查清单执行：进程层→策略加载层→决策产出层→NATS 连通层→账户层→故障模式速查。核心查询：

- `strategy_decision_logs` 聚合：`SELECT reason, COUNT(*) ... GROUP BY reason` 直接揭示为什么没信号
- macOS `sample <PID>` 探查无日志进程的调用栈
- `ps eww <PID>` / `lsof -p <PID>` 确认进程环境和工作目录
- ⚠️ `subsz` 可能不显示 `nats-py` 订阅 → **decision_logs 才是进程活着的唯一可靠证据**

### 杠杆与下单金额

- **杠杆**：`pick_leverage()` 动态计算，从 `risk_limits.min/max_leverage` 读范围，按阶段/分数/波动率调参。保守阶段（neutral_probe 等）→ 固定 `min_leverage`。
- **动态下单金额**：`desired_notional = min(risk/stop, equity × max_order_pct)`，其中 `max_order_pct = risk_limits.max_order_notional_pct / 100`。后端修改后策略自动跟随，无需改代码。⚠️ 代码中的 `, 40` 只是 fallback 默认值，实际后端值必须从 `strategy_risk_allocations` 表查询——不要假设。
- **下单金额完整计算链**：`risk_pct = clamp(2.0 + (score-55)×0.06, 2.0, 4.0)`（分数动态浮动）→ `target_risk_amount = equity × risk_pct/100` → `desired_notional = target_risk / stop_pct` → 多重封顶（`effective_order_cap`、`remaining_symbol_cap`、`remaining_total_cap`、`leverage_notional_cap` 取 min）→ `quantity = max_notional / price`。改 `risk_pct` 直接影响仓位，改止损宽度反向影响仓位——止损放宽后必须同步提 `risk_pct` 否则仓位同比例缩小。
- **⚠️ 小账户 notional 地板陷阱**：`basic_trade_params()` 用 `notional = max(equity × budget_pct%, min_notional)`。对小账户（如 $100），`equity × 4% = $4` < min_notional=$10 → notional 被地板钉死在 $10。**正确公式是 `risk_amount / stop_pct`**。
- **加仓**：专业金字塔加仓 — 7 层 gate（浮盈≥1.5%、趋势续、book 撑、回调入场、阶段过滤、敞口检查、亏损保护）+ 4 级冷却分层（90/180/240/120min），budget联动 `max_add_count`（1/(1+n)递减），参数从 backend 动态读取不写死。预算联动细节见。
- **加仓评估冷却**：加仓被拒后对同一 `symbol:side` 设 300s 冷却——冷却期内跳过全部 7 层检查，直接返回 `add_in_cooldown`。实测减少 86% 无效评估。
- **效率漏斗**：discover() 源头 candidate 质量直接决定 context 评估量（50万+/h decision logs）。收紧源头（score floor↑、candidate limit↓、TTL动态化、方向预筛选）比加 gate 更有效。
- **仓位轮换**：满仓时按 PnL-梯度判定轮换（小盈需 ≥84 分，中盈 ≥82 分，大盈 ≥80 分），反转阶段 +2 虚拟加分。优先换出最小盈仓（落袋为安）。调用 `GET /api/v1/agent/positions` 获取全策略持仓做全局比较，15s TTL 缓存不 flooding。⚠️ close 信号必须手动构建（不用 signal_envelope 以免 cross-symbol price_ref 错位），且 manifest 需 `max_signals_per_candidate: 2` 防止双信号截断。排查链路见，死锁诊断见。
- **止损波动率**：双源波动率代理（24h change + 1h trend），替代单源 24h change。短时剧烈波动的币自动放宽止损，已冷却的币自动收紧。⚠️ **分母 850 陷阱**：生产验证分母 850 导致止损 0.7-1.0%，配合 11-23x 杠杆必被扫。校准值应为 150-200。
- **阶段多样性**：加 `early_trend` 过渡阶段解决全 short 单一信号问题 + long book gate 放宽 ±0.03 中性区。
- **阶段差异化参数**：止损、移动止盈、盈亏比按 stage 分化——reversal 给宽止损+runner trail，trend_continuation 给紧止损+标准 trail。避免一刀切导致的 reversal 被扫 / trend 跑不掉。
- **阶段诊断**：信号阶段分布分析、死代码检查（sweep_reclaim）、BTC regime gate 影响、stage_bonus 配置。
- **Gate 迭代校准**：query→fix→requery 循环，连锁反应观察，gate 放宽优先级排序。
- **风控参数数据库化**：所有风控参数（`max_positions`、`max_order_notional_pct`、`allocation_pct`、`max_add_count` 等）存储在 `strategy_risk_allocations` 表。Go 后端 `PortfolioStrategyContextEnricher` 通过 `allocationStore` 读取，1 秒 TTL 缓存。通过 `UPDATE strategy_risk_allocations SET ... WHERE strategy_id='...' AND venue='live'` 直接生效，无需重启进程。

### 小账户激进参数校准（"以小博大"）

$99 账户的默认参数（1% risk, 3% max, 1.25x trail）过于保守。用户明确要求"以小博大"时的校准方向：

1. **风险预算放大**：`MIN_TRADE_RISK_PCT` 从 1.0→2.0（每单最少冒 $2）、`MAX_TRADE_RISK_PCT` 从 3.0→8.0（高分信号可冒 $8）。公式斜率从 0.06→0.08，base 从 1.0→2.0。
2. **方向上限匹配**：小账户 `MAX_DIRECTIONAL_POSITIONS` 应 ≥ 目标仓位数。$99 账户方向上限定 2 → 释放 3 个仓位，`notional = 3×$30 = $90 < equity×1.5` 为经验安全线。
3. **高β上限方向感知**：`_portfolio_concentration_gate` 加 `opposite_side_exempt` 允许反方向高 β 仓位。持有 2short 时不再拦截 long 候选。
4. **trailing_stop 紧跟**：deep_reversal 的 `activation=stop_pct×0.5`（从 ×1.25 收紧）、`trail_width=stop_pct×0.5`。10% 行情可捕捉 6.7% 而非 1.6%。

**诊断信号**：用户说"仓位好低""止盈太宽""浪费机会"时，先计算 `notional = equity × risk_pct / stop_pct` 确认数学链，再调上方参数。

### 移动止盈的利润吞噬陷阱

deep_reversal stage 的 `activation = stop_pct × 1.25 + trail_width = stop_pct × 1.25` 是利润杀手。案例：

```
APRUSDT 短空: entry=$0.15951, 跌至 $0.14347 (-10.05%)
旧 trail: activation @9.375%, trail_width=9.375%
  trail_stop = $0.14347 × (1+0.09375) = $0.15692 → 只吃 1.6% 利润
新 trail: activation @3.75%, trail_width=3.75%
  trail_stop = $0.14347 × (1+0.0375) = $0.14885 → 吃 6.7% 利润
```

**规则**：`trail_width > stop_pct` 时 trail 宽度大于止损 → 永远吃不到利润。trail_width 应 ≤ stop_pct×0.5。

### long 侧 reversal stage 分类器的 book 时机陷阱

`_stage()` long deep_reversal（L541）原条件 `directional_book > 0.02 and pos_1h < 0.25` 在深跌币上永不过：跌 28% 的币 book 自然极卖空（db=-0.35~-0.45），且在 1h candle 内反弹后 pos_1h 可达 0.8+。宽限至 `db > -0.35 and pos_1h < 0.80` 后产生 400+ deep_reversal long 候选，avg_score=90。

pullback_reversal long 同步放宽：`db > 0.01 → -0.25, pos_1h < 0.40 → 0.80, bias > -0.12 → -0.20`。

**完整修复后的 long reversal stage 条件（2026-05-18）：**
```python
# deep_reversal long
side == "long" and signed_change < -5.0 and pos_1h < 0.80 and directional_book > -0.35 and bias > -0.25

# pullback_reversal long
side == "long" and signed_change < -2.0 and pos_1h < 0.80 and directional_book > -0.25 and bias > -0.20
```

### breakout 的位置感知保护（2026-05-18 新增）

原 breakout 只看 momentum 和 book，不管币在 4h 范围中的位置。连续多天阴跌的币仍被追空，用户批评"性价比不高，随时可能反弹"。

```python
# 旧：无条件返回 breakout
if signed_change > 3.0 and directional_book > 0.10 and spread_bps <= 15 and bias > 0.10:
    return "breakout"

# 新：4h 位置保护
if signed_change > 3.0 and directional_book > 0.10 and spread_bps <= 15 and bias > 0.10:
    if side == "short" and pos_4h < 0.30:
        pass  # 地板附近 → 出清行情，不追
    elif side == "long" and pos_4h > 0.70:
        pass  # 天花板附近 → 力竭，不追
    else:
        return "breakout"
```

**阈值选择理由：** pos_4h<0.30 即 4h 范围下 30% 分位。DOODUSDT(0.25) 被拦，1000000BOBUSDT(0.33) 放行。如需更严格提到 0.40。

**诊断信号：** 当用户说"为什么追空"时，查 SIGNAL 的 `pos_4h`。若 pos_4h < 0.40 说明入场点在底部区域。参考。

**双向检查清单**：
- short reversal：`directional_book > 0.02`（book 已翻空）→ 合理，冲顶后 book 自然翻空
- long reversal：`directional_book > 0.02`（book 已翻多）→ **不合理**，深跌后 book 极卖空，应 `db > -0.35`

诊断：当 long 侧 100% `neutral_probe` 时查 `pos_1h` 和 `directional_book` 的实际值分布。

## 参考

- 策略编写流程：[references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)
- Agent API：[references/agent-api.zh-CN.md](references/agent-api.zh-CN.md)
- 安全边界：[references/safety-boundaries.zh-CN.md](references/safety-boundaries.zh-CN.md)
- StrategySignalEvent 与 trade_params：[references/trade-plan-signal-parameter-design.md](references/trade-plan-signal-parameter-design.md)
- **Go 后端校验模式**：[references/go-backend-validation-patterns.md](references/go-backend-validation-patterns.md) — 排查信号 reject 时直接读 Go 源码而不是从 DB 反推
- **intent 格式**：[references/intent-format.md](references/intent-format.md) — `_intent_for_owned_position` 返回值不被后端识别
- 模板：[templates/dynamic_strategy.py](templates/dynamic_strategy.py)
