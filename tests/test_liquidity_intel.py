from __future__ import annotations

import unittest

from paper_signal_bot.liquidity_intel import evaluate_liquidity_intel
from paper_signal_bot.strategy import INTERVAL_MS
from paper_signal_bot.telegram import format_liquidity_event_message


AT = 1_699_992_000_000


def kline(
    open_time: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    quote_volume: float,
    taker_ratio: float = 0.5,
) -> list:
    return [
        open_time,
        str(open_price),
        str(high),
        str(low),
        str(close),
        "100",
        open_time + INTERVAL_MS["15m"] - 1,
        str(quote_volume),
        100,
        "50",
        str(quote_volume * taker_ratio),
        "0",
    ]


def volume_rows(symbol: str, *, move: float = 0.0, taker_ratio: float = 0.5) -> list[list]:
    step = INTERVAL_MS["15m"]
    rows = []
    for index in range(30):
        close = 100.0
        rows.append(
            kline(
                AT - (30 - index) * step,
                open_price=100.0,
                high=100.1,
                low=99.9,
                close=close,
                quote_volume=1_000.0 + (index % 2) * 10.0,
            )
        )
    close = 100.0 * (1.0 + move)
    rows[-1] = kline(
        AT - step,
        open_price=100.0,
        high=max(100.2, close + 0.1),
        low=99.8,
        close=close,
        quote_volume=5_000.0,
        taker_ratio=taker_ratio,
    )
    return rows


def binance_book() -> dict:
    return {
        "lastUpdateId": 1,
        "bids": [["99.95", "5"], ["99.75", "2"], ["99.50", "1"]],
        "asks": [["100.05", "2"], ["100.20", "80"], ["100.60", "1"]],
    }


def hyperliquid_book() -> dict:
    return {
        "levels": [
            [{"px": "99.95", "sz": "5"}, {"px": "99.80", "sz": "2"}],
            [{"px": "100.05", "sz": "3"}, {"px": "100.20", "sz": "70"}],
        ]
    }


def oi_rows(start: float = 100_000_000.0, end: float = 98_000_000.0) -> list[dict]:
    out = []
    for index in range(13):
        value = start + (end - start) * index / 12
        out.append(
            {
                "symbol": "BTCUSDT",
                "sumOpenInterest": str(value / 100.0),
                "sumOpenInterestValue": str(value),
                "timestamp": str(AT - (12 - index) * 5 * 60 * 1000),
            }
        )
    return out


class LiquidityIntelTests(unittest.TestCase):
    def test_liquidity_map_event_combines_volume_oi_depth_and_hyperliquid(self) -> None:
        result = evaluate_liquidity_intel(
            klines_cache={("BTCUSDT", "15m"): volume_rows("BTCUSDT", move=0.008, taker_ratio=0.78)},
            depth_cache={"BTCUSDT": binance_book()},
            open_interest_cache={"BTCUSDT": oi_rows()},
            hyperliquid_cache={"BTCUSDT": hyperliquid_book()},
            now_ms=AT + 60_000,
        )

        event = result["events"][0]
        self.assertEqual(event["event_type"], "LIQUIDITY_MAP")
        self.assertEqual(event["symbol"], "BTCUSDT")
        self.assertEqual(event["side"], "LONG")
        self.assertEqual(event["reason"], "SHORT_LIQUIDATION_PRESSURE")
        self.assertTrue(event["watch_only"])
        self.assertFalse(event["auto_trade"])
        self.assertGreater(event["features"]["binance_wall_quote"], 0.0)
        self.assertEqual(event["features"]["hyperliquid_state"], "OK")

    def test_missing_hyperliquid_is_degraded_but_not_fatal(self) -> None:
        result = evaluate_liquidity_intel(
            klines_cache={("BTCUSDT", "15m"): volume_rows("BTCUSDT", move=0.008, taker_ratio=0.78)},
            depth_cache={"BTCUSDT": binance_book()},
            open_interest_cache={"BTCUSDT": oi_rows()},
            hyperliquid_cache={},
            now_ms=AT + 60_000,
        )

        event = result["events"][0]
        self.assertEqual(event["features"]["hyperliquid_state"], "UNAVAILABLE")
        self.assertEqual(event["reason"], "SHORT_LIQUIDATION_PRESSURE")

    def test_liquidity_format_is_watch_only_not_trade_entry(self) -> None:
        event = evaluate_liquidity_intel(
            klines_cache={("BTCUSDT", "15m"): volume_rows("BTCUSDT", move=-0.008, taker_ratio=0.22)},
            depth_cache={"BTCUSDT": binance_book()},
            open_interest_cache={"BTCUSDT": oi_rows()},
            hyperliquid_cache={"BTCUSDT": hyperliquid_book()},
            now_ms=AT + 60_000,
        )["events"][0]

        text = format_liquidity_event_message(event)
        self.assertIn("R27A BẢN ĐỒ THANH KHOẢN", text)
        self.assertIn("CHỈ THEO DÕI", text)
        self.assertIn("HEATMAP THANH KHOẢN", text)
        self.assertIn("ƯỚC LƯỢNG ÁP LỰC THANH LÝ", text)
        self.assertIn("BẢN ĐỒ HYPERLIQUID", text)
        self.assertNotIn("Entry:", text)
        self.assertNotIn("TP1", text)


if __name__ == "__main__":
    unittest.main()
