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
11. 全部通过后 submit-review，停止等待人工审批。

策略不得自行推导 UI 结论；no-trade、degraded、rejected 原因必须来自 runtime decision logs 或后端报告。
