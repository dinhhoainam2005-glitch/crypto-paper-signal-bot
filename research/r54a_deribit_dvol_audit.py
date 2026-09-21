"""Download and audit causal daily BTC/ETH DVOL from the public Deribit API."""

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
DEFAULT_DATA_DIR = ROOT / "data" / "r54_deribit"
DEFAULT_OUTPUT_DIR = ROOT / "r54a_output" / "reports" / "R54A_DERIBIT_DVOL_AUDIT"
API_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
CURRENCIES = ("BTC", "ETH")
RESOLUTION = "1D"
EARLIEST_EXPECTED = pd.Timestamp("2021-03-24", tz="UTC")
USER_AGENT = "Mozilla/5.0 (compatible; R54 research audit)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-01-01")
    last_closed_day = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    parser.add_argument("--end", default=last_closed_day.strftime("%Y-%m-%d"))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    return parser.parse_args()


def utc_day(value: str | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.normalize()


def to_milliseconds(value: pd.Timestamp) -> int:
    return int(value.timestamp() * 1000)


def api_url(currency: str, start_ms: int, end_ms: int) -> str:
    if currency not in CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency}")
    query = urllib.parse.urlencode(
        {
            "currency": currency,
            "start_timestamp": start_ms,
            "end_timestamp": end_ms,
            "resolution": RESOLUTION,
        }
    )
    return f"{API_URL}?{query}"


def fetch_page(currency: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    request = urllib.request.Request(
        api_url(currency, start_ms, end_ms), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"Deribit API error: {payload['error']}")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise ValueError("Unexpected Deribit DVOL response")
    return result


def rows_to_frame(rows: list[list[Any]], currency: str) -> pd.DataFrame:
    columns = ["timestamp_ms", "open", "high", "low", "close"]
    if not rows:
        return pd.DataFrame(
            columns=["currency", "bar_open", "available_at", "open", "high", "low", "close"]
        )
    frame = pd.DataFrame(rows, columns=columns)
    frame["bar_open"] = pd.to_datetime(frame.pop("timestamp_ms"), unit="ms", utc=True)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["currency"] = currency
    frame["available_at"] = frame["bar_open"] + pd.Timedelta(days=1)
    frame = (
        frame.dropna(subset=["bar_open", "open", "high", "low", "close"])
        .drop_duplicates("bar_open", keep="last")
        .sort_values("bar_open")
    )
    return frame[["currency", "bar_open", "available_at", "open", "high", "low", "close"]]


def download_currency(
    currency: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    fetcher: Callable[[str, int, int], dict[str, Any]] = fetch_page,
    sleep_seconds: float = 0.15,
) -> pd.DataFrame:
    start_ms = to_milliseconds(start)
    end_ms = to_milliseconds(end + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1))
    rows: list[list[Any]] = []
    seen_continuations: set[int] = set()
    while end_ms >= start_ms:
        result = fetcher(currency, start_ms, end_ms)
        rows.extend(result["data"])
        continuation = result.get("continuation")
        if continuation is None:
            break
        continuation = int(continuation)
        if continuation in seen_continuations or continuation < start_ms:
            break
        seen_continuations.add(continuation)
        end_ms = continuation
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    frame = rows_to_frame(rows, currency)
    return frame.loc[frame["bar_open"].between(start, end)].reset_index(drop=True)


def audit_currency(frame: pd.DataFrame, currency: str) -> dict[str, Any]:
    scoped = frame.loc[frame["currency"].eq(currency)].sort_values("bar_open")
    if scoped.empty:
        return {"currency": currency, "rows": 0, "status": "MISSING"}
    start = scoped["bar_open"].iloc[0]
    end = scoped["bar_open"].iloc[-1]
    expected = pd.date_range(start, end, freq="1D", tz="UTC")
    observed = pd.DatetimeIndex(scoped["bar_open"])
    missing = expected.difference(observed)
    duplicate_rows = int(scoped["bar_open"].duplicated().sum())
    causal = bool(scoped["available_at"].eq(scoped["bar_open"] + pd.Timedelta(days=1)).all())
    valid_ohlc = bool(
        scoped[["open", "high", "low", "close"]].gt(0.0).all().all()
        and scoped["high"].ge(scoped[["open", "close", "low"]].max(axis=1)).all()
        and scoped["low"].le(scoped[["open", "close", "high"]].min(axis=1)).all()
    )
    coverage = float(len(observed.unique()) / len(expected)) if len(expected) else 0.0
    passed = bool(
        start <= EARLIEST_EXPECTED
        and coverage >= 0.995
        and not duplicate_rows
        and causal
        and valid_ohlc
    )
    return {
        "currency": currency,
        "rows": int(len(scoped)),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "expected_days": int(len(expected)),
        "missing_days": int(len(missing)),
        "coverage_pct": coverage * 100.0,
        "duplicate_rows": duplicate_rows,
        "causal_availability": causal,
        "valid_ohlc": valid_ohlc,
        "status": "PASS" if passed else "FAIL",
    }


def write_outputs(output_dir: Path, frame: pd.DataFrame) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audits = [audit_currency(frame, currency) for currency in CURRENCIES]
    verdict = {
        "audit_id": "R54A_DERIBIT_DVOL_AUDIT",
        "source": "OFFICIAL_PUBLIC_DERIBIT_API",
        "series": "BTC_AND_ETH_DVOL_DAILY_OHLC",
        "audits": audits,
        "all_series_pass": all(row["status"] == "PASS" for row in audits),
        "causal_rule": "DAILY_CANDLE_AVAILABLE_AT_NEXT_UTC_DAY",
        "contains_directional_skew": False,
        "promotion_eligible": False,
    }
    verdict["status"] = "PASS" if verdict["all_series_pass"] else "FAIL"
    frame.to_csv(output_dir / "R54A_DVOL_DAILY.csv", index=False)
    (output_dir / "R54A_VERDICT.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# R54A Deribit DVOL Data Audit",
        "",
        f"- Status: **{verdict['status']}**.",
        "- Source: official public Deribit API.",
        "- Causal rule: a daily candle is available only at the next UTC day.",
        "- Directional skew included: **False**.",
        "- Promotion eligible: **False**. This is a data audit only.",
        "",
        "| Currency | Rows | Start | End | Coverage | Missing | Status |",
        "| --- | ---: | --- | --- | ---: | ---: | --- |",
    ]
    for row in audits:
        lines.append(
            f"| {row['currency']} | {row['rows']} | {row.get('start', 'n/a')} | "
            f"{row.get('end', 'n/a')} | {row.get('coverage_pct', 0.0):.3f}% | "
            f"{row.get('missing_days', 0)} | {row['status']} |"
        )
    (output_dir / "R54A_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return verdict


def main() -> int:
    args = parse_args()
    start = utc_day(args.start)
    end = utc_day(args.end)
    if end < start:
        raise ValueError("end must not be earlier than start")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    for currency in CURRENCIES:
        path = args.data_dir / f"{currency}_DVOL_1D.csv"
        if args.download:
            frame = download_currency(currency, start, end, sleep_seconds=args.sleep_seconds)
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
