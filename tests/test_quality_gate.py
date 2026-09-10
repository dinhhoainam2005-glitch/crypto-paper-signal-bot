from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from paper_signal_bot.quality_gate import (
    TRADE_A_PLUS_TIER,
    WATCH_TIER,
    classify_signal,
    evaluate_trade_readiness,
)
from paper_signal_bot.storage import JsonStore
from paper_signal_bot.web import settle_due_positions


class QualityGateTests(unittest.TestCase):
    def _passing_state(self, at: datetime) -> dict:
        started = at - timedelta(days=100)
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
        signals = []
        for index in range(160):
            win = index % 10 < 7
            signals.append(
                {
                    "signal_id": f"signal-{index}",
                    "symbol": symbols[index % len(symbols)],
                    "timeframe": "1h" if index % 2 == 0 else "4h",
                    "side": "LONG" if index % 2 == 0 else "SHORT",
                    "status": "PAPER_CLOSED",
                    "delivery_status": "SENT",
                    "actual_exit_time_ms": int((started + timedelta(hours=index * 12)).timestamp() * 1000),
                    "net_return_12bps": 0.01 if win else -0.01,
                    "net_return_20bps": 0.0092 if win else -0.0108,
                }
            )
        return {
            "created_utc": started.isoformat(),
            "forward_evidence_started_utc": started.isoformat(),
            "scan_count": 144_001,
            "durable_state_configured": True,
            "signals": signals,
        }

    def test_gate_stays_watch_without_research_approval(self) -> None:
        at = datetime(2026, 12, 20, tzinfo=timezone.utc)
        result = evaluate_trade_readiness(
            self._passing_state(at),
            at_ms=int(at.timestamp() * 1000),
        )
        self.assertFalse(result["trade_a_plus_eligible"])
        self.assertEqual(classify_signal(result), WATCH_TIER)
        self.assertIn("Nghiên cứu forward độc lập chưa được phê duyệt.", result["failed_reasons_vi"])

    def test_gate_promotes_only_when_all_forward_rules_pass(self) -> None:
        at = datetime(2026, 12, 20, tzinfo=timezone.utc)
        result = evaluate_trade_readiness(
            self._passing_state(at),
            at_ms=int(at.timestamp() * 1000),
            research_approved=True,
        )
        self.assertTrue(result["trade_a_plus_eligible"])
        self.assertEqual(classify_signal(result), TRADE_A_PLUS_TIER)
        self.assertGreaterEqual(result["metrics"]["win_rate"], 0.70)
        self.assertGreaterEqual(result["metrics"]["probability_positive"], 0.80)

    def test_drawdown_applies_signal_risk_fraction(self) -> None:
        at = datetime(2026, 12, 20, tzinfo=timezone.utc)
        started = at - timedelta(days=2)
        state = {
            "created_utc": started.isoformat(),
            "forward_evidence_started_utc": started.isoformat(),
            "scan_count": 1,
            "signals": [
                {
                    "signal_id": "loss",
                    "symbol": "BTCUSDT",
                    "timeframe": "4h",
                    "side": "LONG",
                    "status": "PAPER_CLOSED",
                    "actual_exit_time_ms": int(started.timestamp() * 1000),
                    "net_return_12bps": -0.20,
                    "net_return_20bps": -0.21,
                    "risk_fraction": 0.25,
                }
            ],
        }
        result = evaluate_trade_readiness(state, at_ms=int(at.timestamp() * 1000))
        self.assertAlmostEqual(result["metrics"]["max_drawdown_pct"], -5.0)
        self.assertTrue(result["metrics"]["risk_weighting_applied"])

    def test_settlement_uses_planned_exit_open_and_cost_stress(self) -> None:
        exit_time_ms = 2_000_000
        position = {
            "signal_id": "s-1",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "side": "LONG",
            "entry_price": 100.0,
            "planned_exit_time_ms": exit_time_ms,
        }
        row = [exit_time_ms, "102", "103", "101", "102.5", "1", exit_time_ms + 3_599_999, "100", 1, "0.5", "50"]
        closed = settle_due_positions(
            [position],
            {("BTCUSDT", "1h"): [row]},
            at_ms=exit_time_ms + 60_000,
        )
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["status"], "PAPER_CLOSED")
        self.assertAlmostEqual(closed[0]["gross_return"], 0.02)
        self.assertAlmostEqual(closed[0]["net_return_12bps"], 0.0188)
        self.assertAlmostEqual(closed[0]["net_return_20bps"], 0.018)

    def test_store_merges_closed_outcome_and_releases_position(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JsonStore(Path(tmp) / "state.json")
            position = {
                "signal_id": "s-1",
                "asset": "BTC",
                "status": "PAPER_OPEN_PLANNED",
            }
            store.save(
                {
                    "created_utc": "2026-09-10T00:00:00+00:00",
                    "scan_count": 1,
                    "signals": [dict(position)],
                    "active_positions": [dict(position)],
                    "errors": [],
                }
            )
            closed = {**position, "status": "PAPER_CLOSED", "net_return_12bps": 0.01}
            state = store.record_scan(
                {"trade_readiness": {"status": "WATCH_ONLY"}},
                [],
                closed_signals=[closed],
            )
            self.assertEqual(state["signals"][0]["status"], "PAPER_CLOSED")
            self.assertEqual(state["active_positions"], [])
            self.assertEqual(state["trade_readiness"]["status"], "WATCH_ONLY")


if __name__ == "__main__":
    unittest.main()
