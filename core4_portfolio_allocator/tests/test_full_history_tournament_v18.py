from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit_full_history_tournament_v18 import dominates, pareto_names


def candidate(name: str, values: tuple[float, ...]) -> dict:
    keys = (
        "trades_per_week", "win_rate", "profit_factor", "mean_r",
        "total_return", "sharpe", "max_drawdown",
    )
    return {"name": name, "metrics": {"full": dict(zip(keys, values))}}


class FullHistoryTournamentTests(unittest.TestCase):
    def test_pareto_removes_strictly_dominated_candidate(self) -> None:
        strong = candidate("strong", (1.0, 0.6, 1.5, 0.2, 0.1, 1.1, -0.05))
        weak = candidate("weak", (0.9, 0.5, 1.4, 0.1, 0.08, 1.0, -0.06))
        tradeoff = candidate("tradeoff", (1.2, 0.5, 1.3, 0.1, 0.07, 0.9, -0.07))
        self.assertTrue(dominates(strong, weak))
        self.assertEqual(pareto_names([strong, weak, tradeoff]), ["strong", "tradeoff"])


if __name__ == "__main__":
    unittest.main()
