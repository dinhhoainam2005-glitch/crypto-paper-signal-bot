from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import r53a_tardis_microstructure_probe as r53a  # noqa: E402


def write_gzip_csv(path: Path, text: str) -> None:
    with gzip.open(path, "wt", newline="") as handle:
        handle.write(text)


class R53ResearchTests(unittest.TestCase):
    def test_sample_url_is_core4_and_first_day_only(self) -> None:
        self.assertEqual(
            r53a.dataset_url("trades", "2021-09-01", "BTCUSDT"),
            "https://datasets.tardis.dev/v1/binance-futures/trades/"
            "2021/09/01/BTCUSDT.csv.gz",
        )
        with self.assertRaises(ValueError):
            r53a.dataset_url("trades", "2021-09-02", "BTCUSDT")
        with self.assertRaises(ValueError):
            r53a.dataset_url("trades", "2021-09-01", "XRPUSDT")

    def test_trade_features_use_local_arrival_hour(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.csv.gz"
            write_gzip_csv(
                path,
                "local_timestamp,side,price,amount\n"
                "1630454399000000,buy,100,2\n"
                "1630454401000000,sell,101,1\n",
            )
            result = r53a.aggregate_trades(path)
        self.assertEqual(len(result), 2)
        first = result.iloc[0]
        self.assertEqual(first["buy_notional"], 200.0)
        self.assertEqual(first["sell_notional"], 0.0)

    def test_liquidation_side_semantics_are_not_reversed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "liquidations.csv.gz"
            write_gzip_csv(
                path,
                "local_timestamp,side,price,amount\n"
                "1630454401000000,sell,100,2\n"
                "1630454402000000,buy,100,1\n",
            )
            result = r53a.aggregate_liquidations(path)
        row = result.iloc[0]
        self.assertEqual(row["long_liquidated_notional"], 200.0)
        self.assertEqual(row["short_liquidated_notional"], 100.0)
        self.assertAlmostEqual(row["liquidation_pressure"], -1.0 / 3.0)

    def test_derivative_features_keep_last_known_hour_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ticker.csv.gz"
            write_gzip_csv(
                path,
                "local_timestamp,funding_rate,open_interest,last_price,index_price,mark_price\n"
                "1630454401000000,0.0001,100,100,100,100.1\n"
                "1630457999000000,0.0002,110,101,101,101.2\n",
            )
            result = r53a.aggregate_derivative_ticker(path)
        row = result.iloc[0]
        self.assertEqual(row["oi_open"], 100.0)
        self.assertEqual(row["oi_close"], 110.0)
        self.assertAlmostEqual(row["oi_change_pct"], 0.10)
        self.assertEqual(row["funding_rate"], 0.0002)

    def test_combined_features_are_available_only_after_hour_close(self) -> None:
        index = pd.DatetimeIndex(["2021-09-01 00:00:00+00:00"], name="hour_open")
        frame = pd.DataFrame({"value": [1.0]}, index=index)
        result = r53a.combine_features({"test": frame})
        self.assertEqual(
            result.iloc[0]["available_at"],
            pd.Timestamp("2021-09-01 01:00:00+00:00"),
        )


if __name__ == "__main__":
    unittest.main()
