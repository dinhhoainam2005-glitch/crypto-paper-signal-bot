from __future__ import annotations

import unittest

from paper_signal_bot.strategy import INTERVAL_MS, evaluate_latest, prior_zscore


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

    def test_short_signal_requires_meta_filter_and_derivatives(self) -> None:
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

        blocked = evaluate_latest(
            symbol="ETHUSDT",
            timeframe="1h",
            klines=rows,
            premium_klines=premium_rows,
            derivatives_state_available=False,
            now_ms=now_ms,
        )
        self.assertEqual(blocked["status"], "NO_SIGNAL")


if __name__ == "__main__":
    unittest.main()
