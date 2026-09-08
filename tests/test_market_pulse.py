from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paper_signal_bot.market_pulse import MAX_PULSE_LAG_SECONDS, PULSE_MARKETS, THRESHOLDS, evaluate_market_pulses
from paper_signal_bot.storage import JsonStore
from paper_signal_bot.strategy import INTERVAL_MS
from paper_signal_bot.telegram import format_heartbeat_message, format_market_pulse_message
from paper_signal_bot.web import SignalService, deliver_pending
from tests.test_strategy import FakeClient, kline


AT = 1_699_992_000_000


def market_rows(direction=1):
    result = {}
    for symbol, tf in PULSE_MARKETS:
        step = INTERVAL_MS[tf]
        rows = [kline(AT - (30-i)*step, 100, 100.1, 99.9, 100, 1000 + i%2*10, tf) for i in range(30)]
        close = 100 * (1 + direction * THRESHOLDS[tf][symbol] * 1.25)
        rows[-1] = kline(AT-step, 100, max(close,100)+.1, min(close,100)-.1, close, 2000, tf)
        result[(symbol, tf)] = rows
    return result


class MarketPulseTests(unittest.TestCase):
    def test_both_directions_every_market_and_timeframe(self):
        for direction, side in [(1, "LONG"), (-1, "SHORT")]:
            with self.subTest(side=side):
                result = evaluate_market_pulses(market_rows(direction), AT+60000)
                self.assertEqual(len(result["events"]), 12)
                self.assertTrue(all(e["side"] == side for e in result["events"]))
                self.assertTrue(all(e["watch_only"] and e["auto_trade"] is False for e in result["events"]))

    def test_open_candle_never_creates_event_and_expired_not_replayed(self):
        rows = market_rows()
        self.assertEqual(evaluate_market_pulses(rows, AT-1)["events"], [])
        self.assertEqual(evaluate_market_pulses(rows, AT + (MAX_PULSE_LAG_SECONDS + 1) * 1000)["events"], [])

    def test_missing_stale_invalid_and_nan_feeds_not_used_for_breadth(self):
        for bad in ("missing", "gap", "stale", "nan"):
            rows = market_rows()
            for key in [("ETHUSDT", "15m"), ("SOLUSDT", "15m"), ("BNBUSDT", "15m")]:
                if bad == "missing":
                    rows.pop(key)
                elif bad == "gap":
                    del rows[key][-3]
                elif bad == "stale":
                    rows[key] = rows[key][:-1]
                else:
                    rows[key][-1][4] = "nan"
            result = evaluate_market_pulses(rows, AT+60000)
            self.assertFalse(any(e["timeframe"] == "15m" for e in result["events"]), bad)

    def test_service_dedupe_across_reload_cap_does_not_starve_and_no_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SignalService()
            service.client = FakeClient(market_rows(), [])
            service.store = JsonStore(Path(tmp)/"state.json")
            first = service.scan_once(now_ms_override=AT+60000)
            self.assertEqual(first["scan"]["new_market_event_count"], 12)
            # New service process with the same persisted ledger.
            second_service = SignalService()
            second_service.client = service.client
            second_service.store = JsonStore(service.store.path)
            second = second_service.scan_once(now_ms_override=AT+61000)
            third = second_service.scan_once(now_ms_override=AT+62000)
            fourth = second_service.scan_once(now_ms_override=AT+63000)
            self.assertEqual(len(third["state"]["market_events"]), 12)
            self.assertEqual(fourth["scan"]["new_market_event_count"], 0)
            self.assertEqual(second["scan"]["new_market_event_count"], 0)
            self.assertEqual(second["state"]["active_positions"], [])
            self.assertEqual(len({e["event_id"] for e in third["state"]["market_events"]}), 12)

    def test_delivery_retries_only_pending_and_expires_old_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SignalService()
            service.store = JsonStore(Path(tmp)/"state.json")
            event = evaluate_market_pulses(market_rows(), AT+60000)["events"][0]
            service.store.record_scan({}, [], [event])
            telegram = Mock(configured=True)
            telegram.send_message.side_effect = [RuntimeError("network"), {"ok": True}, {"ok": True}]
            with patch("paper_signal_bot.web.now_ms", return_value=AT+61000):
                deliver_pending(service, telegram)
                self.assertEqual(service.store.load()["market_events"][0]["delivery_status"], "PENDING")
                deliver_pending(service, telegram)
                deliver_pending(service, telegram)
            self.assertEqual(telegram.send_message.call_count, 2)
            self.assertEqual(service.store.load()["market_events"][0]["delivery_status"], "SENT")
            event["event_id"] += "-expired"
            service.store.record_scan({}, [], [event])
            with patch("paper_signal_bot.web.now_ms", return_value=AT+121000):
                deliver_pending(service, telegram)
            self.assertEqual(telegram.send_message.call_count, 3)
            self.assertEqual(service.store.load()["market_events"][-1]["delivery_status"], "SENT")
            event["event_id"] += "-very-expired"
            service.store.record_scan({}, [], [event])
            with patch("paper_signal_bot.web.now_ms", return_value=AT + (MAX_PULSE_LAG_SECONDS + 1) * 1000):
                deliver_pending(service, telegram)
            self.assertEqual(telegram.send_message.call_count, 3)
            self.assertEqual(service.store.load()["market_events"][-1]["delivery_status"], "EXPIRED")

    def test_trade_price_rechecked_at_send(self):
        for side, price in [("LONG", 101), ("SHORT", 99)]:
            with self.subTest(side=side), tempfile.TemporaryDirectory() as tmp:
                service = SignalService()
                service.store = JsonStore(Path(tmp)/"state.json")
                signal = {"signal_id":"trade-1", "delivery_status":"PENDING", "symbol":"BTCUSDT",
                          "side":side, "entry_price":100, "entry_time_ms":AT, "max_entry_lag_seconds":600,
                          "max_chase_bps":40}
                service.store.record_scan({}, [signal])
                service.client = Mock()
                service.client.ticker_price.return_value = {"price":price, "time":AT+60000}
                telegram = Mock(configured=True)
                with patch("paper_signal_bot.web.now_ms", return_value=AT+60000):
                    deliver_pending(service, telegram)
                telegram.send_message.assert_not_called()
                self.assertEqual(service.store.load()["signals"][0]["delivery_status"], "SUPPRESSED_CHASE_AT_SEND")

    def test_heartbeat_cannot_label_partial_stale_data_fresh(self):
        text = format_heartbeat_message({"groups":[
            {"symbol":"BTCUSDT", "timeframe":"4h", "status":"STALE_DATA", "data_state":"STALE"},
            {"symbol":"ETHUSDT", "timeframe":"1h", "status":"NO_SIGNAL", "data_state":"FRESH"},
        ]})
        self.assertIn("State: <b>DEGRADED</b>", text)
        self.assertNotIn("• Data: <b>FRESH</b>", text)

    def test_watch_format_never_calls_candle_price_current_or_trade_entry(self):
        event = evaluate_market_pulses(market_rows(-1), AT+60000)["events"][0]
        text = format_market_pulse_message(event)
        self.assertIn("WATCH ONLY", text)
        self.assertIn("SHORT", text)
        self.assertIn("| VN ", text)
        self.assertNotIn("Entry:", text)
        self.assertNotIn("Current:", text)


if __name__ == "__main__":
    unittest.main()
