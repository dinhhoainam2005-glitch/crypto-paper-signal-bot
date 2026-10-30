from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research import r63a_continuous_multivenue_collector as r63a
from research import r63c_positioning_liquidation_collector as r63c
from research import r63d_positioning_collector_audit as r63d


class PositioningCollectorAuditTests(unittest.TestCase):
    def build_fixture(self, root: Path) -> None:
        store = r63c.PositioningStore(root / "R63C_POSITIONING.sqlite3")
        rows = []
        for venue in r63d.EXPECTED_VENUES:
            for symbol in r63a.DEFAULT_SYMBOLS:
                for minute in (60_000, 120_000):
                    cell = r63c.MinutePositioning(venue, symbol, minute)
                    cell.add(r63c.ContextEvent(venue, symbol, minute + 1_000, minute + 1_010, 100, 200, 0.001))
                    rows.append(cell.row())
        store.write(rows)
        store.close()
        (root / "R63C_STATUS.json").write_text(json.dumps({
            "version": r63c.VERSION,
            "streams": {venue: "STOPPED" for venue in r63d.EXPECTED_VENUES},
            "late_events": 0,
            "duplicate_liquidations": 0,
            "errors": [],
        }), encoding="utf-8")

    def test_complete_fixture_passes_without_liquidations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_fixture(root)
            report = r63d.audit(root)
            self.assertTrue(report["pass"])
            self.assertEqual(report["common_complete_minutes"], 2)

    def test_missing_context_cell_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_fixture(root)
            connection = sqlite3.connect(root / "R63C_POSITIONING.sqlite3")
            connection.execute("DELETE FROM minute_positioning WHERE venue='hyperliquid' AND symbol='SOLUSDT'")
            connection.commit()
            connection.close()
            report = r63d.audit(root)
            self.assertFalse(report["pass"])
            self.assertTrue(any("hyperliquid:SOLUSDT" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
