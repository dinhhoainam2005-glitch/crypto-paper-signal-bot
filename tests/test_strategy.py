from __future__ import annotations

import unittest

from paper_signal_bot.strategy import INTERVAL_MS, STRATEGY_ID, candidate_groups, evaluate_latest, prior_zscore
from paper_signal_bot.telegram import format_heartbeat_message, format_signal_message, format_startup_message


def kline(open_time: int, open_price: float, high: float, low: float, close: float, quote_volume: float, taker_ratio: float) -> list:
    interval = INTERVAL_MS["1h"]
    return [
        open_time,
        str(open_price),
        str(high),
        str(low),
        str(close),
        "100",
        open_time + interval - 1,
        str(quote_volume),
        100,
        str(100 * taker_ratio),
        str(quote_volume * taker_ratio),
        "0",
    ]


def premium(open_time: int, value: float) -> list:
    interval = INTERVAL_MS["1h"]
    return [
        open_time,
        str(value),
        str(value),
        str(value),
        str(value),
        "0",
        open_time + interval - 1,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


class StrategyTests(unittest.TestCase):
    def test_prior_zscore_uses_prior_window(self) -> None:
        values = [10.0] * 20 + [20.0]
        self.assertEqual(prior_zscore(values, 20, 20), 0.0)
        values = [10.0 + i for i in range(20)] + [50.0]
        self.assertGreater(prior_zscore(values, 20, 20), 1.0)

    def test_short_signal_requires_volume_and_volatility_filter(self) -> None:
        rows = []
        start = 1_700_000_000_000
        for i in range(30):
            close = 100.0
            low = 99.0
            high = 102.0
            quote = 1000.0 + i * 10.0
            taker_ratio = 0.50
            if i == 29:
                close = 95.0
                low = 94.0
                high = 101.0
                quote = 2500.0
                taker_ratio = 0.40
            rows.append(kline(start + i * INTERVAL_MS["1h"], 100.0, high, low, close, quote, taker_ratio))
        premium_rows = [premium(start + i * INTERVAL_MS["1h"], 0.0) for i in range(30)]
        now_ms = start + 31 * INTERVAL_MS["1h"]

        result = evaluate_latest(
            symbol="ETHUSDT",
            timeframe="1h",
            klines=rows,
            premium_klines=premium_rows,
            derivatives_state_available=True,
            now_ms=now_ms,
        )
        self.assertEqual(result["status"], "SIGNAL")
        self.assertEqual(result["signals"][0]["side"], "SHORT")

        low_volume_rows = [row.copy() for row in rows]
        low_volume_rows[-1][7] = "1050"
        blocked = evaluate_latest(
            symbol="ETHUSDT",
            timeframe="1h",
            klines=low_volume_rows,
            premium_klines=premium_rows,
            derivatives_state_available=True,
            now_ms=now_ms,
        )
        self.assertEqual(blocked["status"], "NO_SIGNAL")

    def test_candidate_groups_include_r15c_markets(self) -> None:
        groups = candidate_groups()
        self.assertIn(("BTCUSDT", "4h"), groups)
        self.assertIn(("ETHUSDT", "1h"), groups)
        self.assertIn(("ETHUSDT", "4h"), groups)

    def test_telegram_message_is_plain_paper_signal(self) -> None:
        signal = {
            "symbol": "ETHUSDT",
            "side": "SHORT",
            "timeframe": "1h",
            "strategy_id": STRATEGY_ID,
            "signal_time_utc": "2026-08-11T13:00:00+00:00",
            "entry_time_utc": "2026-08-11T14:00:00+00:00",
            "entry_price": None,
            "candidate": {"candidate_id": "abc", "hold_bars": 12},
            "features": {
                "premium_close_prior_z_24": 0.12,
                "full_derivatives_state_available": True,
                "mkt_taker_quote_imbalance_derived": -0.22,
                "quote_volume_prior_z_20": 1.5,
                "volume_z_min": 1.6,
                "realized_vol_24": 0.0123,
                "realized_vol_24_min": 0.0056,
            },
        }
        text = format_signal_message(signal)
        self.assertIn("<b>TRADE | PAPER | R15C</b>", text)
        self.assertIn("<b>ETHUSDT SHORT</b>", text)
        self.assertIn("Realized vol24", text)
        self.assertIn("<b>Status</b>: PAPER ONLY, NOT LIVE", text)

    def test_startup_and_heartbeat_messages(self) -> None:
        startup = format_startup_message(
            strategy_id=STRATEGY_ID,
            scan_interval_seconds=300,
            heartbeat_interval_seconds=3600,
        )
        self.assertIn("<b>STARTUP | R15C Paper Bot</b>", startup)
        self.assertIn("<b>Mode</b>: PAPER ONLY", startup)
        self.assertIn("ETHUSDT 4h", startup)

        heartbeat = format_heartbeat_message(
            {
                "strategy_id": STRATEGY_ID,
                "new_signal_count": 0,
                "active_position_count": 0,
                "groups": [
                    {
                        "symbol": "ETHUSDT",
                        "timeframe": "1h",
                        "status": "NO_SIGNAL",
                        "latest_closed_bar_utc": "2026-08-11T13:00:00+00:00",
                        "premium_close_prior_z_24": 0.12,
                        "quote_volume_prior_z_20": 1.5,
                        "realized_vol_24": 0.0123,
                    }
                ],
            }
        )
        self.assertIn("<b>HEARTBEAT | R15C Paper Bot</b>", heartbeat)
        self.assertIn("<b>ETHUSDT 1h</b>", heartbeat)
        self.assertIn("Status: <code>NO_SIGNAL</code>", heartbeat)


if __name__ == "__main__":
    unittest.main()
