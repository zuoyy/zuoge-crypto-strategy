# 策略编写流程

1. 读取 capabilities catalog。
2. 明确策略交易场景：趋势、突破、震荡均值回归、生态动量、避开拥挤资金费率等。
3. 创建 research bundle，读取 Context 样本、feature 分布、reject/no-trade/degraded 原因。
4. 只基于 catalog 中存在的字段写策略；`discover()` 使用 all-market 横截面流筛候选，不能把 all-market 数据当成执行报价。
5. 使用 `runtime.strategy_sdk`：
   - `candidate`
   - `dependency`
   - `is_warm`
   - `universe_value`
   - `is_context_ready`
   - `dependencies_fresh`
   - `feature`
   - `avg_timeframe`
   - `basic_trade_params`
   - `signal_envelope`
   - `decision_log`
6. `discover(universe)` 输出 `(strategy_id, symbol, side)` 维度的候选。
7. `build_signals_from_context(context)` 只在完整行情预热后输出信号。
8. 对每个 symbol 明确输出：
   - `SIGNAL`
   - `NO_TRADE`
   - `WAIT`
   - `WAIT_WARMUP`
   - `DEGRADED_SKIP`
   - `VALIDATION_FAILED`
9. 无机会返回 `[]`。
10. 运行 check/test/backtest/report。
11. 全部通过后 `crypto-skill candidate publish --candidate <candidate_id>`，把候选包投递到生产 Strategy Center 收件箱，然后停止等待人工审批。

`candidate new` 表示首次创建候选策略登记记录并返回 `candidate_id`。检查、测试、回测、报告都面向已有 `candidate_id` 执行；如果用户只是要求检查或回测已有策略，先查 `crypto-skill candidate list`，不要重复创建同一个 `strategy_id` + `version`。

`candidate publish` 只导入生产候选，不审批、不启用、不写 enabled。生产审批由人通过 Strategy Center 完成。

发布当前策略目录是独立操作。只有用户明确要求发布当前 `strategy/` 运行时代码时，才执行 `crypto-skill strategy deploy-current`；不要在候选编写、校验、回测或候选投递流程中顺手发布。生产 enabled 策略保存在持久目录，不随 release 切换。

策略不得自行推导 UI 结论；no-trade、degraded、rejected 原因必须来自 runtime decision logs 或后端报告。
