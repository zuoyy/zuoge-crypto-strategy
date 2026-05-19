# Reversal Stage: Book Timing Entry Condition

## The Bug

Short reversal stages (`deep_reversal`, `pullback_reversal`) required `directional_book > 0.02` — sellers already dominant. This means waiting for the book to flip before entering, which defeats the purpose of a reversal.

**Symptom**: 100% of short signals land in `neutral_probe` because no reversal stage condition is ever met when the market is pumping.

**Root cause**: `directional_book` for short = `book_imbalance × (-1)`. A pumped coin has buyers dominant (`book_imbalance > 0`), so `directional_book` is negative. The old code required positive `directional_book` (sellers dominant), which only happens *after* the reversal has already started.

## The Fix

| Stage | Old | New | Semantic |
|-------|-----|-----|----------|
| deep_reversal short | `directional_book > 0.02` | `directional_book < -0.02` | Buyers still dominate → catch the top |
| pullback_reversal short | `directional_book > 0.01` | `directional_book < -0.01` | Buyers still dominate → enter before flip |

## Why This Is Correct

A reversal entry means:
- **Long reversal**: coin crashed, you buy while sellers are still active but exhausted (`directional_book > 0.02` = buyers starting to step in) — this was already correct on the long side.
- **Short reversal**: coin pumped, you short while buyers are still chasing (`directional_book < -0.02` = buyers still dominate for short direction).

## Discrimination from Trend Continuation

After the fix, `deep_reversal` short and `trend_continuation` short can both have buyers-dominant books. The distinction is in `trend_4h` + `signed_change`:

| Stage | signed_change | trend_4h |
|-------|---------------|----------|
| deep_reversal short | `< -5.0` (pumped 5%+) | Any (typically bullish from pump) |
| pullback_reversal short | `< -2.0` (pumped 2%+) | Any |
| trend_continuation short | `-2.0 to 3.0` | `< -0.3` (downtrend confirmed) |

They don't overlap because a pumped coin (2%+) cannot satisfy `trend_4h < -0.3` (which requires a confirmed downtrend).

## Diagnostic SQL

When all short signals are `neutral_probe`:

```sql
SELECT evidence_json->>'stage' as stage, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND side = 'short'
  AND created_at > now() - interval '10 minutes'
GROUP BY stage;

SELECT reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND side = 'short'
  AND created_at > now() - interval '10 minutes'
  AND evidence_json->>'stage' IS NULL
GROUP BY reason
ORDER BY cnt DESC;
```

If 100% neutral_probe → check `_stage()` book conditions for the short reversal branches. The directional_book sign is the most likely culprit.

## Pitfalls

- **Don't over-correct**: `breakout` long still needs `directional_book > 0.10` (momentum confirmation). Only reversal stages need the "early entry" book condition.
- **Bias is not the bottleneck**: For pumped coins, `bias ≈ -0.97` which easily passes `bias < 0.15`. Don't waste time checking bias when the book condition is the real gate.
- **All-short degeneracy**: When all signals are short AND all are neutral_probe, the book timing is always the root cause. Fix before touching anything else.

## Long Side Mirror Problem (2026-05-18)

The same book-timing bug existed on the **long side** but in the opposite direction:

**Bug**: long deep_reversal required `directional_book > 0.02` — buyers already dominant. After a 5%+ crash, the book is naturally ask-heavy (sellers panicking). Waiting for buyers to step in means entering too late.

**Diagnostic fingerprint**:
```
Signed_change = -28% (deep crash), but stage = neutral_probe
Reject: "neutral_probe_too_weak" with avg_score ~65
flow_score: 15-20 (punished by momentum formula)
```

**Actual long candidate values** (HANAUSDT, signed_change=-28):
```
position_1h = 0.54  ← coin dropped 28% then bounced within candle
directional_book = -0.35  ← sellers still dominate (normal after crash)
```

The old condition `pos_1h < 0.25` required near-candle-bottom, which never fires for a crashed-and-bounced coin. The old condition `directional_book > 0.02` required buyers already in control — impossible after a fresh crash dump while sellers are still active.

**Fix (deployed 2026-05-18)**:

| Condition | Old | New | Rationale |
|-----------|-----|-----|-----------|
| deep_reversal long: pos_1h | < 0.25 | < 0.80 | Allow recovered portion of crash candle |
| deep_reversal long: directional_book | > 0.02 | > -0.35 | Allow moderate seller dominance (early entry) |
| deep_reversal long: bias | > -0.15 | > -0.25 | Slightly wider structure tolerance |
| pullback_reversal long: pos_1h | < 0.40 | < 0.80 | Same reasoning |
| pullback_reversal long: directional_book | > 0.01 | > -0.25 | Same reasoning |
| pullback_reversal long: bias | > -0.12 | > -0.20 | Same reasoning |

**Key insight**: After a 28% crash, directional_book of -0.35 to -0.45 is NORMAL. The book will not flip until the crash has fully exhausted and buyers return. By then, the reversal opportunity is half over. **Use `signed_change < -5.0` as the primary filter**, not the book condition.
