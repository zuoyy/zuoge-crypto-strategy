---
name: zuoge-crypto-strategy
description: "用于编写、校验、回测并自动投递实时策略候选到生产收件箱；生产审批和启用由人工完成。"
---

# zuoge-crypto-strategy

本技能面向渐进式漏斗实时策略架构。策略分两阶段：

- `discover(universe)` 消费 `strategy.universe.delta` 的全市场轻量数据，只输出 side-aware candidates。
- `build_signals_from_context(context)` 消费已预热的 `strategy.context.delta.{symbol}`，只在完整行情依赖满足后输出标准 `StrategySignalEvent`。

候选池由系统托管但按策略隔离，候选唯一键为 `(strategy_id, symbol, side)`。同一 symbol 可以同时被不同策略或不同方向选中；完整行情订阅由系统按 symbol/dependency 合并，方向冲突交给 risk/execution。

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

## 策略胜率优化方法论

⚠️ **先修评分公式，再调 gate 阈值。** 不要只调 `neutral_probe` score floor 或 `directional_book` gate——先检查评分公式本身是否奖励了错误行为。

常见结构性陷阱：
- **`flow_score` 奖励追涨**：`signed_change * N` 线性系数让拉得最凶的币得最高分。应改为奖金衰减（≤5% 线性，5-15% 衰减到 0，≥15% 不奖）。参见 [references/change-bonus-pattern.md](references/change-bonus-pattern.md)。
- **`_stage()` 方向错误——动量追涨而非反转抄底**：所有阶段都要求 `signed_change > 0`（币已按信号方向移动），本质是追涨杀跌。专业做法是反转优先：币大跌→抄底做多，币大涨→摸顶做空。详见 [references/reversal-first-stage-architecture.md](references/reversal-first-stage-architecture.md)。
- **`candidate_score`/`move_score` 奖励大波动**：`log1p(abs(change))` 不区分方向，涨 20% 和跌 20% 得同分。
- **缺少超买/超卖过滤**：`position_in_range` 来自 1h/4h kline，>0.88 不做多，<0.12 不做空。
- **缺少大趋势确认**：做多要求 4h `trend_return_pct > -1.5%`，做空要求 `< 1.5%`。
- **缺少市场 regime**：BTC 跌 >1.5% 时不做多 alts；BTC 涨 >1.5% 时不追空。

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

**源头收紧 > gate 加码**：discover() 多放一个弱 candidate，context delta 每秒触发多次评估链。优先从源头砍弱 candidate（score floor、limit、动态 TTL），减少 context 评估总量。详见 [references/efficiency-funnel-source-quality.md](references/efficiency-funnel-source-quality.md)。

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

> **🔴 硬规则 1：所有策略修改必须走此工作流。** 不要跳过步骤直接修改生产 enabled 文件。若已修改的文件已是生产策略文件，也必须回填到候选目录、补走 check/test/backtest。
> 
> **🔴 硬规则 2：永远只编辑项目源码根目录下的文件。** 源码在 `$ZUOGE_CRYPTO_PROJECT_ROOT/strategy/strategies/candidates/`。`/opt/homebrew/var/crypto-trader/` 下的同名文件是生产部署镜像——管线下游产物，不手动编辑。
>
> **🔴 硬规则 3：生产 DB 值是权威信源。** 不要用代码里的 fallback 默认值（如 `, 40`）去估算后端配置。后端参数（`max_order_notional_pct`、`max_positions` 等）存在 `strategy_risk_allocations` 表，修改前先 `SELECT`。

在项目根目录内工作：

1. 需要运行态能力目录时，`crypto-skill capabilities show --format json`。
2. 按需读取 [references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)。
3. `crypto-skill research create --focus "<研究目标>"`
4. 基于 capabilities 和 SDK 编写候选策略到 `strategy/strategies/candidates/`。
5. `crypto-skill candidate new --strategy <id> --version <x.y.z> --file <candidate.py> --research <research_id>`
6. `crypto-skill candidate check --candidate <candidate_id>`
7. `crypto-skill candidate test --candidate <candidate_id>`
8. `crypto-skill candidate backtest --candidate <candidate_id>`
9. `crypto-skill candidate report --candidate <candidate_id>`
10. 只有 check/test/backtest 都通过时，`crypto-skill candidate publish --candidate <candidate_id>`，把候选包投递到生产 Strategy Center 收件箱，然后停止等待人工审批。

`candidate new` 只用于首次把候选策略文件登记到策略中心并取得 `candidate_id`。如果用户要求检查、测试或回测一个已经登记过的策略，先用 `crypto-skill candidate list` 或 `crypto-skill candidate show --candidate <candidate_id>` 找到已有 `candidate_id`，然后直接运行 `check`、`test`、`backtest`、`report`；不要为同一个 `strategy_id` 和 `version` 重复执行 `candidate new`。

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
# 1. 修改策略文件
vim /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py

# 2. ⚠️ 必须更新 manifest hash ——漏掉这步→策略静默不加载
HASH=$(shasum -a 256 /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py | awk '{print $1}')
python3 -c "
import json
for p in [
    '/opt/homebrew/var/crypto-trader/releases/$(readlink /opt/homebrew/var/crypto-trader/current | xargs basename)/strategy/strategies/enabled/workflow_distilled_funnel_0_1_0.manifest.json',
    '/opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.manifest.json',
]:
    with open(p) as f: m = json.load(f)
    m['code_hash'] = '$HASH'
    m['code_sha256'] = '$HASH'
    with open(p, 'w') as f: json.dump(m, f, indent=2)
"

# 3. 同步到 release 目录的 enabled 子目录（真实加载路径）
RELEASE=$(readlink /opt/homebrew/var/crypto-trader/current | xargs basename)
mkdir -p /opt/homebrew/var/crypto-trader/releases/$RELEASE/strategy/strategies/enabled
cp /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py /opt/homebrew/var/crypto-trader/releases/$RELEASE/strategy/strategies/enabled/
cp /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.manifest.json /opt/homebrew/var/crypto-trader/releases/$RELEASE/strategy/strategies/enabled/

# 4. 清 pycache + 重启
find /opt/homebrew/var/crypto-trader -name '__pycache__' -exec rm -rf {} + 2>/dev/null
pkill -9 -f realtime_main   # KeepAlive=true → 自动重启

# 5. ⚠️ 验证策略加载成功
cd /opt/homebrew/var/crypto-trader/releases/$RELEASE && python3 -c "
import sys; sys.path.insert(0,'strategy')
from runtime.strategy_manager import load_enabled_strategies
h,e = load_enabled_strategies()
print(f'Handles: {len(h)}, Errors: {len(e)}')
for x in e: print(f'  ✗ {x[\"error\"][:120]}')
for x in h: print(f'  ✓ {x.strategy_id}')
"
# 期望输出：Handles: 1, Errors: 0  ✓ workflow_distilled_funnel
```

### 为什么有两个路径都要更新

`strategy_manager.py` 的 `enabled_dir()` 解析路径：
```python
Path(__file__).resolve().parents[1] / "strategies" / "enabled"
```
`__file__` 是 `strategy_manager.py` 的实际路径，Python 导入时已解析 symlink → 指向 release 目录内的 `strategy/strategies/enabled/`。所以**真实加载路径是 release 目录内的 enabled**，不是顶层的 `/opt/homebrew/var/crypto-trader/strategies/enabled/`。但两边 manifest 都要更新以保持一致。

### ⚠️ 常见陷阱：新 release 部署后策略消失

当 `deploy-current` 或系统自动创建新 release 目录时（如 `20260515-224627` → `20260516-000638`），新 release 的 `strategy/strategies/enabled/` 是空的。策略文件必须重新同步到新 release 目录：

```bash
RELEASE=$(readlink /opt/homebrew/var/crypto-trader/current | xargs basename)
DEST="/opt/homebrew/var/crypto-trader/releases/$RELEASE/strategy/strategies/enabled"
mkdir -p "$DEST"
cp /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.py "$DEST/"
cp /opt/homebrew/var/crypto-trader/strategies/enabled/workflow_distilled_funnel_0_1_0.manifest.json "$DEST/"
```

**症状：** `Handles: 0, Errors: 0`（无报错但无策略加载）→ enabled 目录为空。

### 如何快速诊断"策略未加载"

```bash
cd /opt/homebrew/var/crypto-trader/current && python3 -c "
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

**sudo 不可用时的后备方案**：`deploy-current` 需要 sudo 操作 launchd。若 sudo 不可用，可直接复制修改的文件到当前 release 目录，然后 kill 进程让 launchd 自动重启：

```bash
cp strategy/runtime/strategy_sdk.py /opt/homebrew/var/crypto-trader/current/strategy/runtime/
kill $(pgrep -f realtime_main | head -1)   # KeepAlive=true → 自动重启
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
- `min_reward_risk`: 盈亏比不达标

### 6. 排查致命错误模式

**account_risk_budget_missing**: overlay 失败 → 全部 NO_TRADE
**SlowConsumer**: `*` 通配符订阅 → 事件循环饥饿。核对订阅方式应为 per-candidate 动态订阅
**signals 全 expired 但有成交**: 查 fills + position_plan_runtimes
**账户满仓死锁**: total_exposure>100%, remaining_budget=0
**候选池始终为空但无 exception 日志**:
  1. 先验证策略是否加载：`cd <release_dir> && python3 -c "from runtime.strategy_manager import load_enabled_strategies; h,e=load_enabled_strategies(); print(len(h),len(e))"`
  2. 若 `Handles: 0, Errors: 1 → code hash mismatch` → manifest 的 `code_hash` 字段未更新，参照「生产直接修改策略文件的完整步骤」
  3. 若 Handles=1 仍无 candidate → 查 discover() 过滤逻辑（见下方 discovery 死寂排查）

详见 [references/production-db-quick-diagnosis.md](references/production-db-quick-diagnosis.md)。

### 运行时健康检查

当用户问"策略是否正常"时，按 [references/production-runtime-diagnosis.md](references/production-runtime-diagnosis.md) 的 6 层检查清单执行：进程层→策略加载层→决策产出层→NATS 连通层→账户层→故障模式速查。核心查询：

- `strategy_decision_logs` 聚合：`SELECT reason, COUNT(*) ... GROUP BY reason` 直接揭示为什么没信号
- macOS `sample <PID>` 探查无日志进程的调用栈
- `ps eww <PID>` / `lsof -p <PID>` 确认进程环境和工作目录
- ⚠️ `subsz` 可能不显示 `nats-py` 订阅 → **decision_logs 才是进程活着的唯一可靠证据**

### 杠杆与下单金额

- **杠杆**：`pick_leverage()` 动态计算，从 `risk_limits.min/max_leverage` 读范围，按阶段/分数/波动率调参。保守阶段（neutral_probe 等）→ 固定 `min_leverage`。详见 [references/leverage-dynamic-calculation.md](references/leverage-dynamic-calculation.md)。
- **动态下单金额**：`desired_notional = min(risk/stop, equity × max_order_pct)`，其中 `max_order_pct = risk_limits.max_order_notional_pct / 100`。后端修改后策略自动跟随，无需改代码。⚠️ 代码中的 `, 40` 只是 fallback 默认值，实际后端值必须从 `strategy_risk_allocations` 表查询——不要假设。详见 [references/max-order-notional-dynamic.md](references/max-order-notional-dynamic.md)。
- **下单金额完整计算链**：`risk_pct = clamp(2.0 + (score-55)×0.06, 2.0, 4.0)`（分数动态浮动）→ `target_risk_amount = equity × risk_pct/100` → `desired_notional = target_risk / stop_pct` → 多重封顶（`effective_order_cap`、`remaining_symbol_cap`、`remaining_total_cap`、`leverage_notional_cap` 取 min）→ `quantity = max_notional / price`。改 `risk_pct` 直接影响仓位，改止损宽度反向影响仓位——止损放宽后必须同步提 `risk_pct` 否则仓位同比例缩小。
- **加仓**：专业金字塔加仓 — 7 层 gate（浮盈≥1.5%、趋势续、book 撑、回调入场、阶段过滤、敞口检查、亏损保护）+ 4 级冷却分层（90/180/240/120min），budget联动 `max_add_count`（1/(1+n)递减），参数从 backend 动态读取不写死。详见 [references/position-management-add-gate.md](references/position-management-add-gate.md)。预算联动细节见 [references/dynamic-add-budget-linkage.md](references/dynamic-add-budget-linkage.md)。
- **加仓评估冷却**：加仓被拒后对同一 `symbol:side` 设 300s 冷却——冷却期内跳过全部 7 层检查，直接返回 `add_in_cooldown`。实测减少 86% 无效评估。详见 [references/add-check-cooldown.md](references/add-check-cooldown.md)。
- **效率漏斗**：discover() 源头 candidate 质量直接决定 context 评估量（50万+/h decision logs）。收紧源头（score floor↑、candidate limit↓、TTL动态化、方向预筛选）比加 gate 更有效。详见 [references/efficiency-funnel-source-quality.md](references/efficiency-funnel-source-quality.md)。
- **仓位轮换**：满仓时高质量新信号（score≥85）可主动止盈最弱浮盈持仓（0.5%~2.5% PnL，全大盈时取最弱），释放 slot 落袋为安。调用 `GET /api/v1/agent/positions` 获取全策略持仓做全局比较，15s TTL 缓存不 flooding。⚠️ close 信号必须手动构建（不用 signal_envelope 以免 cross-symbol price_ref 错位），且 manifest 需 `max_signals_per_candidate: 2` 防止双信号截断。排查链路见 [references/position-rotation.md](references/position-rotation.md)。
- **止损波动率**：双源波动率代理（24h change + 1h trend），替代单源 24h change。短时剧烈波动的币自动放宽止损，已冷却的币自动收紧。⚠️ **分母 850 陷阱**：生产验证分母 850 导致止损 0.7-1.0%，配合 11-23x 杠杆必被扫。校准值应为 150-200。详见 [references/stop-formula-dual-volatility.md](references/stop-formula-dual-volatility.md)。
- **阶段多样性**：加 `early_trend` 过渡阶段解决全 short 单一信号问题 + long book gate 放宽 ±0.03 中性区。详见 [references/stage-classification-diversity.md](references/stage-classification-diversity.md)。
- **阶段差异化参数**：止损、移动止盈、盈亏比按 stage 分化——reversal 给宽止损+runner trail，trend_continuation 给紧止损+标准 trail。避免一刀切导致的 reversal 被扫 / trend 跑不掉。详见 [references/stage-specific-parameters.md](references/stage-specific-parameters.md)。
- **阶段诊断**：信号阶段分布分析、死代码检查（sweep_reclaim）、BTC regime gate 影响、stage_bonus 配置。详见 [references/stage-diversity-diagnosis.md](references/stage-diversity-diagnosis.md)。
- **Gate 迭代校准**：query→fix→requery 循环，连锁反应观察，gate 放宽优先级排序。详见 [references/gate-calibration-iterative.md](references/gate-calibration-iterative.md)。

## 参考

- 详细流程：[references/authoring-workflow.zh-CN.md](references/authoring-workflow.zh-CN.md)
- 安全边界：[references/safety-boundaries.zh-CN.md](references/safety-boundaries.zh-CN.md)
- Agent API：[references/agent-api.zh-CN.md](references/agent-api.zh-CN.md)
- StrategySignalEvent 与 trade_params：[references/trade-plan-signal-parameter-design.md](references/trade-plan-signal-parameter-design.md)
- 持仓感知交易计划：[references/position-aware-trading-plan.md](references/position-aware-trading-plan.md)
- 生产 DB 快速诊断：[references/production-db-quick-diagnosis.md](references/production-db-quick-diagnosis.md)
- 策略胜率诊断与优化：[references/strategy-optimization-playbook.md](references/strategy-optimization-playbook.md)
- 胜率分析 SQL 全集：[references/win-rate-analysis-queries.md](references/win-rate-analysis-queries.md)
- **移动止盈吃不到大肉**：[references/trailing-stop-runner-problem.md](references/trailing-stop-runner-problem.md)
- **反转优先阶段分类器**：[references/reversal-first-stage-architecture.md](references/reversal-first-stage-architecture.md)
- **加仓评估冷却**：[references/add-check-cooldown.md](references/add-check-cooldown.md)
- 价格量化陷阱：[references/price-quantization-pitfalls.md](references/price-quantization-pitfalls.md)
- risk_budget sizing 瓶颈：[references/risk-budget-sizing-pitfall.md](references/risk-budget-sizing-pitfall.md)
- risk_budget 公式倒置：[references/risk-budget-formula-inversion.md](references/risk-budget-formula-inversion.md)
- close_ratio 尾盘残留：[references/close-ratio-ladder-tail.md](references/close-ratio-ladder-tail.md)
- change_bonus 衰减模式：[references/change-bonus-pattern.md](references/change-bonus-pattern.md)
- Manifest hash 陷阱：[references/manifest-hash-silent-failure.md](references/manifest-hash-silent-failure.md)
- Signal 重复推送冷却：[references/signal-dedup-cooldown.md](references/signal-dedup-cooldown.md)
- 生产运行时诊断：[references/production-runtime-diagnosis.md](references/production-runtime-diagnosis.md)
- 动态杠杆计算：[references/leverage-dynamic-calculation.md](references/leverage-dynamic-calculation.md)
- 持仓管理与加仓门禁：[references/position-management-add-gate.md](references/position-management-add-gate.md)
- 仓位轮换（落袋为安）：[references/position-rotation.md](references/position-rotation.md)
- 仓位轮换死锁诊断（分数天花板 vs 绝对阈值）：[references/position-rotation-deadlock.md](references/position-rotation-deadlock.md)
- 动态下单金额封顶：[references/max-order-notional-dynamic.md](references/max-order-notional-dynamic.md)
- 加仓预算动态联动：[references/dynamic-add-budget-linkage.md](references/dynamic-add-budget-linkage.md)
- 效率漏斗（源头质量）：[references/efficiency-funnel-source-quality.md](references/efficiency-funnel-source-quality.md)
- 止损波动率：[references/stop-formula-dual-volatility.md](references/stop-formula-dual-volatility.md)
- **止盈全平约定**：[references/close-ratio-ladder-tail.md](references/close-ratio-ladder-tail.md) — `close_ratio: "1.0"` 哨兵值触发 Binance `ClosePosition=true`。止盈梯子在 `strategy_sdk.py` `basic_trade_params()` 构建，不在策略文件。策略层配合 `allow_partial_exit: False`。
- 阶段多样性诊断与修复：[references/stage-diversity-diagnosis.md](references/stage-diversity-diagnosis.md)
- BTC regime 梯度惩罚：[references/btc-regime-graduated-penalty.md](references/btc-regime-graduated-penalty.md)
- 信号拒绝诊断：[references/signal-rejection-diagnosis.md](references/signal-rejection-diagnosis.md)
- 模板：[templates/dynamic_strategy.py](templates/dynamic_strategy.py)