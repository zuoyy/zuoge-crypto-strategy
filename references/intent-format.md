# intent 格式不被后端识别

## 症状

- SIGNAL 正常产生，但 signals 表 `status=rejected`
- `strategy_signal_rejects` 显示 `reason_code='invalid_intent'`，`reason='intent is not supported'`
- 信号 payload 中 `intent` 的值如下：

| 版本 | intent 值 | 结果 |
|------|-----------|------|
| 旧版 `_intent_for_owned_position` | `open_new_position` / `add_position` / `reverse_position` | ❌ `invalid_intent` |
| 新版（用户修正） | `OPEN_LONG` / `OPEN_SHORT` / `REVERSE_LONG` / `REVERSE_SHORT` | ⚠️ 与 SDK 的 `intent_for_side()` 返回值一致 |

## 注意

SDK 的 `intent_for_side()` 和 `close_intent_for_side()` 也返回大写格式（`"OPEN_LONG"`、`"CLOSE_LONG"`）。Go 后端是否接受这些值取决于后端版本。

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
