# Position Management Dead Code: Call-Chain Analysis

## The Trap

Post-entry position management (`_maybe_close_position`, `_maybe_rotate_position`) only fires when `build_signals_from_context()` is called for a symbol+side pair. If `discover()` blocks the candidate, **the evaluation path is never entered** and all exit logic becomes dead code.

## How It Happens

```
discover()
  → _candidate_already_held(symbol, side) returns True
  → candidate skipped, NO candidate emitted
  → context delta for this symbol never triggers build_signals_from_context()
  → _maybe_close_position() NEVER called
  → time stop (60min), opposite-side exit, stop-loss check → ALL DEAD CODE
```

## Call Chain: The Only Paths to Position Management

In the current `build_signals_from_context()` flow:

```
Line 232:  candidate exists?  (from discover())
Line 244:  price <= 0?        → _maybe_close_position()
Line 250:  trade_gate failed? → _maybe_close_position()
Line 253:  trade_gate passed? → _maybe_rotate_position()
```

**Key insight**: `_maybe_close_position()` is called at TWO points:
- Line 244: only when `price <= 0` (corner case, almost never)
- Line 250: only when a candidate arrives AND fails trade_gate

If `discover()` blocks the candidate (via `_candidate_already_held()`, hard cooldown, or filtering), **neither path is reachable** — even though `_maybe_close_position()` contains working time-stop logic at lines 708-715.

## Diagnosis: How to Detect This

```sql
-- Check if position management is producing CLOSE decisions
SELECT decision, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND decision IN ('CLOSE', 'SIGNAL', 'NO_TRADE')
  AND created_at > now() - interval '24 hours'
GROUP BY decision
ORDER BY cnt DESC;
```

If `CLOSE` count is 0 but the strategy has open positions, position management is dead code.

```sql
-- Check held positions with no exit activity
SELECT symbol, position_side, quantity, avg_entry_price, notional, updated_at
FROM execution_position_basis
WHERE venue = 'live'
ORDER BY updated_at ASC
LIMIT 10;
```

Cross-reference with decision_logs — if a position has been held >60min and no CLOSE exists for it, time-stop is dead.

## Root Cause

`_candidate_already_held()` was added to prevent redundant candidate emission for held symbols (discover has no position access natively — it caches positions from context). The side effect: **breaking the only call path to position exit logic**.

## Fix Approaches

### Approach A: Independent position monitor (Recommended)
Create a separate call path for position management that doesn't depend on candidates:

```python
# Called from runtime ticker or periodic context
def _monitor_positions(self, context: dict) -> list[dict]:
    """Independent position monitor — fires regardless of candidates."""
    positions = self._fetch_strategy_positions(context)
    closes = []
    for pos in positions:
        # Evaluate time stop, drawdown, opposite-side signals
        close_signal = self._evaluate_exit_conditions(context, pos)
        if close_signal:
            closes.append(close_signal)
    return closes
```

This needs a runtime hook — either a timed ticker or a periodic context update that doesn't require a candidate.

### Approach B: Re-evaluate held positions in discover()
Let `discover()` iterate over held positions and emit "re-evaluation" candidates that the context evaluation can process:

```python
def discover(self, universe: dict) -> list[dict]:
    ...
    # Emit re-evaluation candidates for held positions
    for sym, side in self._held_symbols():
        if self._needs_re_evaluation(sym, side):
            candidates.append(self._build_reeval_candidate(sym, side))
    ...
```

### Approach C: Trigger evaluation from `_add_check()` 
If `_add_check()` fires independently of discover(), use it as the evaluation trigger. Currently `_add_check()` also depends on candidates.

## Verification After Fix

After implementing any fix:

```sql
SELECT decision, reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND created_at > now() - interval '15 minutes'
  AND decision = 'CLOSE'
GROUP BY decision, reason
ORDER BY cnt DESC;
```

Expected: `CLOSE` entries with reasons like `time_stop_XXmin_no_profit`, `stop_loss_hit`, `opposite_signal_losing_long`.
