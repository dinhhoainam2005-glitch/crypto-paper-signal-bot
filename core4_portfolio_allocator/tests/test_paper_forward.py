from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from core4_portfolio_allocator.paper_forward.clients import BinanceClient, Candle
from core4_portfolio_allocator.paper_forward.config import (
    EXPECTED_SPEC_SHA256,
    RuntimeConfig,
    load_locked_spec,
    spec_sha256,
)
from core4_portfolio_allocator.paper_forward.engine import (
    DAY_MS,
    ForwardEngine,
    evaluate_candidate,
    new_position,
    process_position_path,
)
from core4_portfolio_allocator.paper_forward.state import ForwardStore
from core4_portfolio_allocator.paper_forward.telegram import format_signal


def daily_series(side: str = "LONG") -> dict[str, list[Candle]]:
    start = 1_700_000_000_000 // DAY_MS * DAY_MS
    output: dict[str, list[Candle]] = {}
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
        candles: list[Candle] = []
        for index in range(260):
            base = 100.0 + index * 0.10 if side == "LONG" else 200.0 - index * 0.10
            close = base
            if index == 259:
                close = 150.0 if side == "LONG" else 120.0
            open_time = start + index * DAY_MS
            candles.append(
                Candle(
                    open_time,
                    base,
                    max(base + 1.0, close),
                    min(base - 1.0, close),
                    close,
                    open_time + DAY_MS - 1,
                )
            )
        current_open = start + 260 * DAY_MS
        candles.append(
            Candle(
                current_open,
                candles[-1].close,
                candles[-1].close + 0.5,
                candles[-1].close - 0.5,
                candles[-1].close,
                current_open + DAY_MS - 1,
            )
        )
        output[symbol] = candles
    return output


class FakeClient:
    def __init__(self, daily: dict[str, list[Candle]]) -> None:
        self.daily = daily

    def daily_candles(self, symbol: str, limit: int = 260) -> list[Candle]:
        return self.daily[symbol]

    def minute_candles(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> list[Candle]:
        return []

    def funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
        return []


class FailingClient(FakeClient):
    def daily_candles(self, symbol: str, limit: int = 260) -> list[Candle]:
        raise OSError("offline")


class SpotFallbackClient(BinanceClient):
    def __init__(self) -> None:
        super().__init__(timeout_seconds=0.1, retries=0)
        self.base_urls = ("https://blocked-futures.example",)
        self.calls: list[tuple[str, str]] = []

    def _request_json(self, base_url: str, path: str, params: dict) -> object:
        self.calls.append((base_url, path))
        if base_url in self.base_urls:
            raise json.JSONDecodeError("non-json", "", 0)
        return [[1_700_000_000_000, "100", "102", "99", "101", "1", 1_700_086_399_999]]


class PaperForwardTests(unittest.TestCase):
    def test_daily_candles_fall_back_to_public_spot_market_data(self) -> None:
        client = SpotFallbackClient()
        candles = client.daily_candles("BTCUSDT", limit=1)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].close, 101.0)
        self.assertEqual(
            client.calls,
            [
                ("https://blocked-futures.example", "/fapi/v1/klines"),
                ("https://data-api.binance.vision", "/api/v3/klines"),
            ],
        )

    def test_frozen_spec_hash_and_controls(self) -> None:
        self.assertEqual(spec_sha256(), EXPECTED_SPEC_SHA256)
        spec = load_locked_spec()
        self.assertTrue(spec["controls"]["paper_only"])
        self.assertFalse(spec["controls"]["live_trading_enabled"])

    def test_long_candidate_has_complete_locked_plan(self) -> None:
        markets = daily_series("LONG")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 60_000
        signal = evaluate_candidate("ETHUSDT", markets, now_ms)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["plan"]["side"], "LONG")
        self.assertEqual(signal["plan"]["max_holding_hours"], 2160)
        self.assertEqual(signal["plan"]["reward_to_risk"], (1.0, 2.0, 4.0))

    def test_short_candidate_is_supported(self) -> None:
        markets = daily_series("SHORT")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 60_000
        signal = evaluate_candidate("SOLUSDT", markets, now_ms)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["plan"]["side"], "SHORT")
        self.assertGreater(signal["plan"]["stop_loss"], signal["plan"]["entry"])
        self.assertLess(signal["plan"]["take_profit_3"], signal["plan"]["entry"])

    def test_engine_deduplicates_signal(self) -> None:
        markets = daily_series("LONG")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 60_000
        with tempfile.TemporaryDirectory() as directory:
            config = RuntimeConfig(
                Path(directory) / "state.json", 60, 3600, 600, 40.0, 1500, False, True, True
            )
            store = ForwardStore(config.state_path, spec_sha256())
            engine = ForwardEngine(client=FakeClient(markets), store=store, config=config)
            first = engine.scan(now_ms)
            second = engine.scan(now_ms)
            self.assertEqual(len(first["state"]["signals"]), 4)
            self.assertEqual(len(second["state"]["signals"]), 4)
            self.assertEqual(len(second["state"]["active_positions"]), 4)
            gross = sum(
                position["initial_notional"] for position in second["state"]["active_positions"]
            )
            self.assertLessEqual(gross, 0.60 + 1e-12)

            restarted = ForwardEngine(client=FakeClient(markets), store=store, config=config)
            third = restarted.scan(now_ms)
            self.assertEqual(len(third["state"]["signals"]), 4)
            self.assertEqual(len(third["state"]["active_positions"]), 4)

    def test_engine_suppresses_stale_entry(self) -> None:
        markets = daily_series("LONG")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 700_000
        with tempfile.TemporaryDirectory() as directory:
            config = RuntimeConfig(
                Path(directory) / "state.json", 60, 3600, 600, 40.0, 1500, False, True, True
            )
            engine = ForwardEngine(
                client=FakeClient(markets),
                store=ForwardStore(config.state_path, spec_sha256()),
                config=config,
            )
            result = engine.scan(now_ms)
            self.assertEqual(len(result["state"]["active_positions"]), 0)
            self.assertTrue(
                all(item["status"] == "SUPPRESSED_STALE" for item in result["state"]["signals"])
            )

    def test_degraded_scan_keeps_complete_response_contract(self) -> None:
        markets = daily_series("LONG")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 60_000
        with tempfile.TemporaryDirectory() as directory:
            config = RuntimeConfig(
                Path(directory) / "state.json", 60, 3600, 600, 40.0, 1500, False, True, True
            )
            engine = ForwardEngine(
                client=FailingClient(markets),
                store=ForwardStore(config.state_path, spec_sha256()),
                config=config,
            )
            result = engine.scan(now_ms)
            self.assertEqual(result["status"], "DEGRADED")
            self.assertEqual(result["time_utc"], result["state"]["last_scan_utc"])
            self.assertEqual(result["notifications"], [])

    def test_minute_ambiguity_is_stop_first(self) -> None:
        markets = daily_series("LONG")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 60_000
        signal = evaluate_candidate("ETHUSDT", markets, now_ms)
        assert signal is not None
        position = new_position(signal, 1.0, 0.10)
        state = {
            "cash": 1.0 - position["entry_fee"],
            "events": [],
            "closed_trades": [],
            "signals": [signal],
        }
        candle = Candle(
            signal["entry_time_ms"],
            signal["plan"]["entry"],
            signal["plan"]["take_profit_2"],
            signal["plan"]["stop_loss"] - 0.01,
            signal["plan"]["entry"],
            signal["entry_time_ms"] + 59_999,
        )
        events, trade = process_position_path(
            state, position, [candle], markets["ETHUSDT"], []
        )
        self.assertEqual(events[0]["reason"], "STOP")
        self.assertIsNotNone(trade)

    def test_telegram_signal_contains_required_trade_plan(self) -> None:
        markets = daily_series("LONG")
        now_ms = markets["BTCUSDT"][-1].open_time_ms + 60_000
        signal = evaluate_candidate("BTCUSDT", markets, now_ms)
        assert signal is not None
        signal.update(
            current_price=signal["plan"]["entry"],
            entry_lag_seconds=60.0,
            directional_chase_bps=0.0,
            notional_fraction_allocated=0.1,
        )
        text = format_signal(signal)
        for required in (
            "Entry tham chiếu",
            "SL:",
            "TP1",
            "TP2",
            "TP3",
            "90 ngày",
            "PAPER ONLY",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
