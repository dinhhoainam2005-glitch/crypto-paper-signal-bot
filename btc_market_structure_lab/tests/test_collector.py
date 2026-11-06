import unittest

from market_structure import SYMBOL
from market_structure.collector import (
    StreamAudit,
    _prepare_binance_bootstrap,
    _readiness,
)


class StreamAuditTests(unittest.TestCase):
    def test_binance_raw_events_are_mapped_to_subscribed_topics(self) -> None:
        audit = StreamAudit("binance")
        topic = audit.observe({"e": "aggTrade", "E": 1000}, 1002)
        self.assertEqual(topic, "btcusdt@aggTrade")
        self.assertEqual(audit.counts[topic], 1)

    def test_binance_continuity_break_is_counted(self) -> None:
        audit = StreamAudit("binance")
        audit.observe(
            {"stream": "btcusdt@depth@100ms", "data": {"E": 1000, "pu": 9, "u": 10}},
            1002,
        )
        audit.observe(
            {"stream": "btcusdt@depth@100ms", "data": {"E": 1003, "pu": 8, "u": 11}},
            1005,
        )
        self.assertEqual(audit.sequence_breaks, 1)

    def test_bybit_update_regression_is_counted(self) -> None:
        audit = StreamAudit("bybit")
        audit.observe(
            {"topic": f"orderbook.50.{SYMBOL}", "ts": 1000, "data": {"u": 10}},
            1001,
        )
        audit.observe(
            {"topic": f"orderbook.50.{SYMBOL}", "ts": 1001, "data": {"u": 9}},
            1002,
        )
        self.assertEqual(audit.sequence_regressions, 1)

    def test_readiness_requires_non_liquidation_topics(self) -> None:
        audits = {"binance": StreamAudit("binance"), "bybit": StreamAudit("bybit")}
        for topic in (
            "binance_depth_snapshot",
            "btcusdt@aggTrade",
            "btcusdt@depth@100ms",
            "btcusdt@markPrice@1s",
        ):
            audits["binance"].counts[topic] = 1
        for topic in (
            f"publicTrade.{SYMBOL}",
            f"orderbook.50.{SYMBOL}",
            f"tickers.{SYMBOL}",
        ):
            audits["bybit"].counts[topic] = 1
        ready, missing = _readiness(audits)
        self.assertTrue(ready)
        self.assertEqual(missing, {"binance": [], "bybit": []})

    def test_binance_bootstrap_drops_stale_and_keeps_bridge_after_snapshot(self) -> None:
        snapshot = {"lastUpdateId": 100}
        buffered = [
            (900, {"e": "depthUpdate", "U": 90, "u": 99, "pu": 89}),
            (950, {"e": "depthUpdate", "U": 100, "u": 101, "pu": 99}),
        ]
        before, after, stale = _prepare_binance_bootstrap(snapshot, 1000, buffered)
        self.assertEqual(before, [])
        self.assertEqual(stale, 1)
        self.assertEqual(len(after), 1)
        self.assertGreater(after[0][0], 1000)
        self.assertEqual(after[0][1], 950)

    def test_binance_bootstrap_rejects_missing_bridge(self) -> None:
        snapshot = {"lastUpdateId": 100}
        buffered = [(950, {"e": "depthUpdate", "U": 102, "u": 103, "pu": 101})]
        with self.assertRaisesRegex(ValueError, "does not bridge"):
            _prepare_binance_bootstrap(snapshot, 1000, buffered)


if __name__ == "__main__":
    unittest.main()
