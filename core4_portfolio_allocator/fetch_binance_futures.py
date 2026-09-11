"""Download verified official Binance USD-M daily klines and funding archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fetch_binance_daily import month_sequence


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data" / "binance_usdm"
MANIFEST_PATH = ROOT / "reports" / "futures_data_manifest.csv"
BASE_URL = "https://data.binance.vision/data/futures/um/monthly"
LISTING_MONTHS = {
    "BTCUSDT": "2019-09",
    "ETHUSDT": "2019-11",
    "BNBUSDT": "2020-02",
    "SOLUSDT": "2020-09",
}


@dataclass(frozen=True)
class FuturesTask:
    dataset: str
    symbol: str
    month: str

    @property
    def interval(self) -> str:
        return "1d" if self.dataset == "klines" else ""

    @property
    def filename(self) -> str:
        if self.dataset == "klines":
            return f"{self.symbol}-1d-{self.month}.zip"
        return f"{self.symbol}-fundingRate-{self.month}.zip"

    @property
    def url(self) -> str:
        if self.dataset == "klines":
            return f"{BASE_URL}/klines/{self.symbol}/1d/{self.filename}"
        return f"{BASE_URL}/fundingRate/{self.symbol}/{self.filename}"


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


def tasks(through: str) -> list[FuturesTask]:
    return [
        FuturesTask(dataset, symbol, month)
        for symbol, start in LISTING_MONTHS.items()
        for month in month_sequence(start, through)
        for dataset in ("klines", "fundingRate")
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_url(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "core4-cleanroom/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def download_one(task: FuturesTask, data_root: Path) -> dict[str, object]:
    destination = data_root / task.dataset / task.symbol / task.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        checksum = read_url(f"{task.url}.CHECKSUM").decode("ascii").split()[0].lower()
        if destination.exists() and sha256(destination) == checksum:
            status = "CACHED_VERIFIED"
        else:
            temporary = destination.with_suffix(".zip.part")
            temporary.write_bytes(read_url(task.url))
            actual = sha256(temporary)
            if actual != checksum:
                temporary.unlink(missing_ok=True)
                raise ValueError(f"checksum mismatch: {actual} != {checksum}")
            os.replace(temporary, destination)
            status = "DOWNLOADED_VERIFIED"
        return {
            "dataset": task.dataset,
            "symbol": task.symbol,
            "month": task.month,
            "status": status,
            "bytes": destination.stat().st_size,
            "sha256": checksum,
            "path": str(destination),
        }
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {
                "dataset": task.dataset,
                "symbol": task.symbol,
                "month": task.month,
                "status": "NOT_PUBLISHED",
                "bytes": 0,
                "sha256": "",
                "path": str(destination),
            }
        raise


def main() -> int:
    args = parse_args()
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(download_one, task, args.data_root): task
            for task in tasks(args.through)
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                f"{row['status']} {row['dataset']} {row['symbol']} {row['month']}",
                flush=True,
            )
    rows.sort(key=lambda row: (str(row["dataset"]), str(row["symbol"]), str(row["month"])))
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
