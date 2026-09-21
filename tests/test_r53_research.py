from __future__ import annotations

import gzip
import math
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
import r53b_tardis_liquidation_samples as r53b  # noqa: E402
import r53c_liquidation_event_study as r53c  # noqa: E402
import r53d_liquidation_book_confirmation as r53d  # noqa: E402
import r54a_deribit_dvol_audit as r54a  # noqa: E402
import r54b_dvol_regime_interaction as r54b  # noqa: E402


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

    def test_liquidation_sample_tasks_respect_symbol_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks = r53b.build_tasks(
                Path(directory),
                "2020-01",
                "2020-04",
                ["BTCUSDT", "BNBUSDT", "SOLUSDT"],
            )
        by_symbol = {
            symbol: [task.date for task in tasks if task.symbol == symbol]
            for symbol in ("BTCUSDT", "BNBUSDT", "SOLUSDT")
        }
        self.assertEqual(len(by_symbol["BTCUSDT"]), 4)
        self.assertEqual(by_symbol["BNBUSDT"], ["2020-03-01", "2020-04-01"])
        self.assertEqual(by_symbol["SOLUSDT"], [])

    def test_liquidation_sample_validation_requires_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.csv.gz"
            invalid = Path(directory) / "invalid.csv.gz"
            write_gzip_csv(
                valid,
                "exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n",
            )
            write_gzip_csv(invalid, "timestamp,price\n")
            self.assertEqual(r53b.validate_gzip(valid), (True, None))
            ok, error = r53b.validate_gzip(invalid)
            self.assertFalse(ok)
            self.assertIn("missing columns", str(error))

    def test_r53c_overlap_filter_is_per_symbol_and_hypothesis(self) -> None:
        frame = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT", "ETHUSDT"],
                "hypothesis": ["A", "A", "B", "A"],
                "entry_time": pd.to_datetime(
                    ["2025-01-01 00:00", "2025-01-01 06:00", "2025-01-01 06:00", "2025-01-01 06:00"],
                    utc=True,
                ),
                "exit_time": pd.to_datetime(
                    ["2025-01-01 12:00", "2025-01-01 18:00", "2025-01-01 18:00", "2025-01-01 18:00"],
                    utc=True,
                ),
            }
        )
        result = r53c.remove_overlaps(frame)
        self.assertEqual(len(result), 3)

    def test_r53c_profit_factor_handles_no_losses(self) -> None:
        self.assertTrue(math.isinf(r53c.profit_factor(pd.Series([0.01, 0.02]))))
        self.assertEqual(r53c.profit_factor(pd.Series([-0.01, -0.02])), 0.0)

    def test_r53c_cost_constants_are_exact_bps(self) -> None:
        empty = pd.DataFrame(
            {
                "hypothesis": ["TEST"] * 3,
                "split": ["development", "validation", "locked_diagnostic"],
                "trades": [0, 0, 0],
                "profit_factor_20bps": [0.0, 0.0, 0.0],
            }
        )
        verdict = r53c.gate(empty)
        self.assertEqual(verdict["base_cost_bps"], 12)
        self.assertEqual(verdict["stress_cost_bps"], 20)

    def test_r53d_confirmation_masks_match_economic_direction(self) -> None:
        frame = pd.DataFrame(
            {
                "hypothesis": [
                    "SHORT_DELEVERAGING_CONTINUATION",
                    "LONG_SQUEEZE_CONTINUATION",
                    "LONG_LIQUIDATION_REVERSAL",
                    "SHORT_SQUEEZE_REVERSAL",
                ],
                "oi_value_change_1": [-0.1] * 4,
                "book_bid_notional_p1_change": [-0.1, 0.0, 0.1, 0.0],
                "book_ask_notional_p1_change": [0.0, -0.1, 0.0, 0.1],
                "book_imbalance_p1_last": [-0.1, 0.1, 0.1, -0.1],
                "book_imbalance_p1_change": [-0.1, 0.1, 0.1, -0.1],
                "confirmation_quality_ok": [True] * 4,
            }
        )
        self.assertEqual(r53d.confirmation_mask(frame).tolist(), [True] * 4)

    def test_r54a_api_url_is_public_daily_dvol(self) -> None:
        url = r54a.api_url("BTC", 1, 2)
        self.assertIn("public/get_volatility_index_data", url)
        self.assertIn("currency=BTC", url)
        self.assertIn("resolution=1D", url)
        with self.assertRaises(ValueError):
            r54a.api_url("SOL", 1, 2)

    def test_r54a_daily_data_is_available_next_day(self) -> None:
        frame = r54a.rows_to_frame(
            [[1616544000000, 80.0, 85.0, 75.0, 82.0]], "BTC"
        )
        self.assertEqual(
            frame.iloc[0]["available_at"],
            frame.iloc[0]["bar_open"] + pd.Timedelta(days=1),
        )

    def test_r54a_pagination_deduplicates_boundary_rows(self) -> None:
        pages = iter(
            [
                {
                    "data": [
                        [1616630400000, 81.0, 86.0, 76.0, 83.0],
                        [1616716800000, 82.0, 87.0, 77.0, 84.0],
                    ],
                    "continuation": 1616630399999,
                },
                {
                    "data": [
                        [1616544000000, 80.0, 85.0, 75.0, 82.0],
                        [1616630400000, 81.0, 86.0, 76.0, 83.0],
                    ]
                },
            ]
        )

        def fake_fetch(currency: str, start_ms: int, end_ms: int) -> dict:
            self.assertEqual(currency, "BTC")
            self.assertLessEqual(start_ms, end_ms)
            return next(pages)

        frame = r54a.download_currency(
            "BTC",
            pd.Timestamp("2021-03-24", tz="UTC"),
            pd.Timestamp("2021-03-26", tz="UTC"),
            fetcher=fake_fetch,
            sleep_seconds=0.0,
        )
        self.assertEqual(len(frame), 3)
        self.assertFalse(frame["bar_open"].duplicated().any())

    def test_r54b_assigns_only_available_dvol(self) -> None:
        trades = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "BTCUSDT"],
                "signal_time": pd.to_datetime(
                    ["2021-03-25 00:00", "2021-03-25 23:00"], utc=True
                ),
                "entry_time": pd.to_datetime(
                    ["2021-03-25 00:00", "2021-03-25 23:00"], utc=True
                ),
                "exit_time": pd.to_datetime(
                    ["2021-03-25 01:00", "2021-03-26 00:00"], utc=True
                ),
                "side": ["LONG", "SHORT"],
            }
        )
        dvol = pd.DataFrame(
            {
                "currency": ["BTC", "BTC"],
                "bar_open": pd.to_datetime(["2021-03-24", "2021-03-25"], utc=True),
                "available_at": pd.to_datetime(["2021-03-25", "2021-03-26"], utc=True),
                "close": [80.0, 90.0],
                "dvol_median_60d": [70.0, 75.0],
                "dvol_change_5d": [0.1, 0.2],
                "dvol_currency": ["BTC", "BTC"],
            }
        )
        joined = r54b.attach_causal_dvol(trades, dvol)
        self.assertEqual(joined["dvol_close"].tolist(), [80.0, 80.0])
        self.assertTrue((joined["available_at"] <= joined["signal_time"]).all())

    def test_r54b_fixed_masks_are_direction_agnostic(self) -> None:
        frame = pd.DataFrame(
            {
                "side": ["LONG", "SHORT", "LONG", "SHORT"],
                "dvol_close": [80.0, 80.0, 60.0, 60.0],
                "dvol_median_60d": [70.0, 70.0, 70.0, 70.0],
                "dvol_change_5d": [0.1, 0.1, -0.1, -0.1],
            }
        )
        breakout = r54b.apply_fixed_mask(frame, "BREAKOUT_CONTINUATION")
        reversal = r54b.apply_fixed_mask(frame, "MEAN_REVERSION")
        self.assertEqual(breakout["side"].tolist(), ["LONG", "SHORT"])
        self.assertEqual(reversal["side"].tolist(), ["LONG", "SHORT"])


if __name__ == "__main__":
    unittest.main()
