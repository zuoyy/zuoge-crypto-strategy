# intent 格式不被后端识别

## 症状

- SIGNAL 正常产生，但 signals 表 `status=rejected`
- `strategy_signal_rejects` 显示 `reason_code='invalid_intent'`，`reason='intent is not supported'`
- 信号 payload 中 `intent` 的值如下：

| 版本 | intent 值 | 结果 |
|------|-----------|------|
| 旧版 `_intent_for_owned_position` | `open_new_position` / `add_position` / `reverse_position` | ❌ `invalid_intent` |
| 当前策略推荐 | `open_long` / `open_short` / `close_long` / `close_short` / `reverse_long` / `reverse_short` | ✅ Go ingress 会做 `strings.ToUpper()` 后映射 |
| Go 合约常量 | `OPEN_LONG` / `OPEN_SHORT` / `CLOSE_LONG` / `CLOSE_SHORT` / `REVERSE_LONG` / `REVERSE_SHORT` | ✅ 后端也兼容，但 Python 策略侧统一用小写，避免历史 payload 被错误丢弃 |

## 注意

当前 Python `strategy_sdk.intent_for_side()`、`close_intent_for_side()` 以及 `workflow_distilled_funnel._intent_for_owned_position()` 返回小写 intent。不要再生成 `open_new_position`、`add_position`、`reverse_position` 这类旧格式。

Go 侧 `mapIntent()` 会把 intent `strings.ToUpper()` 后匹配合约常量，所以小写和大写都能识别；策略侧为保持 payload 一致，统一使用小写。

排查时，对比：
```sql
-- 查看 signals 表中是否有 intent 字段
SELECT payload_json::jsonb ? 'intent' as has_intent, COUNT(*)
FROM signals GROUP BY 1;

-- 查看 reject 记录中的 intent 值
SELECT rejected_at, reason_code, reason,
       payload_json->>'intent' as intent
FROM strategy_signal_rejects
WHERE reason_code = 'invalid_intent'
ORDER BY rejected_at DESC LIMIT 5;
```
