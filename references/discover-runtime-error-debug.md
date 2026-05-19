# discover() Runtime Error Debugging

## Symptom: discover() erroring but SIGNALs still appearing

When discover() throws an exception but the DB still shows SIGNAL entries, the root cause is **candidate TTL + rate limit interplay**:

1. discover() errors out → caught by `handle_universe()` try/except → `continue` (pool unchanged)
2. Rate limit (`_last_discover_at` + 3s gate) returns cached `_last_discover_result` on next call
3. Old candidates survive until their TTL expires (120-300s per score tier)
4. `build_signals_from_context()` keeps processing surviving candidates → SIGNALs still appear
5. Once all old candidates expire → candidate pool empties → signal production stops

**Diagnosis**: Query `strategy_decision_logs` — if SIGNALs are from candidates >2min old (check `created_at`), discover() has been failing silently.

## Case: `name 'time' is not defined`

### Symptoms
```json
{"error": "name 'time' is not defined", "phase": "discover", "strategy_id": "workflow_distilled_funnel"}
```
- Fills stdout log at ~every 3 seconds (one per failed discover() cycle)
- Cannot reproduce in isolated Python test with same strategy file
- Stderr may show `SlowConsumer` warnings simultaneously

### Root Cause
The strategy file imports `import time as _time`, which makes bare `time` unavailable in the strategy module's namespace. Any reference to `time.time()` (without underscore) causes `NameError`.

It can be triggered by:
- A `time.xxx` call in any function called by `discover()` — check: `_symbol_allowed`, `_btc_change`, `_btc_regime_penalty`, `_universe_setups`, `_candidate_already_held`, `_universe_score_components`, `_balanced_select`, `_discover_limit`, `_fetch_cached_positions_for_monitor` (and transitively `_seed_entry_times`, `_seed_one`)
- A dynamic reference in `strategy_sdk.candidate()` or `strategy_sdk.number()` — unlikely since SDK has its own `import time`
- A stale `.pyc` bytecode or partial module reload — clear `__pycache__` everywhere

### Debug Flow
1. **Verify file hash**: `shasum -a 256 <file>` vs manifest hash — confirm production runs the code you think
2. **AST search**: `grep -n '\btime\b' <file>` — find all bare `time` references excluding `_time.` and comments
3. **Reproduce in release dir**: load strategy via `load_enabled_strategies()` (same PYTHONPATH as runtime) and call discover() with mock universe
4. **Check runtime log before first error**: what was the last successful output? (signals published? candidates snapshotted?)
5. **Check process start time vs file modification time**: process may have started before file was deployed → stale version
- **If unreproducible in isolation**: suspect NATS event-loop-specific interaction, multi-threaded module loading, or data-dependent path (universe symbols with None/null values, extreme data ranges)
7. **Check if the error resolved itself on process restart**: the new process may clear the log (log file truncated to 0 bytes) and run cleanly with the same code. This is definitive evidence the error was a transient runtime issue, not a code bug.
8. **SIGNAL staleness check**: Query `strategy_decision_logs` — if SIGNALs are from old candidates (check `created_at` vs current time), discover() has been failing. Run:
   ```sql
   SELECT created_at, reason FROM strategy_decision_logs
   WHERE strategy_id='workflow_distilled_funnel'
     AND decision='SIGNAL'
   ORDER BY created_at DESC LIMIT 3;
   ```
9. **Code comparison — detect undepoyed changes**:
   ```bash
   diff $ZUOGE_CRYPTO_PROJECT_ROOT/strategy/strategies/candidates/<file>.py \
        /opt/homebrew/var/crypto-trader/strategies/enabled/<file>.py
   ```
   If different, user modified project source without deploying to production.

### Fix
Search the file for any `time.` without leading `_`. Common locations:
- `_is_asian_session()` — uses `_time.time()` correctly in current version
- `_seed_one()` — uses local `import datetime`, no `time`
- `_btc_change()` — no time reference

If no direct reference found and the error is transient (resolves on restart):

1. **Prevent deployment-race**: Always copy the strategy file BEFORE killing the old process. Or:
   ```bash
   cp <src> <dst>          # write to enabled dir
   shasum -a 256 <dst>      # verify integrity
   # THEN:
   pkill -9 -f realtime_main   # restart after file is verified
   ```
2. **If the error persists across restarts despite clean code**: add a defensive wrapper inside discover() that catches and prints full traceback:
   ```python
   import traceback
   try:
       # ...existing discover() code...
   except Exception:
       traceback.print_exc()
       raise
   ```

### Real-world case (2026-05-19)
- PID 899 ran workflow_distilled_funnel_0_1_0.py (SHA256: 0077a2f...) deployed at 13:04
- Process started at 13:03:59 (~1s before file deployed)
- Stderr showed 30,000+ name 'time' is not defined errors
- Same file on new process (PID 9843, started at 13:24) had ZERO errors
- File was identical (hash match), environment was identical
- Conclusion: transient race condition — file was being written while module was first loaded
- Process restart was the fix (launchd KeepAlive=true auto-restarted)

## Pitfall: `_portfolio_concentration_gate` same-side direction check

When refactoring the portfolio gate, be careful not to accidentally remove the same-side direction limit.

```python
# WRONG — bypasses direction limit for adds (真实 bug):
if not has_position and side_count >= MAX_DIRECTIONAL_POSITIONS:
    return False, f"too_many_{state['side']}_positions"

# RIGHT — check both new and add positions:
if not has_position and side_count >= MAX_DIRECTIONAL_POSITIONS:
    return False, f"too_many_{state['side']}_positions"
if has_position and same_side and side_count >= MAX_DIRECTIONAL_POSITIONS:
    return False, f"too_many_{state['side']}_positions_via_add"
```

The old code used `if not opposite_side and (not has_position or same_side) and side_count >= ...` — this correctly covered both new positions and adds. Simplifying to `if not has_position` drops the add case.

## SDK Detail: `strategy_sdk.candidate()` clamps score to 100

`strategy_sdk.candidate(score=...)` internally clamps score via `fmt(clamp(score, 0.0, 100.0))`. This means:

- The `score` field in the candidate dict is **[0.0, 100.0]** even if the strategy passes a higher value
- The strategy recomputes the final score in `build_signals_from_context()` from multiple components (`candidate_score * 0.30 + setup_score * 0.25 + ... + stage_bonus - penalties`), so the clamp only affects 30% of the final score contribution
- Final score can reach up to 120 (the hard cap in `strategy_sdk.clamp(score, 0.0, 120.0)`)
- **Confidence** matches the 120 ceiling: `0.50 + score / 220.0, clamped to 0.98`
