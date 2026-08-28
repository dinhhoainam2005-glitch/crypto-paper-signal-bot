from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paper_signal_bot.storage import JsonStore
from paper_signal_bot.strategy import INTERVAL_MS, STRATEGY_ID, R22C_MARKETS, candidate_groups, evaluate_latest, prior_zscore
from paper_signal_bot.telegram import format_heartbeat_message, format_signal_message, format_startup_message
from paper_signal_bot.web import SignalService
from paper_signal_bot.worker import notifiable_signals


def kline(open_time: int, open_price: float, high: float, low: float, close: float, quote_volume: float, interval: str = "4h") -> list:
    interval_ms = INTERVAL_MS[interval]
    return [
        open_time,
        str(open_price),
        str(high),
        str(low),
        str(close),
        "100",
        open_time + interval_ms - 1,
        str(quote_volume),
        100,
        "50",
        str(quote_volume * 0.5),
        "0",
    ]


def premium(open_time: int, value: float, interval: str = "4h") -> list:
    interval_ms = INTERVAL_MS[interval]
    return [
        open_time,
        str(value),
        str(value),
        str(value),
        str(value),
        "0",
        open_time + interval_ms - 1,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


def trending_rows(start: int, *, base: float, step: float, interval: str = "4h") -> list[list]:
    rows = []
    interval_ms = INTERVAL_MS[interval]
    for i in range(80):
        close = base + i * step
        open_price = close - step * 0.25
        rows.append(kline(start + i * interval_ms, open_price, close + 0.5, close - 1.0, close, 1000.0, interval))
    next_open = start + 80 * interval_ms
    rows.append(kline(next_open, base + 80 * step, base + 80 * step + 0.25, base + 80 * step - 0.25, base + 80 * step, 1000.0, interval))
    return rows


class FakeClient:
    def __init__(self, rows_by_symbol: dict[str, list[list]], premium_rows: list[list]) -> None:
        self._rows_by_symbol = rows_by_symbol
        self._premium_rows = premium_rows

    def klines(self, symbol: str, interval: str, limit: int = 220) -> list[list]:
        return self._rows_by_symbol[symbol][-limit:]

    def premium_index_klines(self, symbol: str, interval: str, limit: int = 100) -> list[list]:
        return self._premium_rows[-limit:]

    def derivatives_state_available(self, symbol: str) -> bool:
        return True


class StrategyTests(unittest.TestCase):
    def test_prior_zscore_uses_prior_window(self) -> None:
        values = [10.0] * 20 + [20.0]
        self.assertEqual(prior_zscore(values, 20, 20), 0.0)
        values = [10.0 + i for i in range(20)] + [50.0]
        self.assertGreater(prior_zscore(values, 20, 20), 1.0)

    def test_breadth_router_emits_btc_4h_long_signal(self) -> None:
        start = 1_700_000_000_000
        rows_by_symbol = {
            "BTCUSDT": trending_rows(start, base=100.0, step=1.0),
            "ETHUSDT": trending_rows(start, base=200.0, step=1.5),
            "SOLUSDT": trending_rows(start, base=50.0, step=0.6),
            "BNBUSDT": trending_rows(start, base=300.0, step=1.2),
        }
        premium_rows = [premium(start + i * INTERVAL_MS["4h"], 0.0) for i in range(81)]
        now_ms = start + 80 * INTERVAL_MS["4h"] + 1

        result = evaluate_latest(
            symbol="BTCUSDT",
            timeframe="4h",
            klines=rows_by_symbol["BTCUSDT"],
            premium_klines=premium_rows,
            derivatives_state_available=True,
            market_klines_by_symbol=rows_by_symbol,
            now_ms=now_ms,
        )

        self.assertEqual(result["status"], "SIGNAL")
        signal = result["signals"][0]
        self.assertEqual(signal["strategy_id"], STRATEGY_ID)
        self.assertEqual(signal["side"], "LONG")
        self.assertEqual(signal["sleeve_id"], "4h_LONG_mp4_rf0p25")
        self.assertEqual(signal["risk_fraction"], 0.25)
        self.assertGreaterEqual(signal["features"]["market_breadth_count"], 2)

    def test_candidate_groups_include_r22c_markets(self) -> None:
        groups = candidate_groups()
        for market in R22C_MARKETS:
            self.assertIn(market, groups)
        self.assertNotIn(("ETHUSDT", "1h"), groups)

    def test_telegram_message_is_r22c_paper_signal(self) -> None:
        signal = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "timeframe": "4h",
            "strategy_id": STRATEGY_ID,
            "signal_time_utc": "2026-08-20T00:00:00+00:00",
            "entry_time_utc": "2026-08-20T04:00:00+00:00",
            "entry_price": None,
            "sleeve_id": "4h_LONG_mp4_rf0p25",
            "risk_fraction": 0.25,
            "candidate": {"candidate_id": "breadth_breakout_BTC", "hold_bars": 12, "family": "breadth_breakout"},
            "features": {
                "close": 100.0,
                "market_breadth_count": 3,
                "market_breadth_assets": 4,
                "breadth_n": 2,
                "breadth_min": 0.008,
                "market_directional_mean": 0.021,
                "quote_volume_prior_z_20": 0.5,
                "volz_min": -0.75,
                "sleeve_id": "4h_LONG_mp4_rf0p25",
                "risk_fraction": 0.25,
            },
        }
        text = format_signal_message(signal)
        self.assertIn("🟢🔺 <b>PAPER LONG — BTCUSDT</b> 🔺🟢", text)
        self.assertIn("✅ <b>LIVE PAPER SIGNAL</b>", text)
        self.assertIn("🧩 Sleeve: <code>4h_LONG_mp4_rf0p25</code>", text)
        self.assertIn("📊 <b>SIGNAL QUALITY</b>", text)
        self.assertIn("🔒 <b>PAPER ONLY / NO AUTO-TRADE</b>", text)

    def test_startup_and_heartbeat_messages(self) -> None:
        startup = format_startup_message(
            strategy_id=STRATEGY_ID,
            scan_interval_seconds=300,
            heartbeat_interval_seconds=3600,
        )
        self.assertIn("💞📡 <b>R22C PAPER BOT STARTUP — MARKET WATCH ACTIVE</b>", startup)
        self.assertIn("📌 Mode: <b>PAPER SIGNAL ONLY</b>", startup)
        self.assertIn("SOLUSDT 4h", startup)
        self.assertIn("🔒 <b>SIGNAL ONLY / NO AUTO-TRADE</b>", startup)

        heartbeat = format_heartbeat_message(
            {
                "strategy_id": STRATEGY_ID,
                "time_utc": "2026-08-20T04:30:00+00:00",
                "new_signal_count": 0,
                "active_position_count": 0,
                "groups": [
                    {
                        "symbol": "BTCUSDT",
                        "timeframe": "4h",
                        "status": "NO_SIGNAL",
                        "latest_closed_bar_utc": "2026-08-20T00:00:00+00:00",
                        "candidate_count": 2,
                        "market_breadth_count": 3,
                        "market_breadth_assets": 4,
                        "breadth_n": 2,
                        "market_directional_mean": 0.021,
                        "quote_volume_prior_z_20": 0.5,
                    }
                ],
            }
        )
        self.assertIn("💞📡 <b>R22C PAPER BOT HEARTBEAT — MARKET WATCH ACTIVE</b>", heartbeat)
        self.assertIn("<b>BTCUSDT 4h</b>", heartbeat)
        self.assertIn("• Rules scanned: <b>2</b>", heartbeat)
        self.assertIn("• Breadth: <code>3/4</code> >= <code>2</code>", heartbeat)
        self.assertIn("🛡️ <b>SAFETY</b>", heartbeat)

    def test_service_dedupes_same_signal_across_scans(self) -> None:
        start = 1_700_000_000_000
        rows_by_symbol = {
            "BTCUSDT": trending_rows(start, base=100.0, step=1.0),
            "ETHUSDT": trending_rows(start, base=200.0, step=1.5),
            "SOLUSDT": trending_rows(start, base=50.0, step=0.6),
            "BNBUSDT": trending_rows(start, base=300.0, step=1.2),
        }
        premium_rows = [premium(start + i * INTERVAL_MS["4h"], 0.0) for i in range(81)]

        with tempfile.TemporaryDirectory() as tmp:
            service = SignalService()
            service.client = FakeClient(rows_by_symbol, premium_rows)
            service.store = JsonStore(Path(tmp) / "paper_state.json")

            first = service.scan_once()
            second = service.scan_once()

        self.assertGreaterEqual(first["scan"]["new_signal_count"], 1)
        self.assertEqual(len(first["scan"]["new_signals"]), first["scan"]["new_signal_count"])
        self.assertEqual(second["scan"]["new_signal_count"], 0)
        self.assertEqual(second["scan"]["new_signals"], [])

    def test_worker_only_notifies_new_signals(self) -> None:
        new_signal = {"signal_id": "new-1", "symbol": "BTCUSDT"}
        scan_result = {
            "scan": {
                "new_signals": [new_signal],
                "groups": [
                    {
                        "signals": [
                            {"signal_id": "old-1", "symbol": "BTCUSDT", "suppressed_reason": "DUPLICATE_SIGNAL_ID"},
                            {"symbol": "ETHUSDT", "suppressed_reason": "ACTIVE_POSITION"},
                        ]
                    }
                ],
            }
        }
        self.assertEqual(notifiable_signals(scan_result), [new_signal])


if __name__ == "__main__":
    unittest.main()
