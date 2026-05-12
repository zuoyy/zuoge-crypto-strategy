import unittest

from dynamic_strategy import Strategy


class DynamicStrategyTest(unittest.TestCase):
    def test_no_symbols_returns_empty_candidates(self):
        self.assertEqual(Strategy().discover({"symbols": {}}), [])

    def test_universe_candidate_is_side_aware(self):
        strategy = Strategy()
        result = strategy.discover({"symbols": {"BTCUSDT": {"quote_volume": "20000000", "price_change_percent": "2.5"}}})
        self.assertEqual(result[0]["symbol"], "BTCUSDT")
        self.assertEqual(result[0]["side"], "long")

    def test_cold_context_waits(self):
        strategy = Strategy()
        result = strategy.build_signals_from_context({"symbol": "BTCUSDT", "candidate": {"side": "long"}, "snapshot": {"freshness_ms": {}}})
        self.assertEqual(result, [])
        self.assertEqual(strategy.decision_logs[0]["decision"], "WAIT_WARMUP")


if __name__ == "__main__":
    unittest.main()
