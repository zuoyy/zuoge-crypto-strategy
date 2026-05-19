# Time Stop & Hard Cooldown After Losing Trades

## Problem

Positions can bleed slowly for 2+ hours without hitting the 5% loss stop. Real example:
- GALAUSDT short: held 120 minutes, -$1.06 loss (2.1% on notional, under the 5% stop threshold)
- OPUSDT short: held 120 minutes, -$0.79 loss

These positions only closed because the candidate TTL expired, not because of proactive risk management.

Additionally, repeated stop-loss fills on the same symbol (5 partial fills over 2h for GALA) waste fills and commission with no recovery.

## Fix Components

### 1. Position Entry Time Tracking

Add a dict in `__init__`:

```python
self._position_entry_at: dict[str, float] = {}  # symbol:side → entry timestamp
```

Record entry time when a SIGNAL opens a new position (not an add):

```python
if not position["has_position"]:
    self._position_entry_at[f"{_sym}:{side}"] = _time.time()
```

### 2. Time Stop in `_maybe_close_position()`

```python
if not close:
    entry_ts = self._position_entry_at.get(f"{state.get('symbol', '')}:{pos_side}", 0)
    if entry_ts > 0:
        held_minutes = (_time.time() - entry_ts) / 60.0
        if held_minutes > 60 and position.get("unrealized_pnl", 0) <= 0:
            close = True
            reason = f"time_stop_{int(held_minutes)}min_no_profit"
```

**Rationale**: 60 minutes is enough for a reversal-based entry to prove correct. If the position is flat or losing after 60min, the setup was wrong. Better to cut early than bleed for 2+ hours.

### 3. Hard Cooldown After Losing Close

Add in `__init__`:

```python
self._hard_cooldown_until: dict[str, float] = {}  # symbol → cooldown expires at
```

Check in `discover()`:

```python
cooldown_ts = self._hard_cooldown_until.get(symbol, 0)
if cooldown_ts > 0 and _time.time() < cooldown_ts:
    continue  # skip this symbol entirely
```

Set cooldown in `_build_close_signal()`:

```python
symbol_cooldown_ts = self._hard_cooldown_until.get(symbol, 0)
if _time.time() < symbol_cooldown_ts:
    # Already in cooldown — extend (repeated stop-out)
    self._hard_cooldown_until[symbol] = _time.time() + 1800
elif position.get("unrealized_pnl", 0) <= 0:
    # First losing close — 15 min cooldown
    self._hard_cooldown_until[symbol] = _time.time() + 900
```

## Timing Diagram

```
Entry                         Close (losing)
  │──────────────────────────────│
  │<---- 60 min time stop ----->│  ← if still flat/losing, force close
                                │
                                └──> 15min hard cooldown
                                     (no candidates for this symbol)
                                         │
                                         ├── Re-entry attempt → 30min extension
                                         │    (repeated stop-out pattern)
                                         │
                                         └── Cooldown expires → normal candidate generation resumes
```

## Expected Effect

- Time stop prevents the GALA/OPUS pattern (2h bleed with no recovery)
- Hard cooldown prevents traumatic re-entry after a clearly wrong trade
- Repeated stop-outs get exponentially longer cooldowns (15min → 30min → ...)
