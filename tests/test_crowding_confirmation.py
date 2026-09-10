from __future__ import annotations

import math
import unittest

from paper_signal_bot.crowding_confirmation import (
    STABLE_CANDIDATES,
    crowding_forward_summary,
    evaluate_crowding_confirmation,
    required_history_start_ms,
)


HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS


def crowded_histories(entry_time_ms: int) -> tuple[list[dict], list[list]]:
    start = required_history_start_ms(entry_time_ms)
    premium: list[list] = []
    open_time = start
    index = 0
    while open_time < entry_time_ms:
        value = 0.0002 * math.sin(index / 17.0)
        if open_time >= entry_time_ms - 72 * HOUR_MS:
            value += 0.01
        premium.append([open_time, "0", "0", "0", str(value)])
        open_time += HOUR_MS
        index += 1

    funding: list[dict] = []
    event_time = start
    index = 0
    while event_time <= entry_time_ms:
        value = 0.00005 * math.sin(index / 5.0)
        if event_time >= entry_time_ms - 64 * HOUR_MS:
            value += 0.005
        funding.append({"fundingTime": event_time, "fundingRate": str(value)})
        event_time += 8 * HOUR_MS
        index += 1
    return funding, premium


class CrowdingConfirmationTests(unittest.TestCase):
    def test_directional_consensus_flags_crowded_long_and_confirms_short(self) -> None:
        entry_time_ms = 200 * DAY_MS
        funding, premium = crowded_histories(entry_time_ms)

        long_result = evaluate_crowding_confirmation(
            symbol="BTCUSDT",
            timeframe="4h",
            side="LONG",
            entry_time_ms=entry_time_ms,
            funding_rows=funding,
            premium_rows=premium,
        )
        short_result = evaluate_crowding_confirmation(
            symbol="BTCUSDT",
            timeframe="4h",
            side="SHORT",
            entry_time_ms=entry_time_ms,
            funding_rows=funding,
            premium_rows=premium,
        )

        self.assertEqual(long_result["candidate_available"], len(STABLE_CANDIDATES))
        self.assertEqual(long_result["status"], "CROWDING_RISK")
        self.assertLess(long_result["consensus"], 0.5)
        self.assertEqual(short_result["status"], "CONFIRMED")
        self.assertGreaterEqual(short_result["consensus"], 0.5)

    def test_insufficient_history_never_defaults_to_confirmation(self) -> None:
        result = evaluate_crowding_confirmation(
            symbol="ETHUSDT",
            timeframe="1h",
            side="LONG",
            entry_time_ms=10 * DAY_MS,
            funding_rows=[],
            premium_rows=[],
        )

        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["data_state"], "INSUFFICIENT_HISTORY")
        self.assertNotIn("confirmed", result)

    def test_forward_summary_only_scores_closed_confirmed_signals(self) -> None:
        signals = [
            {
                "status": "PAPER_CLOSED",
                "net_return_12bps": 0.02,
                "crowding_confirmation": {"status": "CONFIRMED"},
            },
            {
                "status": "PAPER_CLOSED",
                "net_return_12bps": -0.01,
                "crowding_confirmation": {"status": "CONFIRMED"},
            },
            {
                "status": "PAPER_CLOSED",
                "net_return_12bps": 0.50,
                "crowding_confirmation": {"status": "CROWDING_RISK"},
            },
        ]

        summary = crowding_forward_summary(signals)

        self.assertEqual(summary["confirmed_signals"], 2)
        self.assertEqual(summary["crowding_risk_signals"], 1)
        self.assertEqual(summary["confirmed_closed_trades"], 2)
        self.assertEqual(summary["confirmed_win_rate"], 0.5)
        self.assertEqual(summary["confirmed_profit_factor_12bps"], 2.0)
        self.assertFalse(summary["blocks_base_signals"])
        self.assertFalse(summary["real_money_authorized"])


if __name__ == "__main__":
    unittest.main()
