# close_ratio 尾盘残留：阶梯止盈最后一档的哨兵值约定

## 现象

两档阶梯止盈都用 `close_ratio: "0.5"`，TP1 触发后 TP2 只平了剩余仓位的 50%，导致 ~25% 仓位残留未清。

用户反馈：*"阶梯止盈的数量，如果是最后一次要全部平仓，不要用计算的数据"*

## 根因：sync 路径的 position.Quantity 漂移

### 初始下单路径（正确）

`initialProtectiveOrderRefs` 用 `fill.Quantity`（**入场时的原始仓位数量**）计算：

```go
// protective_orders.go:40-42
quantity := decimal.Zero
if fill.Quantity.IsPositive() && target.CloseRatio.LessThan(decimal.NewFromInt(1)) {
    quantity = fill.Quantity.Mul(target.CloseRatio)  // 0.5 * 1.0 = 0.5 ✓
}
```

两档 `0.5`：TP1 订单量=50% 原始仓位，TP2 订单量=50% 原始仓位。初始下单时正确。

### sync 重算路径（有 bug）

TP1 触发部分平仓后，reconciler 调用 `syncExchangeProtectiveOrders`，用 **当前交易所持仓数量** 重新生成 desired orders：

```go
// protective_orders.go:77-84
desired := s.initialProtectiveOrderRefs(proposal, Fill{
    ...
    Quantity: position.Quantity,  // ← 当前剩余仓位（例如只剩 0.5）
    ...
}, nextRuntime)
```

此时 `position.Quantity = 0.5`（TP1 已平 50%），TP2 重算：
```
quantity = 0.5 * 0.5 = 0.25  // 只平剩余仓位的 50%，留下另外 50%（=原始 25%）
```

`protectiveOrdersEquivalent` 发现 Quantities 不匹配（旧的 0.5 vs 新的 0.25），取消旧 TP2 订单，重挂 0.25 量的单。尾盘 25% 残留。

## 修复：最后一档 `close_ratio: "1.0"`

### Python SDK 修改

```python
# strategy_sdk.py basic_trade_params() — 原代码
"take_profit": {"mode": "ladder", "targets": [
    {"price": fmt(tp1), "close_ratio": "0.5"},
    {"price": fmt(tp2), "close_ratio": "0.5"}   # ❌ 尾盘残留
]}

# 修改后
"take_profit": {"mode": "ladder", "targets": [
    {"price": fmt(tp1), "close_ratio": "0.5"},
    {"price": fmt(tp2), "close_ratio": "1.0"}   # ✅ 哨兵值：全平剩余
]}
```

### Go 校验修改

原校验拒绝 `sum(close_ratio) > 1.0000001`（0.5+1.0=1.5 被拒）。修改后：

```go
// service.go validateTradeParams()
lastIdx := len(takeProfit.Targets) - 1
for idx, target := range takeProfit.Targets {
    ...
    // 最后一档 close_ratio=1.0 是"全平剩余"哨兵，不计入 sum check
    if idx == lastIdx && target.CloseRatio.Equal(decimal.NewFromInt(1)) {
        continue
    }
    totalCloseRatio = totalCloseRatio.Add(target.CloseRatio)
}
```

## 执行端 `close_ratio=1.0` 的处理

执行端已有完整支持：

```go
// protective_orders.go:39-42
if fill.Quantity.IsPositive() && target.CloseRatio.LessThan(decimal.NewFromInt(1)) {
    quantity = fill.Quantity.Mul(target.CloseRatio)
}
// close_ratio=1.0 时不进这个分支，quantity 保持为 0
```

```go
// protective_orders.go:254-261 (buildProtectiveOrderPlacement)
if ref.Quantity.IsPositive() {
    req.Quantity = ref.Quantity
    ...
} else {
    req.ClosePosition = true  // ← Binance 原生"全平当前持仓"
}
```

`ClosePosition=true` 告诉 Binance 平掉该 symbol/side 的全部当前持仓，不管剩余多少。

## 合约约定总结

| `close_ratio` | 语义 | 执行路径 |
|---|---|---|
| `0 < x < 1` | 平 `fill.Quantity * x` | `req.Quantity = computed` |
| `1.0`（哨兵） | 全平当前剩余持仓 | `req.ClosePosition = true` |

规则：
- **只有最后一档**使用 `1.0`
- 非最后一档 `close_ratio` 仍受 sum ≤ 1 约束
- SDK `basic_trade_params()` 已默认处理
