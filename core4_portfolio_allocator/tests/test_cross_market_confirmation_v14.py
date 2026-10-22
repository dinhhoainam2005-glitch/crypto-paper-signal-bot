from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_execution_timing_v7 import exact_simulate, has_concurrent_confirmation


class CrossMarketConfirmationTests(unittest.TestCase):
    def test_requires_two_distinct_symbols(self) -> None:
        single = [("BTCUSDT", object(), 0.1)]
        duplicate = single + [("BTCUSDT", object(), 0.2)]
        confirmed = single + [("ETHUSDT", object(), 0.1)]
        self.assertFalse(has_concurrent_confirmation(single, 2))
        self.assertFalse(has_concurrent_confirmation(duplicate, 2))
        self.assertTrue(has_concurrent_confirmation(confirmed, 2))

    def test_rejects_invalid_tradable_subset_before_loading_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "tradable_symbols"):
            exact_simulate({}, {}, {}, None, None, tradable_symbols=("DOGEUSDT",))


if __name__ == "__main__":
    unittest.main()
