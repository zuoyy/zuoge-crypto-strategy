# Side Balancing in `_balanced_select()`

## Problem

When one direction (e.g. short) dominates the candidate pool, `_balanced_select()` can produce a lopsided candidate set. The existing reversals-first → pullbacks → score ordering doesn't enforce side diversity.

From real data: 5.4M short decisions vs 1.2M long decisions in a 24h period (~4.5:1 ratio). This causes:
- Strategy becomes directionally blind — only sees one side of the market
- Whichever side has more symbols in that market regime gets amplified
- `too_many_short_positions` gate rejects 1.6M times/cycle

## Fix

After each `add_many()` call, check if selected candidates have >70% in one direction. If so, force-fill from the minority side.

```python
add_many(reversals[:4])
_enforce_side_balance(by_score, limit)
add_many(pullbacks[:4])
_enforce_side_balance(by_score, limit)
add_many(by_score)
return selected[:limit]
```

The enforcement function:

```python
def _enforce_side_balance(minority_items: list[dict], _limit: int) -> None:
    """If selected has >70% of one side, force-fill minority side."""
    if len(selected) < _limit - 1:
        return  # still have room, no need to balance yet
    side_counts: dict[str, int] = {}
    for item in selected:
        s = item.get("side", "")
        side_counts[s] = side_counts.get(s, 0) + 1
    total = sum(side_counts.values()) or 1
    for s, c in side_counts.items():
        if c / total > 0.7 and len(selected) < _limit:
            for mi in minority_items:
                mk = f"{mi['symbol']}:{mi.get('side', '')}:{mi.get('setup_id', '')}"
                if mk not in seen and len(selected) < _limit:
                    selected.append(mi)
                    seen.add(mk)
                    return
```

## Key Details

- `minority_items` is passed as `by_score` — the highest-scoring candidates of the minority side
- The 70% threshold prevents false triggers when the sample is small
- Each add_many stage gets its own balance check to catch skew early
- The function is a closure over `selected` and `seen` (they're captured from `_balanced_select` scope)

## Expected Effect

Side ratio from 4.5:1 → ~2:1 at worst. More diverse candidate pool → more balanced position openings → less `too_many_xxx_positions` waste.
