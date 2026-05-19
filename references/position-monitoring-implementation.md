# Position Monitoring: Implementation Guide

## Problem

Position management (`_maybe_close_position()`: time stop, drawdown exit, opposite-side close) was dead code. The only call path was through `build_signals_from_context()` — which requires a candidate from `discover()`. `_candidate_already_held()` blocked held symbols from producing ANY candidate, so the exit path was never entered.

## Solution Architecture

### Phase 1: Emit "position_monitor" candidates from discover()

After normal candidate generation, discover() emits one special candidate per held position:

```python
# In discover(), after normal candidate loop:
positions_for_monitor = self._fetch_cached_positions_for_monitor()
for pos in positions_for_monitor:
    # Rate-limit: one emission per 60s per symbol:side
    if now - self._last_position_monitor_at.get(pm_key, 0) < 60:
        continue
    self._last_position_monitor_at[pm_key] = now
    candidates.append(
        strategy_sdk.candidate(... score=1, setup_id="position_monitor", ttl_seconds=15)
    )
```

Key design choices:
- **score=1**: Guarantees this never produces real signals
- **setup_id="position_monitor"**: Identified in build_signals_context() to skip normal evaluation
- **ttl_seconds=15**: Short enough to avoid context flooding
- **60s rate limit**: Only one monitor candidate per symbol per minute

### Phase 2: Handle in build_signals_from_context()

At the top of build_signals_context(), before any warmup/gate checks:

```python
if _setup == "position_monitor":
    position = strategy_sdk.position_snapshot(strategy_context)
    if not position.get("has_position"):
        return []
    close_signal = self._maybe_close_position(strategy_context, position, _side, state)
    if close_signal:
        return [close_signal]
    # Log MONITOR status
    self._log(context, "MONITOR", f"held {sym} {side} pnl={pnl:.2f} held_min={held_m:.0f} no_close")
    return []
```

### Phase 3: Position data source — dual path

**`_fetch_cached_positions_for_monitor()`** chooses between:
1. **Cache** (from `_fetch_strategy_positions()` context enrichment) — 30s TTL
2. **PSQL fallback** (cold-start) — subprocess calls psql to query `execution_position_basis`

Always calls `_seed_entry_times()` regardless of source.

## Pitfalls Encountered

### P1: Rate limiter sharing between discover and build_signals_context

**Symptom**: MONITOR decisions show `held_min=0` and no CLOSE ever fires.

**Root cause**: discover() sets `_last_position_monitor_at[monitor_key] = now` at emission. Then build_signals_context() checks `if now - last_check < 60 and last_check > 0: return []` — since discover just set the timestamp (last_check > 0, and now - last_check < 60), ALL context evaluations are rate-limited away for 60 seconds.

**Fix**: Don't add a separate rate limiter in build_signals_context(). Only rely on discover's 60s emission interval. With TTL=15s, one candidate = ~75 context evaluations, all running the full check. This is computationally cheap (~3 simple condition checks per call).

### P2: `_position_entry_at` empty after process restart

**Symptom**: After restart, time stop never fires even for positions held 12+ hours.

**Root cause**: `_position_entry_at` is an in-memory `__init__` variable (`self._position_entry_at = {}`). Restart wipes it. No mechanism restores it from persistent storage.

**Fix**: `_seed_entry_times()` queries `execution_position_basis.updated_at` from PostgreSQL, parses the ISO 8601 timestamp, and populates `_position_entry_at[symbol:side] = epoch`.

### P3: `updated_at` missing from Go context overlay

**Symptom**: Even with the DB seeding code, `_position_entry_at` stays empty because `_fetch_strategy_positions()` (which populates the cache) gets data from Go backend's `positionMap`, which **does not include `updated_at`**.

**Root cause**: Go backend `strategy_context_enricher.go` → `positionMap()` only returns: symbol, position_side, side, qty, quantity, avg_entry_price, entry_price, mark_price, notional, unrealized_pnl, leverage, margin_type. No `updated_at`.

**Fix (two-phase)**: 
1. **Go backend** adds `"updated_at": item.UpdatedAt` to `positionMap()` in `strategy_context_enricher.go`. After deploying the updated Go binary, the context overlay includes `updated_at` in every `strategy_positions` entry.
2. **Python fallback**: `_seed_entry_times()` has a DB fallback that queries `execution_position_basis` for `updated_at` when position data lacks it. This is a transitional measure — if the Go overlay provides `updated_at`, the DB path is never hit.

**⚠️ Hard boundary**: The DB fallback is a workaround for cold-start until Go backend provides `updated_at`. Per Rule #7, strategy code must NOT query the production database. Once Go overlay reliably includes `updated_at`, the `subprocess`+`psql` code must be removed from the strategy. (Or the Go overlay already provides it — verify by checking `evidence_json->>'strategy_positions'` in decision_logs.)

## Implementation Boundaries

### Strategy code must not query the database
Per Hard Rule #7, the Python strategy must never contain `subprocess` calls to `psql` or direct database connections. Position management data (entry timestamps, PnL, open positions) comes through the Go overlay via context enrichment:

```
Go: positionMap(portfolio.Position) → NATS context delta → Python: context["strategy_positions"] → _positions_cache
```

If `updated_at` is missing from the overlay (e.g., Go binary hasn't been redeployed), the strategy should degrade gracefully — skip time-stop evaluation rather than querying the DB.

### P4: PSQL timestamp format compatibility

**Symptom**: `datetime.datetime.fromisoformat()` fails on the timestamp.

**Root cause**: PostgreSQL `row_to_json()` outputs `2026-05-19T00:15:33.713+08:00`. Python 3.9's `fromisoformat()` handles this correctly (the T separator and +HH:MM timezone).

**No fix needed** — works with Python 3.9+. Always test with:
```python
dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
epoch = dt.timestamp()
```

### P5: Manifest hash glob pattern wrong extension

**Symptom**: `python3 -c "glob.glob('.../${F}.manifest.json')"` finds no files → hash not updated → strategy_manager reports "code hash mismatch" → strategy not loaded.

**Root cause**: `$F=workflow_distilled_funnel_0_1_0.py` → glob pattern becomes `.../workflow_distilled_funnel_0_1_0.py.manifest.json`. But the actual file is `workflow_distilled_funnel_0_1_0.manifest.json` (no `.py` before `.manifest`).

**Fix**: Always use the base name without extension for manifest files:
```bash
F_BASE="workflow_distilled_funnel_0_1_0"
find /opt/homebrew/var/crypto-trader -name "${F_BASE}.manifest.json"
```

### P6: SlowConsumer + subprocess timeout interaction

**Symptom**: During heavy SlowConsumer periods, `subprocess.check_output(timeout=5)` may hang, triggering `except Exception: return cached_positions` → empty cache → no monitor candidates.

**Mitigation**: The 5-second timeout prevents permanent hangs. DB fallback is only a cold-start path; after `_positions_cache` is populated from context enrichment, DB queries stop. If SlowConsumer persists, address via [discover-rate-limiting.md](discover-rate-limiting.md).

## Verification

Check that monitoring is working:

```sql
-- MONITOR decisions should appear
SELECT decision, reason, 
       (evidence_json->>'unrealized_pnl')::numeric as pnl,
       (evidence_json->>'held_minutes')::numeric as held_min
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND decision = 'MONITOR'
  AND created_at > now() - interval '5 minutes'
ORDER BY created_at DESC;

-- CLOSE decisions from monitoring  
SELECT reason, COUNT(*) as cnt
FROM strategy_decision_logs
WHERE strategy_id = 'workflow_distilled_funnel'
  AND decision = 'CLOSE'
  AND created_at > now() - interval '24 hours'
GROUP BY reason;
```

Expected MONITOR output:
```
MONITOR | held CGPTUSDT long pnl=-1.96 held_min=705 no_close
```

If `held_min=0` and `pnl=0.00` → `_position_entry_at` not seeded or context overlay missing position data.

## Race Conditions

- **CPU-bound PSQL call in discover()**: `subprocess.check_output()` spawns a psql process every 30-60s. Under SlowConsumer load (~280 dec/s), this adds latency to the event loop. If it times out (5s), monitor candidates are skipped for one cycle. The next cycle (3s later) retries.
- **`_seed_entry_times()` mutates `_position_entry_at`** without a lock. Could race if multiple discover cycles fire concurrently (unlikely with rate limiting, but possible with NATS async runtime). `if key not in self._position_entry_at` guard prevents overwrites but not concurrent writes. In practice: each position is seeded once per process lifetime.
