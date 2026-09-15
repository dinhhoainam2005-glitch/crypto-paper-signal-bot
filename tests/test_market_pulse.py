from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paper_signal_bot.market_pulse import MAX_PULSE_LAG_SECONDS, PULSE_MARKETS, THRESHOLDS, evaluate_market_pulses
from paper_signal_bot.storage import JsonStore
from paper_signal_bot.strategy import INTERVAL_MS
from paper_signal_bot.telegram import format_heartbeat_message, format_market_pulse_message
from paper_signal_bot.web import SignalService, deliver_pending, scan_notify_once
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
                self.assertEqual(len(result["events"]), 8)
                self.assertTrue(all(e["side"] == side for e in result["events"]))
                self.assertTrue(all(e["watch_only"] and e["auto_trade"] is False for e in result["events"]))
                self.assertEqual(
                    {event["timeframe"] for event in result["events"]},
                    {"1h", "4h"},
                )

    def test_open_candle_never_creates_event_and_expired_not_replayed(self):
        rows = market_rows()
        self.assertEqual(evaluate_market_pulses(rows, AT-1)["events"], [])
        self.assertEqual(evaluate_market_pulses(rows, AT + (MAX_PULSE_LAG_SECONDS + 1) * 1000)["events"], [])

    def test_missing_stale_invalid_and_nan_feeds_not_used_for_breadth(self):
        for bad in ("missing", "gap", "stale", "nan"):
            rows = market_rows()
            for key in [("ETHUSDT", "1h"), ("SOLUSDT", "1h"), ("BNBUSDT", "1h")]:
                if bad == "missing":
                    rows.pop(key)
                elif bad == "gap":
                    del rows[key][-3]
                elif bad == "stale":
                    rows[key] = rows[key][:-1]
                else:
                    rows[key][-1][4] = "nan"
            result = evaluate_market_pulses(rows, AT+60000)
            self.assertFalse(any(e["timeframe"] == "1h" for e in result["events"]), bad)

    def test_service_dedupe_across_reload_cap_does_not_starve_and_no_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SignalService()
            service.liquidity_enabled = False
            service.macro_enabled = False
            service.client = FakeClient(market_rows(), [])
            service.store = JsonStore(Path(tmp)/"state.json")
            first = service.scan_once(now_ms_override=AT+60000)
            self.assertEqual(first["scan"]["new_market_event_count"], 8)
            # New service process with the same persisted ledger.
            second_service = SignalService()
            second_service.liquidity_enabled = False
            second_service.macro_enabled = False
            second_service.client = service.client
            second_service.store = JsonStore(service.store.path)
            second = second_service.scan_once(now_ms_override=AT+61000)
            third = second_service.scan_once(now_ms_override=AT+62000)
            fourth = second_service.scan_once(now_ms_override=AT+63000)
            self.assertEqual(len(third["state"]["market_events"]), 8)
            self.assertEqual(fourth["scan"]["new_market_event_count"], 0)
            self.assertEqual(second["scan"]["new_market_event_count"], 0)
            self.assertEqual(second["state"]["active_positions"], [])
            self.assertEqual(len({e["event_id"] for e in third["state"]["market_events"]}), 8)

    def test_delivery_retries_only_pending_and_expires_old_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SignalService()
            service.liquidity_enabled = False
            service.macro_enabled = False
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

    def test_macro_events_are_sent_as_one_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SignalService()
            service.store = JsonStore(Path(tmp)/"state.json")
            events = [
                {
                    "event_id": f"macro-{index}",
                    "event_type": "MACRO_EVENT",
                    "title": title,
                    "priority": "CRITICAL",
                    "impact_score": 90 - index,
                    "phase": "T-24H",
                    "event_time_ms": AT + index * 60000,
                    "event_time_utc": "2023-11-14T22:13:20+00:00",
                    "expires_at_ms": AT + 120000,
                    "delivery_status": "PENDING",
                }
                for index, title in enumerate(("Consumer Price Index", "Employment Situation"))
            ]
            service.store.record_scan({}, [], events)
            telegram = Mock(configured=True)
            telegram.send_message.return_value = {"ok": True}

            with patch("paper_signal_bot.web.now_ms", return_value=AT + 1000):
                delivered = deliver_pending(service, telegram)

            self.assertEqual(len(delivered), 2)
            telegram.send_message.assert_called_once()
            self.assertIn("2 SỰ KIỆN", telegram.send_message.call_args.args[0])
            self.assertTrue(
                all(item["delivery_status"] == "SENT" for item in service.store.load()["market_events"])
            )

    def test_startup_folds_macro_batch_into_one_telegram_message(self):
        service = Mock()
        service.scan_once.return_value = {
            "scan": {
                "strategy_id": "R26A_TEST",
                "completed_utc": "2026-09-10T03:12:00+00:00",
                "groups": [],
                "pulse_groups": [],
                "liquidity_groups": [],
                "macro_groups": [],
                "trade_readiness": {"trade_a_plus_eligible": False, "metrics": {}},
            }
        }
        service.store.load.return_value = {"active_positions": []}
        macro_batch = [
            {
                "event_id": "macro-cpi",
                "event_type": "MACRO_EVENT",
                "title": "Consumer Price Index",
                "priority": "CRITICAL",
                "impact_score": 95,
                "phase": "T-24H",
                "event_time_ms": AT,
                "event_time_utc": "2026-09-11T12:30:00+00:00",
            },
            {
                "event_id": "macro-fomc",
                "event_type": "MACRO_EVENT",
                "title": "FOMC Press Conference",
                "priority": "CRITICAL",
                "impact_score": 96,
                "phase": "T-7D",
                "event_time_ms": AT + 60000,
                "event_time_utc": "2026-09-16T18:30:00+00:00",
            },
        ]
        telegram = Mock(configured=True)
        telegram.send_message.return_value = {"ok": True}

        with (
            patch("paper_signal_bot.web.SERVICE", service),
            patch("paper_signal_bot.web.TelegramSender", return_value=telegram),
            patch("paper_signal_bot.web.deliver_pending", return_value=macro_batch),
            patch("paper_signal_bot.web.effective_scan_interval_seconds", return_value=60),
            patch("paper_signal_bot.web.env_enabled", return_value=True),
        ):
            scan_notify_once(heartbeat_interval_seconds=3600, startup=True)

        telegram.send_message.assert_called_once()
        message = telegram.send_message.call_args.args[0]
        self.assertIn("ĐÃ KHỞI ĐỘNG", message)
        self.assertIn("VĨ MÔ CẦN LƯU Ý (2)", message)
        self.assertIn("Chỉ số giá tiêu dùng CPI", message)
        self.assertEqual(service.store.record_delivery.call_count, 2)

    def test_trade_price_rechecked_at_send(self):
        for side, price in [("LONG", 101), ("SHORT", 99)]:
            with self.subTest(side=side), tempfile.TemporaryDirectory() as tmp:
                service = SignalService()
                service.liquidity_enabled = False
                service.macro_enabled = False
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
        self.assertIn("Hệ thống: <b>SUY GIẢM</b>", text)
        self.assertIn("Dữ liệu: <b>SUY GIẢM</b>", text)
        self.assertIn("BTCUSDT 4h: <code>DỮ LIỆU CŨ</code>", text)

    def test_watch_format_never_calls_candle_price_current_or_trade_entry(self):
        event = evaluate_market_pulses(market_rows(-1), AT+60000)["events"][0]
        text = format_market_pulse_message(event)
        self.assertIn("CHỈ THEO DÕI", text)
        self.assertIn("SHORT (BÁN)", text)
        self.assertIn("| VN ", text)
        self.assertNotIn("Entry:", text)
        self.assertNotIn("Current:", text)


if __name__ == "__main__":
    unittest.main()
