# close_ratio 全平：单级止盈 `1.0` 哨兵值约定

## 当前策略：单级全平（非阶梯）

`basic_trade_params()` 构建**单 rung ladder**：

```python
# strategy_sdk.py basic_trade_params()
"take_profit": {"mode": "ladder", "targets": [
    {"price": fmt(tp2), "close_ratio": "1.0"}
]}
```

`close_ratio: "1.0"` 是哨兵值，触发 Binance `ClosePosition=true` 原生全平，不计算数量。

策略层配合：`_apply_position_management` 设 `allow_partial_exit: False`。

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
