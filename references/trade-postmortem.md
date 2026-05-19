# 持仓交易复盘（Trade Post-Mortem）

当用户问"这笔交易为什么做"、"这单为什么开仓"、"分析一下XX的持仓"时使用。

## 触发场景

- "查一下 CGPT 为什么做多"
- "这笔合约为什么开仓"
- "分析一下 XX 的持仓"
- "复盘这笔交易"

⚠️ 这属于 **策略分析** 类问题，**不走 zuoge-crypto-query**。直接使用 `zuoge-crypto-strategy` 技能。

## 工作流

### 1. 查持仓

```sql
SELECT symbol, position_side, quantity, avg_entry_price, notional, leverage, updated_at
FROM execution_position_basis
WHERE symbol = '<SYMBOL>'
ORDER BY updated_at DESC LIMIT 5;
```

### 2. 查做多/做空原因（strategy_decision_logs 找 SIGNAL）

```sql
SELECT created_at, decision, reason, side, evidence_json::text
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND symbol = '<SYMBOL>'
  AND decision = 'SIGNAL'
ORDER BY created_at DESC LIMIT 3;
```

从 `evidence_json` 提取关键字段：
- **stage**: deep_reversal / pullback / breakout / neutral_probe
- **signed_change**: 24h 涨跌幅（负数 = 跌，反转做多触发的关键）
- **directional_book**: 盘口方向（正 = 买方主导，负 = 卖方主导）
- **spread_bps**: 点差
- **trend_1h / trend_4h**: 1h / 4h 趋势
- **position_1h / position_4h**: 在 1h / 4h 范围中的位置（0-1）
- **score**: 总分
- **stop_pct**: 止损百分比
- **confidence**: 置信度
- **reward_risk**: 盈亏比

### 3. 查当前行情

```bash
# 当前价 + 24h 统计
curl -s "https://api.binance.com/api/v3/ticker/24hr?symbol=<SYMBOL>"

# 1h klines（最近 48 根看价格行动）
curl -s "https://api.binance.com/api/v3/klines?symbol=<SYMBOL>&interval=1h&limit=48"
```

### 4. 分析价格行动（Price Action）

用 kline 数据还原自开仓以来的价格路径：

| 时间 | 价格 | 变化 | 解读 |
|------|------|------|------|
| 开仓时间 | 入场价 | — | 开仓 |
| 下一小时 | 收盘价 | ↑/↓ | 立即方向 |
| ... | ... | ... | 连续方向判断 |

关键观察：
- **反弹是否成立**：入场后是否有 ≥入场价 2% 以上的反弹
- **反弹是否持续**：反弹后的下一根 kline 是否继续向上
- **是否创新低**：价格是否跌破所有近期低点

### 5. 给出专业判断

评估标准（三板斧）：

**① 止损距离**
```
距止损 = (当前价 - 止损价) / 当前价
止损价 = 入场价 × (1 - stop_pct)
```
- 接近止损（<1%）→ 准备离场
- 距止损较远（>3%）→ 还有空间

**② 反转逻辑是否成立**
- deep_reversal 要求 1h/4h 趋势翻转 + 买方出现
- 如果入场后连续 6+ 小时阴跌 → 反转逻辑瓦解
- 价格在 pos_4h < 0.3 时入场但持续走低 → 地板下面还有地下室

**③ 24h 低点支撑**
- 价格距 24h 低点 <1% → 跌破就是新低，反转逻辑完全失败
- 跌破 24h 低后 → 止损会被扫 → 建议在 24h 低点上方主动平仓

### 6. 回复格式

使用三部分结构：

1. **持仓快照**：方向/数量/入场价/杠杆/名义价值
2. **做多/做空原因**：stage + key_metrics（change/book/spread）
3. **专业判断**：基于数据和策略逻辑的 hold/close 建议

专业判断必须包含定量计算（止损距离、亏损比例、点位分析）和定性判断（反转逻辑强度、市场信号）。不要只说"看情况"。
