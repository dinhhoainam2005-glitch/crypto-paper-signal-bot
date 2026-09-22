from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import r55a_kraken_futures_audit as r55a  # noqa: E402
import r55b_cross_venue_lead_lag as r55b  # noqa: E402


class R55ResearchTests(unittest.TestCase):
    def test_kraken_url_is_public_hourly_trade_candles(self) -> None:
        url = r55a.api_url(
            "PI_XBTUSD",
            pd.Timestamp("2021-01-01", tz="UTC"),
            pd.Timestamp("2021-01-02", tz="UTC"),
        )
        self.assertIn("/trade/PI_XBTUSD/1h", url)
        self.assertIn("from=", url)
        self.assertIn("to=", url)
        with self.assertRaises(ValueError):
            r55a.api_url(
                "UNKNOWN",
                pd.Timestamp("2021-01-01", tz="UTC"),
                pd.Timestamp("2021-01-02", tz="UTC"),
            )

    def test_kraken_rows_are_available_only_at_hour_close(self) -> None:
        frame = r55a.rows_to_frame(
            [
                {
                    "time": 1609459200000,
                    "open": "100",
                    "high": "110",
                    "low": "90",
                    "close": "105",
                    "volume": "20",
                }
            ],
            "BTCUSDT",
            "PI_XBTUSD",
        )
        self.assertEqual(
            frame.iloc[0]["available_at"],
            frame.iloc[0]["bar_open"] + pd.Timedelta(hours=1),
        )

    def test_window_download_deduplicates_boundaries(self) -> None:
        row = {
            "time": 1609459200000,
            "open": "100",
            "high": "110",
            "low": "90",
            "close": "105",
            "volume": "20",
        }

        def fake_fetch(contract: str, start: pd.Timestamp, end: pd.Timestamp) -> dict:
            self.assertEqual(contract, "PI_XBTUSD")
            return {"candles": [row], "more_candles": False}

        frame = r55a.download_symbol(
            "BTCUSDT",
            pd.Timestamp("2021-01-01", tz="UTC"),
            pd.Timestamp("2021-01-02", tz="UTC"),
            window_days=1,
            sleep_seconds=0.0,
            fetcher=fake_fetch,
        )
        self.assertEqual(len(frame), 1)
        self.assertFalse(frame["bar_open"].duplicated().any())

    def test_divergence_z_uses_only_prior_observations(self) -> None:
        count = r55b.LOOKBACK + 2
        kraken = pd.Series([100.0] * (count - 1) + [110.0])
        binance = pd.Series([100.0] * count)
        frame = pd.DataFrame({"kraken_close": kraken, "binance_close": binance})
        result = r55b.add_divergence_features(frame)
        self.assertTrue(pd.isna(result.loc[r55b.LOOKBACK, "divergence_z"]))
        self.assertTrue(pd.isna(result.loc[count - 1, "divergence_z"]))

    def test_fixed_signal_mask_is_symmetric(self) -> None:
        frame = pd.DataFrame(
            {
                "divergence_z": [2.1, -2.1, 2.1, -2.1],
                "kraken_return_1h": [0.01, -0.01, -0.01, 0.01],
            }
        )
        long_mask, short_mask = r55b.signal_mask(frame)
        self.assertEqual(long_mask.tolist(), [True, False, False, False])
        self.assertEqual(short_mask.tolist(), [False, True, False, False])


if __name__ == "__main__":
    unittest.main()
