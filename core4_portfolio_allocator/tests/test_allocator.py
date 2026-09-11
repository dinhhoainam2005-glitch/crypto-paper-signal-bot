from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_allocator as allocator
import backtest_risk_defined as risk_defined
import backtest_walkforward_v3 as walkforward
import backtest_symmetric_v4 as symmetric
import backtest_breakout_v5 as breakout
import backtest_sleeve_council_v6 as sleeve_council
import backtest_beta_regime_v7 as beta_regime
import audit_beta_regime_v7 as beta_audit
import fetch_binance_daily as fetcher
import fetch_binance_futures as futures_fetcher
import futures_data
from signal_contract import TradePlan


class AllocatorTests(unittest.TestCase):
    def test_month_sequence_crosses_year(self) -> None:
        self.assertEqual(
            fetcher.month_sequence("2024-11", "2025-02"),
            ["2024-11", "2024-12", "2025-01", "2025-02"],
        )

    def test_exchange_time_accepts_mixed_units(self) -> None:
        parsed = allocator.parse_exchange_time(
            pd.Series([1_700_000_000_000, 1_700_086_400_000_000])
        )
        self.assertEqual(parsed.iloc[0], pd.Timestamp("2023-11-14 22:13:20", tz="UTC"))
        self.assertEqual(parsed.iloc[1], pd.Timestamp("2023-11-15 22:13:20", tz="UTC"))

    def test_capped_inverse_vol_respects_cap_and_cash(self) -> None:
        result = allocator.capped_inverse_vol(
            pd.Series({"BTC": 0.20, "ETH": 0.40, "SOL": np.nan, "BNB": np.nan})
        )
        self.assertLessEqual(float(result.max()), allocator.MAX_ASSET_WEIGHT)
        self.assertAlmostEqual(float(result.sum()), 0.80)

    def test_max_drawdown(self) -> None:
        returns = pd.Series([0.10, -0.10, -0.10, 0.05])
        expected = (1.10 * 0.90 * 0.90) / 1.10 - 1.0
        self.assertAlmostEqual(allocator.max_drawdown(returns), expected)

    def test_trade_plan_requires_consistent_sl_tp_and_holding_time(self) -> None:
        now = datetime(2026, 9, 11, tzinfo=timezone.utc)
        plan = TradePlan(
            symbol="BTCUSDT",
            side="LONG",
            timeframe="1d",
            generated_at_utc=now,
            entry=100.0,
            stop_loss=95.0,
            take_profit_1=105.0,
            take_profit_2=110.0,
            take_profit_3=115.0,
            invalid_after_utc=now + timedelta(hours=24),
            max_holding_hours=168,
            review_interval_hours=24,
            capital_at_risk_fraction=0.01,
            early_exit_condition="Portfolio risk gate turns off",
        )
        self.assertEqual(plan.reward_to_risk(), (1.0, 2.0, 3.0))
        self.assertEqual(plan.as_payload()["max_holding_hours"], 168)

    def test_trade_plan_blocks_invalid_long_targets(self) -> None:
        now = datetime(2026, 9, 11, tzinfo=timezone.utc)
        plan = TradePlan(
            symbol="ETHUSDT",
            side="LONG",
            timeframe="1d",
            generated_at_utc=now,
            entry=100.0,
            stop_loss=101.0,
            take_profit_1=105.0,
            take_profit_2=110.0,
            take_profit_3=115.0,
            invalid_after_utc=now + timedelta(hours=24),
            max_holding_hours=168,
            review_interval_hours=24,
            capital_at_risk_fraction=0.01,
            early_exit_condition="Portfolio risk gate turns off",
        )
        with self.assertRaises(ValueError):
            plan.validate()

    def test_v2_plan_has_fixed_r_targets_and_holding_time(self) -> None:
        generated = pd.Timestamp("2026-09-14", tz="UTC")
        plan = risk_defined.build_plan("BTCUSDT", generated, entry=100.0, atr=2.0)
        self.assertEqual(plan.stop_loss, 96.0)
        self.assertEqual((plan.take_profit_1, plan.take_profit_2, plan.take_profit_3), (104.0, 108.0, 112.0))
        self.assertEqual(plan.reward_to_risk(), (1.0, 2.0, 3.0))
        self.assertEqual(plan.max_holding_hours, 28 * 24)

    def test_v2_same_day_ambiguity_uses_stop_first(self) -> None:
        plan = risk_defined.build_plan(
            "ETHUSDT", pd.Timestamp("2026-09-14", tz="UTC"), entry=100.0, atr=2.0
        )
        position = risk_defined.Position(
            plan=plan,
            initial_quantity=1.0,
            remaining_quantity=1.0,
            initial_notional=100.0,
            initial_risk_cash=4.0,
            entry_fee=0.2,
            current_stop=96.0,
        )
        cash, _ = risk_defined.process_intraday(
            position,
            pd.Timestamp("2026-09-15", tz="UTC"),
            low=95.0,
            high=105.0,
            cost_rate=0.002,
        )
        self.assertAlmostEqual(cash, 96.0 * (1.0 - 0.002))
        self.assertEqual(position.exits[0]["reason"], "STOP")
        self.assertEqual(position.remaining_quantity, 0.0)

    def test_v2_tp_schedule_moves_stop(self) -> None:
        plan = risk_defined.build_plan(
            "BNBUSDT", pd.Timestamp("2026-09-14", tz="UTC"), entry=100.0, atr=2.0
        )
        position = risk_defined.Position(
            plan=plan,
            initial_quantity=1.0,
            remaining_quantity=1.0,
            initial_notional=100.0,
            initial_risk_cash=4.0,
            entry_fee=0.2,
            current_stop=96.0,
        )
        risk_defined.process_intraday(
            position,
            pd.Timestamp("2026-09-15", tz="UTC"),
            low=99.0,
            high=109.0,
            cost_rate=0.002,
        )
        self.assertAlmostEqual(position.remaining_quantity, 0.50)
        self.assertEqual(position.next_target_index, 2)
        self.assertEqual(position.current_stop, plan.take_profit_1)

    def test_v3_candidate_set_is_finite_and_frozen(self) -> None:
        self.assertEqual(walkforward.CANDIDATES, {"ALL": None, "TOP1": 1, "TOP2": 2})
        self.assertEqual(walkforward.TRAINING_YEARS, 3)

    def test_v3_bootstrap_is_deterministic(self) -> None:
        values = pd.Series([1.0, -0.5, 0.25, 0.75])
        first = walkforward.bootstrap_mean_r_lower_bound(values)
        second = walkforward.bootstrap_mean_r_lower_bound(values)
        self.assertEqual(first, second)

    def test_v4_futures_urls_are_official_and_dataset_specific(self) -> None:
        kline = futures_fetcher.FuturesTask("klines", "BTCUSDT", "2024-01")
        funding = futures_fetcher.FuturesTask("fundingRate", "BTCUSDT", "2024-01")
        self.assertEqual(
            kline.url,
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            "BTCUSDT/1d/BTCUSDT-1d-2024-01.zip",
        )
        self.assertEqual(
            funding.url,
            "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
            "BTCUSDT/BTCUSDT-fundingRate-2024-01.zip",
        )

    def test_v4_verified_futures_and_funding_loaders(self) -> None:
        market = futures_data.load_futures_daily("BTCUSDT")
        funding = futures_data.load_funding_events("BTCUSDT")
        self.assertEqual(market.index.min(), pd.Timestamp("2020-01-01", tz="UTC"))
        self.assertEqual(funding["event_time"].min(), pd.Timestamp("2020-01-01", tz="UTC"))
        self.assertGreater(len(market), 2400)
        self.assertGreater(len(funding), 7000)
        daily = futures_data.daily_funding_rates(funding)
        self.assertTrue(daily.index.is_monotonic_increasing)
        self.assertTrue(np.isfinite(daily.to_numpy()).all())

    def test_hourly_futures_url_is_official(self) -> None:
        from fetch_binance_futures_hourly import HourlyTask

        task = HourlyTask("klines", "BTCUSDT", "2024-01")
        self.assertEqual(
            task.url,
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            "BTCUSDT/1h/BTCUSDT-1h-2024-01.zip",
        )

    def test_verified_hourly_loader(self) -> None:
        hourly = futures_data.load_futures_hourly("BTCUSDT")
        self.assertEqual(hourly.index.min(), pd.Timestamp("2020-01-01", tz="UTC"))
        self.assertGreater(len(hourly), 50_000)
        self.assertTrue(hourly.index.is_monotonic_increasing)
        self.assertFalse(hourly.index.has_duplicates)

    def test_sol_gap_repair_urls_are_official(self) -> None:
        from repair_sol_hourly_gaps import FiveMinuteTask, MarkPriceTask

        task = FiveMinuteTask("klines", "SOLUSDT", "2022-02")
        self.assertEqual(
            task.url,
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            "SOLUSDT/5m/SOLUSDT-5m-2022-02.zip",
        )
        mark = MarkPriceTask("markPriceKlines", "SOLUSDT", "2022-02")
        self.assertEqual(
            mark.url,
            "https://data.binance.vision/data/futures/um/monthly/markPriceKlines/"
            "SOLUSDT/1h/SOLUSDT-1h-2022-02.zip",
        )

    def test_gap_merge_preserves_native_source_priority(self) -> None:
        timestamp = pd.Timestamp("2022-02-01", tz="UTC")
        native = pd.DataFrame({"close": [100.0]}, index=[timestamp])
        fine = pd.DataFrame({"close": [99.0]}, index=[timestamp])
        mark = pd.DataFrame({"close": [98.0]}, index=[timestamp])
        merged = pd.concat([native, fine, mark])
        merged = merged.loc[~merged.index.duplicated(keep="first")].sort_index()
        self.assertEqual(float(merged.loc[timestamp, "close"]), 100.0)

    def test_v4_long_and_short_plans_are_symmetric(self) -> None:
        generated = pd.Timestamp("2026-09-14", tz="UTC")
        long_plan = symmetric.build_plan("BTCUSDT", "LONG", generated, 100.0, 2.0)
        short_plan = symmetric.build_plan("BTCUSDT", "SHORT", generated, 100.0, 2.0)
        self.assertEqual((long_plan.stop_loss, long_plan.take_profit_3), (96.0, 112.0))
        self.assertEqual((short_plan.stop_loss, short_plan.take_profit_3), (104.0, 88.0))
        self.assertEqual(short_plan.reward_to_risk(), (1.0, 2.0, 3.0))

    def test_v4_positive_funding_has_correct_side_sign(self) -> None:
        generated = pd.Timestamp("2026-09-14", tz="UTC")
        long_plan = symmetric.build_plan("ETHUSDT", "LONG", generated, 100.0, 2.0)
        short_plan = symmetric.build_plan("ETHUSDT", "SHORT", generated, 100.0, 2.0)
        long_position = symmetric.FuturesPosition(long_plan, 1.0, 1.0, 100.0, 4.0, 0.2, 96.0)
        short_position = symmetric.FuturesPosition(short_plan, 1.0, 1.0, 100.0, 4.0, 0.2, 104.0)
        rate = 0.001
        self.assertLess(-long_position.direction * 100.0 * rate, 0.0)
        self.assertGreater(-short_position.direction * 100.0 * rate, 0.0)

    def test_v4_short_intraday_ambiguity_uses_stop_first(self) -> None:
        generated = pd.Timestamp("2026-09-14", tz="UTC")
        plan = symmetric.build_plan("SOLUSDT", "SHORT", generated, 100.0, 2.0)
        position = symmetric.FuturesPosition(plan, 1.0, 1.0, 100.0, 4.0, 0.2, 104.0)
        cash, _ = symmetric.process_intraday(
            position,
            generated + pd.Timedelta(days=1),
            low=95.0,
            high=105.0,
            cost_rate=0.002,
        )
        self.assertLess(cash, 0.0)
        self.assertEqual(position.exits[0]["reason"], "STOP")

    def test_v4_blocks_nonpositive_short_tp3(self) -> None:
        with self.assertRaises(ValueError):
            symmetric.build_plan(
                "SOLUSDT",
                "SHORT",
                pd.Timestamp("2026-09-14", tz="UTC"),
                entry=10.0,
                atr=2.0,
            )

    def test_v5_channels_exclude_current_bar(self) -> None:
        dates = pd.date_range("2025-01-01", periods=60, freq="1D", tz="UTC")
        frame = pd.DataFrame(
            {"open": 1.0, "high": np.arange(60.0), "low": np.arange(60.0), "close": np.arange(60.0)},
            index=dates,
        )
        enriched = breakout.add_channels({"BTCUSDT": frame})["BTCUSDT"]
        self.assertEqual(enriched.iloc[-1]["entry_high"], 58.0)

    def test_v5_breakout_side_is_symmetric(self) -> None:
        date = pd.Timestamp("2025-01-02", tz="UTC")
        long_frame = pd.DataFrame(
            {"close": [110.0], "entry_high": [100.0], "entry_low": [90.0]}, index=[date]
        )
        short_frame = pd.DataFrame(
            {"close": [80.0], "entry_high": [100.0], "entry_low": [90.0]}, index=[date]
        )
        self.assertEqual(breakout.breakout_side({"X": long_frame}, "X", date), "LONG")
        self.assertEqual(breakout.breakout_side({"X": short_frame}, "X", date), "SHORT")

    def test_v6_sleeve_eligibility_is_fixed(self) -> None:
        trades = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"] * 8 + ["ETHUSDT"] * 7,
                "side": ["LONG"] * 15,
                "net_pnl": [2.0, -1.0] * 4 + [2.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0],
                "realized_r": [0.5, -0.25] * 4 + [0.5, -0.25, 0.5, -0.25, 0.5, -0.25, 0.5],
            }
        )
        selected, evidence = sleeve_council.eligible_sleeves(trades)
        self.assertEqual(selected, {("BTCUSDT", "LONG")})
        self.assertEqual(int(evidence["eligible"].sum()), 1)

    def test_v7_beta_regime_blocks_countertrend_breakout(self) -> None:
        date = pd.Timestamp("2025-01-02", tz="UTC")
        asset = pd.DataFrame(
            {"close": [110.0], "entry_high": [100.0], "entry_low": [90.0]}, index=[date]
        )
        btc_bear = pd.DataFrame({"close": [80.0], "sma200": [100.0]}, index=[date])
        btc_bull = pd.DataFrame({"close": [120.0], "sma200": [100.0]}, index=[date])
        self.assertIsNone(
            beta_regime.beta_regime_breakout_side({"X": asset, "BTCUSDT": btc_bear}, "X", date)
        )
        self.assertEqual(
            beta_regime.beta_regime_breakout_side({"X": asset, "BTCUSDT": btc_bull}, "X", date),
            "LONG",
        )

    def test_v7_audit_channels_and_regime_are_causal(self) -> None:
        dates = pd.date_range("2024-01-01", periods=230, freq="1D", tz="UTC")
        frame = pd.DataFrame(
            {
                "open": np.arange(230.0) + 100.0,
                "high": np.arange(230.0) + 101.0,
                "low": np.arange(230.0) + 99.0,
                "close": np.arange(230.0) + 100.0,
            },
            index=dates,
        )
        enriched = beta_audit.enrich({"BTCUSDT": frame}, entry_channel=55, sma_days=200)["BTCUSDT"]
        decision = dates[-2]
        before = enriched.loc[decision, ["audit_entry_high", "audit_sma"]].copy()
        changed = frame.copy()
        changed.loc[dates[-1], ["high", "close"]] = 1_000_000.0
        enriched_changed = beta_audit.enrich(
            {"BTCUSDT": changed}, entry_channel=55, sma_days=200
        )["BTCUSDT"]
        pd.testing.assert_series_equal(
            before, enriched_changed.loc[decision, ["audit_entry_high", "audit_sma"]]
        )

    def test_v4_audit_controls_validate_ranges(self) -> None:
        dates = pd.date_range("2026-01-01", periods=3, freq="1D", tz="UTC")
        frame = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr20": 2.0},
            index=dates,
        )
        markets = {symbol: frame.copy() for symbol in allocator.SYMBOLS}
        funding = {symbol: pd.Series(dtype=float) for symbol in allocator.SYMBOLS}
        with self.assertRaises(ValueError):
            symmetric.simulate(markets, funding, 0.002, dates[0], entry_signal_lag_days=-1)
        with self.assertRaises(ValueError):
            symmetric.simulate(markets, funding, 0.002, dates[0], funding_credit_fraction=1.1)


if __name__ == "__main__":
    unittest.main()
