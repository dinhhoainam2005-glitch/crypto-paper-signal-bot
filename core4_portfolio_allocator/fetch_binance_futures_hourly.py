"""Download checksum-verified official Binance USD-M 1h kline archives."""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fetch_binance_daily import month_sequence
from fetch_binance_futures import (
    BASE_URL,
    DATA_ROOT,
    LISTING_MONTHS,
    FuturesTask,
    download_one,
)


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "reports" / "futures_hourly_data_manifest.csv"


class HourlyTask(FuturesTask):
    @property
    def interval(self) -> str:
        return "1h"

    @property
    def filename(self) -> str:
        return f"{self.symbol}-1h-{self.month}.zip"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/klines/{self.symbol}/1h/{self.filename}"


def parse_args() -> argparse.Namespace:
    today = datetime.now(timezone.utc)
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", default=f"{year:04d}-{month:02d}")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    return parser.parse_args()


def tasks(through: str) -> list[HourlyTask]:
    return [
        HourlyTask("klines", symbol, month)
        for symbol, start in LISTING_MONTHS.items()
        for month in month_sequence(start, through)
    ]


def main() -> int:
    args = parse_args()
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        pending = {
            executor.submit(download_one, task, args.data_root): task
            for task in tasks(args.through)
        }
        for future in as_completed(pending):
            row = future.result()
            rows.append(row)
            print(f"{row['status']} 1h {row['symbol']} {row['month']}", flush=True)
    rows.sort(key=lambda row: (str(row["symbol"]), str(row["month"])))
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"verified={sum(str(row['status']).endswith('VERIFIED') for row in rows)} "
        f"unavailable={sum(row['status'] == 'NOT_PUBLISHED' for row in rows)} "
        f"total={len(rows)} bytes={sum(int(row['bytes']) for row in rows)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
