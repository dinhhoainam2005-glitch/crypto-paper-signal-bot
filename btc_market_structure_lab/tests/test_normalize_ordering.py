import json
import tempfile
import unittest
from pathlib import Path

from market_structure.normalize import normalize_capture


class NormalizeOrderingTests(unittest.TestCase):
    def test_capture_is_globally_ordered_by_receipt_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            records = {
                "a.jsonl": [
                    {"venue": "binance", "receipt_time_ns": 30, "payload": {"e": "aggTrade", "E": 3, "T": 3, "s": "BTCUSDT", "a": 3, "f": 3, "l": 3, "p": "103", "q": "1", "m": False}},
                ],
                "b.jsonl": [
                    {"venue": "binance", "receipt_time_ns": 20, "payload": {"e": "aggTrade", "E": 2, "T": 2, "s": "BTCUSDT", "a": 2, "f": 2, "l": 2, "p": "102", "q": "1", "m": False}},
                ],
            }
            for name, rows in records.items():
                (run_dir / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            normalize_capture(run_dir)
            output = [json.loads(line) for line in (run_dir / "normalized.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["receipt_time_ns"] for row in output], [20, 30])


if __name__ == "__main__":
    unittest.main()
