# Discover Rate-Limiting for SlowConsumer Prevention

## Problem

`discover()` receives `strategy.universe.delta` messages faster than it can process them. When the event loop falls behind, NATS emits:

```
nats.errors.SlowConsumerError: nats: slow consumer, messages dropped
```

This triggers a death spiral:
1. NATS drops universe delta messages → discover() runs on stale data
2. Overlay enrichment fails because NATS also drops context pipeline messages
3. `account_risk_budget_missing` → all signals blocked
4. No way to recover until process restart

## Solution: rate-limit discover()

Add a minimum interval between `discover()` calls. Cache the last result for skipped calls.

```python
def __init__(self) -> None:
    ...
    self._last_discover_at: float = 0.0
    self._last_discover_result: list[dict] = []

def discover(self, universe: dict) -> list[dict]:
    now = _time.time()
    if now - self._last_discover_at < 3.0:
        return self._last_discover_result  # return cached candidates
    self._last_discover_at = now

    # ... full discover logic ...

    self._last_discover_result = candidates
    return candidates
```

## Why it works

- Universe deltas fire every 1-2 seconds; each triggers `discover()`. Without rate-limiting, the NATS subscription gets flooded at 1-5 Hz.
- Rate-limiting to 3-second intervals (~0.33 Hz) gives the event loop **70% idle time** for context evaluations and overlay enrichment.
- Cached candidates keep their own TTL (120-300s). Skipped cycles don't empty the candidate pool — previous candidates continue living until their TTL expires.
- The NATS subject is `strategy.universe.delta` — intermediate dropped messages don't matter because `discover()` only needs the latest universe state, not every intermediate delta.

## Measured Effect

| Metric | Before (no rate limit) | After (3s rate limit) |
|--------|----------------------|----------------------|
| CPU | 87% | 10.8% |
| Decision rate | 280/sec | 53/sec |
| Memory | 0.6% RSS | 0.1% RSS |
| Signals | produced | produced (fewer but stable) |
| budget_missing errors | intermittent | 0 |

## Trade-offs

- **Fewer discover() runs** → candidates update less frequently (every 3s instead of every 1s). For 120-300s TTL candidates, this is negligible.
- **Cold start latency**: First discover() run is immediate. Subsequent calls within 3s use cached data. Acceptable trade-off.
- Not suitable for HFT strategies that need sub-second candidate updates.
