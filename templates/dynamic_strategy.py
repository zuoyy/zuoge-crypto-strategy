from __future__ import annotations

from runtime import strategy_sdk


class Strategy:
    strategy_id = "dynamic_funnel_strategy"
    strategy_version = "0.1.0"

    def __init__(self) -> None:
        self.decision_logs: list[dict] = []

    def discover(self, universe: dict) -> list[dict]:
        self.decision_logs = []
        candidates: list[dict] = []
        symbols = universe.get("symbols") or {}
        ranked = sorted(
            symbols.items(),
            key=lambda item: strategy_sdk.number((item[1] or {}).get("quote_volume")),
            reverse=True,
        )
        for symbol, item in ranked[:30]:
            change = strategy_sdk.number((item or {}).get("price_change_percent"))
            quote_volume = strategy_sdk.number((item or {}).get("quote_volume"))
            if quote_volume < 10_000_000 or abs(change) < 1.5:
                continue
            side = "long" if change > 0 else "short"
            score = min(100.0, 50.0 + abs(change) * 5.0)
            candidates.append(strategy_sdk.candidate(
                strategy_id=self.strategy_id,
                symbol=symbol,
                side=side,
                score=score,
                reason=f"universe momentum {side}: change={change:.2f}% quote_volume={quote_volume:.0f}",
                required_dependencies=[
                    strategy_sdk.dependency("l1_book"),
                    strategy_sdk.dependency("l2_book_top"),
                    strategy_sdk.dependency("mark_price"),
                    strategy_sdk.dependency("kline", "1m"),
                    strategy_sdk.dependency("kline", "5m"),
                ],
                ttl_seconds=300,
            ))
        return candidates[:10]

    def build_signals_from_context(self, context: dict) -> list[dict]:
        self.decision_logs = []
        candidate = context.get("candidate") or {}
        if not strategy_sdk.is_warm(context):
            self.decision_logs.append(strategy_sdk.decision_log(self.strategy_id, self.strategy_version, context, "WAIT_WARMUP", "candidate full market data is not warm"))
            return []
        side = str(candidate.get("side") or "long")
        score = strategy_sdk.number(candidate.get("score"), 0)
        price = strategy_sdk.price_for_side(context, side)
        spread_bps = strategy_sdk.number((context.get("microstructure") or {}).get("spread_bps"), 999)
        if price <= 0 or score < 70 or spread_bps > 25:
            self.decision_logs.append(strategy_sdk.decision_log(self.strategy_id, self.strategy_version, context, "NO_TRADE", "context thresholds not met", side=side, score=score))
            return []
        trade_params = strategy_sdk.basic_trade_params(context, side, price, risk_pct=2.0, stop_pct=0.012, reward_risk=2.5)
        signal = strategy_sdk.signal_envelope(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            context=context,
            side=side,
            confidence=0.6 + min(score, 100.0) / 500.0,
            reason=f"{side} funnel candidate confirmed score={score:.1f}",
            trade_params=trade_params,
        )
        self.decision_logs.append(strategy_sdk.decision_log(self.strategy_id, self.strategy_version, context, "SIGNAL", signal["reason"], side=side, score=score, signal_id=signal["signal_id"]))
        return [signal]
