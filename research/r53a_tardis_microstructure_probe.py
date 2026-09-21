"""Build causal hourly microstructure features from Tardis CSV samples.

R53A is a data and feature-engineering probe, not a trading backtest. It uses
``local_timestamp`` (when the message reached the recorder) and only publishes
an hourly feature after that hour has closed. This keeps later R53 experiments
from accidentally using exchange messages before they were observable.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_OUTPUT_DIR = ROOT / "r53a_output" / "reports" / "R53A_TARDIS_SAMPLE_PROBE"
EXCHANGE = "binance-futures"
CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
DATA_TYPES = ("trades", "derivative_ticker", "liquidations", "book_snapshot_5")
CHUNK_ROWS = 500_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=CORE_SYMBOLS, default="BTCUSDT")
    parser.add_argument("--date", default="2021-09-01")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--trades", type=Path)
    parser.add_argument("--derivative-ticker", type=Path)
    parser.add_argument("--liquidations", type=Path)
    return parser.parse_args()


def dataset_url(data_type: str, date: str, symbol: str, api_key: str | None = None) -> str:
    if data_type not in DATA_TYPES:
        raise ValueError(f"Unsupported Tardis data type: {data_type}")
    if symbol not in CORE_SYMBOLS:
        raise ValueError(f"R53 is restricted to CORE4, got: {symbol}")
    stamp = pd.Timestamp(date)
    if api_key is None and stamp.day != 1:
        raise ValueError("Unauthenticated Tardis samples are available only for day 01")
    return (
        f"https://datasets.tardis.dev/v1/{EXCHANGE}/{data_type}/"
        f"{stamp:%Y/%m/%d}/{symbol}.csv.gz"
    )


def default_dataset_path(data_dir: Path, symbol: str, data_type: str, date: str) -> Path:
    asset = symbol.removesuffix("USDT").lower()
    return data_dir / f"r53_{asset}_{data_type}_{date}.csv.gz"


def _timestamp(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, unit="us", utc=True, errors="coerce")


def _numeric(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _combine_sums(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts).groupby(level=0, sort=True).sum(min_count=1)


def aggregate_trades(path: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    columns = ["local_timestamp", "side", "price", "amount"]
    for chunk in pd.read_csv(path, compression="gzip", usecols=columns, chunksize=CHUNK_ROWS):
        _numeric(chunk, ("price", "amount"))
        available = _timestamp(chunk["local_timestamp"])
        chunk["hour_open"] = available.dt.floor("h")
        chunk["trade_notional"] = chunk["price"] * chunk["amount"]
        chunk["buy_notional"] = chunk["trade_notional"].where(chunk["side"].eq("buy"), 0.0)
        chunk["sell_notional"] = chunk["trade_notional"].where(chunk["side"].eq("sell"), 0.0)
        chunk["trade_count"] = 1
        grouped = chunk.groupby("hour_open", sort=True)[
            ["trade_count", "trade_notional", "buy_notional", "sell_notional"]
        ].sum(min_count=1)
        parts.append(grouped)

    result = _combine_sums(parts)
    if result.empty:
        return result
    denominator = result["trade_notional"].replace(0.0, np.nan)
    result["taker_notional_imbalance"] = (
        (result["buy_notional"] - result["sell_notional"]) / denominator
    )
    result.index.name = "hour_open"
    return result


def aggregate_liquidations(path: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    columns = ["local_timestamp", "side", "price", "amount"]
    for chunk in pd.read_csv(path, compression="gzip", usecols=columns, chunksize=CHUNK_ROWS):
        _numeric(chunk, ("price", "amount"))
        available = _timestamp(chunk["local_timestamp"])
        chunk["hour_open"] = available.dt.floor("h")
        chunk["liquidation_notional"] = chunk["price"] * chunk["amount"]
        # Tardis semantics: sell liquidates a long; buy liquidates a short.
        chunk["long_liquidated_notional"] = chunk["liquidation_notional"].where(
            chunk["side"].eq("sell"), 0.0
        )
        chunk["short_liquidated_notional"] = chunk["liquidation_notional"].where(
            chunk["side"].eq("buy"), 0.0
        )
        chunk["liquidation_count"] = 1
        grouped = chunk.groupby("hour_open", sort=True)[
            [
                "liquidation_count",
                "liquidation_notional",
                "long_liquidated_notional",
                "short_liquidated_notional",
            ]
        ].sum(min_count=1)
        parts.append(grouped)

    result = _combine_sums(parts)
    if result.empty:
        return result
    denominator = result["liquidation_notional"].replace(0.0, np.nan)
    result["liquidation_pressure"] = (
        (result["short_liquidated_notional"] - result["long_liquidated_notional"])
        / denominator
    )
    result.index.name = "hour_open"
    return result


def aggregate_derivative_ticker(path: Path) -> pd.DataFrame:
    columns = [
        "local_timestamp",
        "funding_rate",
        "open_interest",
        "last_price",
        "index_price",
        "mark_price",
    ]
    frame = pd.read_csv(path, compression="gzip", usecols=columns)
    _numeric(frame, columns[1:])
    frame["available_at_raw"] = _timestamp(frame["local_timestamp"])
    frame = frame.dropna(subset=["available_at_raw"]).sort_values("available_at_raw")
    frame["hour_open"] = frame["available_at_raw"].dt.floor("h")
    grouped = frame.groupby("hour_open", sort=True)
    result = grouped.agg(
        ticker_updates=("local_timestamp", "size"),
        oi_open=("open_interest", "first"),
        oi_close=("open_interest", "last"),
        funding_rate=("funding_rate", "last"),
        last_price=("last_price", "last"),
        index_price=("index_price", "last"),
        mark_price=("mark_price", "last"),
        source_last_seen=("available_at_raw", "last"),
    )
    result["oi_change_pct"] = result["oi_close"] / result["oi_open"] - 1.0
    result["mark_index_basis_bps"] = (
        (result["mark_price"] / result["index_price"] - 1.0) * 10_000.0
    )
    result.index.name = "hour_open"
    return result


def combine_features(parts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    nonempty = [frame.add_prefix(f"{name}_") for name, frame in parts.items() if not frame.empty]
    if not nonempty:
        return pd.DataFrame()
    result = pd.concat(nonempty, axis=1).sort_index()
    result.insert(0, "available_at", result.index + pd.Timedelta(hours=1))
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def audit(
    symbol: str,
    date: str,
    paths: dict[str, Path],
    parts: dict[str, pd.DataFrame],
    features: pd.DataFrame,
) -> dict[str, Any]:
    expected_hours = 24
    present_hours = int(features.index.nunique()) if not features.empty else 0
    availability_ok = bool(
        features.empty
        or (features["available_at"] == features.index + pd.Timedelta(hours=1)).all()
    )
    return {
        "audit_id": "R53A_TARDIS_SAMPLE_PROBE",
        "purpose": "DATA_AND_FEATURE_PIPELINE_ONLY_NOT_ALPHA_EVIDENCE",
        "symbol": symbol,
        "date": date,
        "exchange": EXCHANGE,
        "files": {
            name: {
                "path": str(path.resolve()),
                "exists": path.exists(),
                "compressed_bytes": path.stat().st_size if path.exists() else 0,
                "aggregated_hours": int(len(parts.get(name, pd.DataFrame()))),
            }
            for name, path in paths.items()
        },
        "feature_hours": present_hours,
        "expected_hours": expected_hours,
        "coverage_pct": present_hours / expected_hours * 100.0,
        "causal_availability_pass": availability_ok,
        "schema_pass": bool(parts) and all(not frame.empty for frame in parts.values()),
        "status": "PASS" if availability_ok and present_hours == expected_hours else "REVIEW",
        "promotion_eligible": False,
        "note": "One-day monthly samples cannot validate profitability or any R31A gate.",
    }


def write_report(output_dir: Path, result: dict[str, Any], features: pd.DataFrame) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    features.reset_index().to_csv(output_dir / "R53A_FEATURES.csv", index=False)
    (output_dir / "R53A_DATA_AUDIT.json").write_text(
        json.dumps(_json_safe(result), indent=2, sort_keys=True), encoding="utf-8"
    )
    files = result["files"]
    lines = [
        "# R53A Tardis Sample Probe",
        "",
        f"- Status: **{result['status']}**.",
        f"- Symbol/date: `{result['symbol']}` / `{result['date']}`.",
        f"- Hourly coverage: `{result['feature_hours']}/{result['expected_hours']}`.",
        f"- Causal availability: `{result['causal_availability_pass']}`.",
        "- Promotion eligible: **False**. This is a pipeline probe, not alpha evidence.",
        "",
        "## Inputs",
        "",
        "| Dataset | Exists | Compressed bytes | Aggregated hours |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, item in files.items():
        lines.append(
            f"| {name} | {item['exists']} | {item['compressed_bytes']} | "
            f"{item['aggregated_hours']} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "R53A proves only that normalized trades, open-interest/funding and liquidation files can be read causally and aggregated to 1h. Full-history data and locked out-of-sample evaluation are still required before any strategy can be judged against the unchanged 15/15 gate.",
            "",
        ]
    )
    (output_dir / "R53A_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    paths = {
        "trades": args.trades
        or default_dataset_path(args.data_dir, args.symbol, "trades", args.date),
        "derivative": args.derivative_ticker
        or default_dataset_path(args.data_dir, args.symbol, "derivative_ticker", args.date),
        "liquidations": args.liquidations
        or default_dataset_path(args.data_dir, args.symbol, "liquidations", args.date),
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing R53A sample files: {missing}")

    parts = {
        "trades": aggregate_trades(paths["trades"]),
        "derivative": aggregate_derivative_ticker(paths["derivative"]),
        "liquidations": aggregate_liquidations(paths["liquidations"]),
    }
    features = combine_features(parts)
    result = audit(args.symbol, args.date, paths, parts, features)
    write_report(args.output_dir, result, features)
    print(json.dumps(_json_safe(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
