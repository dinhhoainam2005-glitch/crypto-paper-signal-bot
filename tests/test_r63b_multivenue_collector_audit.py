from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research import r63a_continuous_multivenue_collector as r63a
from research import r63b_multivenue_collector_audit as r63b


class MultivenueCollectorAuditTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> None:
        store = r63a.SQLiteMinuteStore(root / "R63A_FLOW.sqlite3")
        rows = []
        for venue in r63b.EXPECTED_VENUES:
            for symbol in r63a.DEFAULT_SYMBOLS:
                for minute in (60_000, 120_000):
                    accumulator = r63a.MinuteAccumulator(venue, symbol, minute, 1)
                    accumulator.add_trade(
                        r63a.TradeEvent(venue, symbol, minute + 1_000, minute + 1_010, "BUY", 100, 1, "id")
                    )
                    accumulator.add_quote(
                        r63a.QuoteEvent(venue, symbol, minute + 2_000, minute + 2_010, 99, 2, 101, 3)
                    )
                    rows.append(accumulator.row())
        store.write(rows)
        store.close()
        (root / "R63A_STATUS.json").write_text(
            json.dumps({
                "version": r63a.VERSION,
                "streams": {venue: "STOPPED" for venue in r63b.EXPECTED_VENUES},
                "late_events": 0,
                "duplicate_trades": 0,
                "out_of_order_events": 0,
            }),
            encoding="utf-8",
        )

    def test_complete_fixture_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_fixture(root)
            report = r63b.audit(root)
            self.assertTrue(report["pass"])
            self.assertEqual(report["covered_cells"], 12)
            self.assertEqual(report["common_complete_minutes"], 2)

    def test_missing_cell_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_fixture(root)
            connection = sqlite3.connect(root / "R63A_FLOW.sqlite3")
            connection.execute("DELETE FROM minute_flow WHERE venue='bybit' AND symbol='BTCUSDT'")
            connection.commit()
            connection.close()
            report = r63b.audit(root)
            self.assertFalse(report["pass"])
            self.assertTrue(any("bybit:BTCUSDT" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
