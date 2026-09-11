"""Verified USD-M futures and funding archive loaders for V4."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from backtest_allocator import CSV_COLUMNS, parse_exchange_time
from fetch_binance_futures import DATA_ROOT


def load_futures_klines(
    symbol: str,
    interval: str,
    data_root: Path = DATA_ROOT,
    dataset: str = "klines",
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    directory = data_root / dataset / symbol
    for path in sorted(directory.glob(f"{symbol}-{interval}-*.zip")):
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                raise ValueError(f"Unexpected archive members in {path}")
            parts.append(pd.read_csv(archive.open(members[0]), names=CSV_COLUMNS))
    if not parts:
        raise FileNotFoundError(f"No {interval} futures klines for {symbol}")
    frame = pd.concat(parts, ignore_index=True)
    frame["date"] = parse_exchange_time(frame["open_time"])
    for column in ("open", "high", "low", "close", "quote_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .set_index("date")
    )


def load_futures_daily(
    symbol: str,
    data_root: Path = DATA_ROOT,
) -> pd.DataFrame:
    frame = load_futures_klines(symbol, "1d", data_root)
    frame.index = frame.index.floor("D")
    return frame.loc[~frame.index.duplicated(keep="last")]


def load_futures_hourly(
    symbol: str,
    data_root: Path = DATA_ROOT,
) -> pd.DataFrame:
    return load_futures_klines(symbol, "1h", data_root)


def load_futures_mark_price_hourly(
    symbol: str,
    data_root: Path = DATA_ROOT,
) -> pd.DataFrame:
    return load_futures_klines(symbol, "1h", data_root, dataset="markPriceKlines")


def load_funding_events(
    symbol: str,
    data_root: Path = DATA_ROOT,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    directory = data_root / "fundingRate" / symbol
    for path in sorted(directory.glob(f"{symbol}-fundingRate-*.zip")):
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                raise ValueError(f"Unexpected archive members in {path}")
            parts.append(pd.read_csv(archive.open(members[0])))
    if not parts:
        raise FileNotFoundError(f"No funding archives for {symbol}")
    frame = pd.concat(parts, ignore_index=True)
    frame["event_time"] = parse_exchange_time(frame["calc_time"])
    frame["funding_rate"] = pd.to_numeric(frame["last_funding_rate"], errors="coerce")
    frame["funding_interval_hours"] = pd.to_numeric(
        frame["funding_interval_hours"], errors="coerce"
    )
    return (
        frame.dropna(subset=["event_time", "funding_rate"])
        .sort_values("event_time")
        .drop_duplicates("event_time", keep="last")
        [["event_time", "funding_rate", "funding_interval_hours"]]
        .reset_index(drop=True)
    )


def daily_funding_rates(events: pd.DataFrame) -> pd.Series:
    dates = events["event_time"].dt.floor("D")
    return events.groupby(dates)["funding_rate"].sum().sort_index()
