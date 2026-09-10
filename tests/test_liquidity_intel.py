from __future__ import annotations

import unittest

from paper_signal_bot.liquidity_intel import LIQUIDITY_INTERVALS, evaluate_liquidity_intel
from paper_signal_bot.strategy import INTERVAL_MS
from paper_signal_bot.telegram import format_liquidity_event_message, liquidity_symbol_readiness


AT = 1_699_992_000_000


def kline(
    open_time: int,
    *,
    timeframe: str = "1h",
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
        open_time + INTERVAL_MS[timeframe] - 1,
        str(quote_volume),
        100,
        "50",
        str(quote_volume * taker_ratio),
        "0",
    ]


def volume_rows(
    symbol: str,
    *,
    timeframe: str = "1h",
    move: float = 0.0,
    taker_ratio: float = 0.5,
) -> list[list]:
    step = INTERVAL_MS[timeframe]
    rows = []
    for index in range(30):
        close = 100.0
        rows.append(
            kline(
                AT - (30 - index) * step,
                timeframe=timeframe,
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
        timeframe=timeframe,
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
    def test_readiness_counts_four_symbols_not_twelve_timeframe_feeds(self) -> None:
        groups = [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "status": "NO_EVENT",
                "data_state": "FRESH",
            }
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
            for timeframe in LIQUIDITY_INTERVALS
        ]

        self.assertEqual(liquidity_symbol_readiness(groups), (4, 4))

    def test_liquidity_map_event_combines_volume_oi_depth_and_hyperliquid(self) -> None:
        result = evaluate_liquidity_intel(
            klines_cache={("BTCUSDT", "1h"): volume_rows("BTCUSDT", move=0.015, taker_ratio=0.78)},
            depth_cache={"BTCUSDT": binance_book()},
            open_interest_cache={"BTCUSDT": oi_rows()},
            hyperliquid_cache={"BTCUSDT": hyperliquid_book()},
            now_ms=AT + 60_000,
        )

        event = result["events"][0]
        self.assertEqual(event["event_type"], "LIQUIDITY_MAP")
        self.assertEqual(event["symbol"], "BTCUSDT")
        self.assertEqual(event["timeframe"], "1h")
        self.assertEqual(event["side"], "LONG")
        self.assertEqual(event["reason"], "SHORT_LIQUIDATION_PRESSURE")
        self.assertTrue(event["watch_only"])
        self.assertFalse(event["auto_trade"])
        self.assertGreater(event["features"]["binance_wall_quote"], 0.0)
        self.assertEqual(event["features"]["hyperliquid_state"], "OK")
        self.assertEqual(result["timeframes"], list(LIQUIDITY_INTERVALS))
        self.assertNotIn("15m", {group["timeframe"] for group in result["groups"]})

    def test_missing_hyperliquid_is_degraded_but_not_fatal(self) -> None:
        result = evaluate_liquidity_intel(
            klines_cache={("BTCUSDT", "1h"): volume_rows("BTCUSDT", move=0.015, taker_ratio=0.78)},
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
            klines_cache={("BTCUSDT", "1h"): volume_rows("BTCUSDT", move=-0.015, taker_ratio=0.22)},
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
        self.assertIn("Biến động 1h", text)
        self.assertNotIn("Biến động 15m", text)
        self.assertNotIn("Entry:", text)
        self.assertNotIn("TP1", text)

    def test_largest_timeframe_wins_when_multiple_maps_alert(self) -> None:
        caches = {
            ("BTCUSDT", "1h"): volume_rows("BTCUSDT", timeframe="1h", move=0.015, taker_ratio=0.78),
            ("BTCUSDT", "4h"): volume_rows("BTCUSDT", timeframe="4h", move=0.030, taker_ratio=0.78),
            ("BTCUSDT", "1d"): volume_rows("BTCUSDT", timeframe="1d", move=0.060, taker_ratio=0.78),
        }
        result = evaluate_liquidity_intel(
            klines_cache=caches,
            depth_cache={"BTCUSDT": binance_book()},
            open_interest_cache={"BTCUSDT": oi_rows()},
            hyperliquid_cache={"BTCUSDT": hyperliquid_book()},
            now_ms=AT + 60_000,
        )

        btc_events = [event for event in result["events"] if event["symbol"] == "BTCUSDT"]
        self.assertEqual(len(btc_events), 1)
        self.assertEqual(btc_events[0]["timeframe"], "1d")


if __name__ == "__main__":
    unittest.main()
