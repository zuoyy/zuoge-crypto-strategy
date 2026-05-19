# Asian Session Stage Classification Thresholds

## Problem

During Asian session (09:00-17:00 Beijing), the stage classifier produces **100% neutral_probe** for ALL candidates — zero breakout, zero deep_reversal, zero pullback_reversal.

Root cause: `_stage()` uses fixed `signed_change` thresholds (5.0% for reversal, 3.0% for breakout, 2.0% for pullback) that are calibrated for US-session volatility. Asian session volatility is typically 40-60% lower.

## Diagnostic

Check stage distribution by hour of day:

```sql
SELECT date_trunc('hour', created_at AT TIME ZONE 'Asia/Shanghai') as hour,
       side,
       COUNT(*) as total,
       COUNT(*) FILTER (WHERE evidence_json->>'stage'='neutral_probe') as neutral,
       COUNT(*) FILTER (WHERE evidence_json->>'stage'='breakout') as breakout,
       COUNT(*) FILTER (WHERE evidence_json->>'stage'='deep_reversal') as deep_reversal
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '24 hours'
GROUP BY hour, side
ORDER BY hour, side;
```

If 09:00-17:00 rows show 100% neutral_probe for all sides → hit.

## Fix

### Add session detection

```python
@staticmethod
def _is_asian_session() -> bool:
    """Asian session (09:00-17:00 Beijing) has lower volatility."""
    beijing_hour = ((_time.time() + 8 * 3600) % 86400) / 3600.0
    return 9.0 <= beijing_hour < 17.0
```

### Define relaxed thresholds

```python
ASIAN_SESSION_BREAKOUT_THRESHOLD = 1.5   # instead of 3.0
ASIAN_SESSION_REVERSAL_THRESHOLD = 2.5   # instead of 5.0
ASIAN_SESSION_PULLBACK_THRESHOLD = 1.0   # instead of 2.0
```

### Modify `_stage()` to accept `is_asian` parameter

```python
def _stage(self, side: str, signed_change: float, directional_book: float,
           funding: float, spread_bps: float, setup_score: float,
           structure: dict, is_asian: bool = False) -> str:
    if is_asian:
        rev_t = ASIAN_SESSION_REVERSAL_THRESHOLD   # 2.5%
        pb_t = ASIAN_SESSION_PULLBACK_THRESHOLD    # 1.0%
        bo_t = ASIAN_SESSION_BREAKOUT_THRESHOLD    # 1.5%
    else:
        rev_t = 5.0
        pb_t = 2.0
        bo_t = 3.0

    if side == "long" and signed_change < -rev_t and ...:
        return "deep_reversal"
    # ... rest of the stages use rev_t/pb_t/bo_t
```

### Pass `is_asian` from `_evaluate_context()`

```python
is_asian = self._is_asian_session()
stage = self._stage(side, signed_change, directional_book, funding, spread_bps, setup_score, structure, is_asian)
```

## Expected Effect

Asian session: from 0% non-neutral_probe → ~15-20% classified as reversal/breakout/pullback.

## Trade-off

Relaxed thresholds may produce more false signals during Asian hours (thin liquidity, low conviction). Mitigated by:
- The `_trade_gate()` still applies all structural checks (book, spread, volume, overbought/oversold)
- `neutral_probe` score floor of 85 still rejects weak candidates
- The Asian session candidates get the same context evaluation quality — only the stage classifier input threshold changes
