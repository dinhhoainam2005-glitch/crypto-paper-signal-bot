"""Download and audit public Kraken Futures 1h candles for CORE4."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data" / "r55_kraken"
DEFAULT_OUTPUT_DIR = ROOT / "r55a_output" / "reports" / "R55A_KRAKEN_FUTURES_AUDIT"
API_ROOT = "https://futures.kraken.com/api/charts/v1/trade"
CONTRACTS = {
    "BTCUSDT": "PI_XBTUSD",
    "ETHUSDT": "PI_ETHUSD",
    "SOLUSDT": "PF_SOLUSD",
    "BNBUSDT": "PF_BNBUSD",
}
EXPECTED_STARTS = {
    "BTCUSDT": pd.Timestamp("2020-03-01", tz="UTC"),
    "ETHUSDT": pd.Timestamp("2020-03-01", tz="UTC"),
    "SOLUSDT": pd.Timestamp("2022-03-22", tz="UTC"),
    "BNBUSDT": pd.Timestamp("2023-11-22", tz="UTC"),
}
USER_AGENT = "Mozilla/5.0 (compatible; R55 research audit)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-02-01")
    last_closed_hour = pd.Timestamp.now(tz="UTC").floor("1h") - pd.Timedelta(hours=1)
    parser.add_argument("--end", default=last_closed_hour.isoformat())
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--sleep-seconds", type=float, default=0.10)
    return parser.parse_args()


def utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def api_url(contract: str, start: pd.Timestamp, end: pd.Timestamp) -> str:
    if contract not in CONTRACTS.values():
        raise ValueError(f"Unsupported Kraken contract: {contract}")
    query = urllib.parse.urlencode(
        {"from": int(start.timestamp()), "to": int(end.timestamp())}
    )
    return f"{API_ROOT}/{contract}/1h?{query}"


def fetch_window(contract: str, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    request = urllib.request.Request(
        api_url(contract, start, end), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("candles"), list):
        raise ValueError("Unexpected Kraken Futures candle response")
    return payload


def rows_to_frame(rows: list[dict[str, Any]], symbol: str, contract: str) -> pd.DataFrame:
    columns = ["time", "open", "high", "low", "close", "volume"]
    if not rows:
        return pd.DataFrame(
            columns=["symbol", "contract", "bar_open", "available_at", *columns[1:]]
        )
    frame = pd.DataFrame(rows)
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"Kraken candle fields missing: {missing}")
    frame["bar_open"] = pd.to_datetime(frame.pop("time"), unit="ms", utc=True)
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["symbol"] = symbol
    frame["contract"] = contract
    frame["available_at"] = frame["bar_open"] + pd.Timedelta(hours=1)
    frame = (
        frame.dropna(subset=["bar_open", "open", "high", "low", "close", "volume"])
        .drop_duplicates("bar_open", keep="last")
        .sort_values("bar_open")
    )
    return frame[
        ["symbol", "contract", "bar_open", "available_at", "open", "high", "low", "close", "volume"]
    ]


def download_symbol(
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    window_days: int = 90,
    sleep_seconds: float = 0.10,
    fetcher: Callable[[str, pd.Timestamp, pd.Timestamp], dict[str, Any]] = fetch_window,
) -> pd.DataFrame:
    contract = CONTRACTS[symbol]
    rows: list[dict[str, Any]] = []
    cursor = start.floor("1h")
    while cursor <= end:
        window_end = min(cursor + pd.Timedelta(days=window_days), end)
        payload = fetcher(contract, cursor, window_end)
        if payload.get("more_candles"):
            raise RuntimeError(
                f"Kraken window too large for complete response: {contract} {cursor} {window_end}"
            )
        rows.extend(payload["candles"])
        cursor = window_end + pd.Timedelta(hours=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return rows_to_frame(rows, symbol, contract)


def audit_symbol(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    scoped = frame.loc[frame["symbol"].eq(symbol)].sort_values("bar_open")
    if scoped.empty:
        return {"symbol": symbol, "contract": CONTRACTS[symbol], "rows": 0, "status": "MISSING"}
    start = scoped["bar_open"].iloc[0]
    end = scoped["bar_open"].iloc[-1]
    expected = pd.date_range(start, end, freq="1h", tz="UTC")
    observed = pd.DatetimeIndex(scoped["bar_open"])
    missing = expected.difference(observed)
    coverage = float(len(observed.unique()) / len(expected)) if len(expected) else 0.0
    duplicate_rows = int(scoped["bar_open"].duplicated().sum())
    causal = bool(scoped["available_at"].eq(scoped["bar_open"] + pd.Timedelta(hours=1)).all())
    valid_ohlc = bool(
        scoped[["open", "high", "low", "close"]].gt(0.0).all().all()
        and scoped["high"].ge(scoped[["open", "close", "low"]].max(axis=1)).all()
        and scoped["low"].le(scoped[["open", "close", "high"]].min(axis=1)).all()
    )
    passed = bool(
        start <= EXPECTED_STARTS[symbol] + pd.Timedelta(days=7)
        and coverage >= 0.99
        and duplicate_rows == 0
        and causal
        and valid_ohlc
    )
    return {
        "symbol": symbol,
        "contract": CONTRACTS[symbol],
        "rows": int(len(scoped)),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "expected_hours": int(len(expected)),
        "missing_hours": int(len(missing)),
        "coverage_pct": coverage * 100.0,
        "duplicate_rows": duplicate_rows,
        "causal_availability": causal,
        "valid_ohlc": valid_ohlc,
        "status": "PASS" if passed else "FAIL",
    }


def write_outputs(output_dir: Path, frame: pd.DataFrame) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audits = [audit_symbol(frame, symbol) for symbol in CONTRACTS]
    all_pass = all(row["status"] == "PASS" for row in audits)
    verdict = {
        "audit_id": "R55A_KRAKEN_FUTURES_AUDIT",
        "source": "OFFICIAL_PUBLIC_KRAKEN_FUTURES_API",
        "audits": audits,
        "all_symbols_pass": all_pass,
        "causal_rule": "HOURLY_CANDLE_AVAILABLE_AT_HOUR_CLOSE",
        "promotion_eligible": False,
        "status": "PASS" if all_pass else "FAIL",
    }
    frame.to_csv(output_dir / "R55A_KRAKEN_1H.csv", index=False)
    (output_dir / "R55A_VERDICT.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8"
    )
    return verdict


def main() -> int:
    args = parse_args()
    start = utc_timestamp(args.start)
    end = utc_timestamp(args.end).floor("1h")
    if end < start:
        raise ValueError("end must not be earlier than start")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    for symbol in CONTRACTS:
        path = args.data_dir / f"{symbol}_1h.csv"
        if args.download:
            frame = download_symbol(
                symbol,
                start,
                end,
                window_days=args.window_days,
                sleep_seconds=args.sleep_seconds,
            )
            frame.to_csv(path, index=False)
        elif path.exists():
            frame = pd.read_csv(path, parse_dates=["bar_open", "available_at"])
        else:
            raise FileNotFoundError(f"Run with --download to create {path}")
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    verdict = write_outputs(args.output_dir, combined)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
