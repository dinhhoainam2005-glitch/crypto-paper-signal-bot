"""Download and verify official Binance monthly spot 1d archives."""

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


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data" / "binance_spot_1d"
MANIFEST_PATH = ROOT / "reports" / "data_manifest.csv"
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
LISTING_MONTHS = {
    "BTCUSDT": "2017-08",
    "ETHUSDT": "2017-08",
    "BNBUSDT": "2017-11",
    "SOLUSDT": "2020-08",
}


@dataclass(frozen=True)
class DownloadTask:
    symbol: str
    month: str

    @property
    def filename(self) -> str:
        return f"{self.symbol}-1d-{self.month}.zip"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.symbol}/1d/{self.filename}"


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


def month_sequence(start: str, end: str) -> list[str]:
    start_year, start_month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    output: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        output.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return output


def tasks(through: str) -> list[DownloadTask]:
    return [
        DownloadTask(symbol, month)
        for symbol, start in LISTING_MONTHS.items()
        for month in month_sequence(start, through)
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


def download_one(task: DownloadTask, data_root: Path) -> dict[str, object]:
    destination = data_root / task.symbol / task.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        checksum_text = read_url(f"{task.url}.CHECKSUM").decode("ascii").strip()
        expected = checksum_text.split()[0].lower()
        if destination.exists() and sha256(destination) == expected:
            status = "CACHED_VERIFIED"
        else:
            payload = read_url(task.url)
            temporary = destination.with_suffix(".zip.part")
            temporary.write_bytes(payload)
            actual = sha256(temporary)
            if actual != expected:
                temporary.unlink(missing_ok=True)
                raise ValueError(f"checksum mismatch: {actual} != {expected}")
            os.replace(temporary, destination)
            status = "DOWNLOADED_VERIFIED"
        return {
            "symbol": task.symbol,
            "month": task.month,
            "status": status,
            "bytes": destination.stat().st_size,
            "sha256": expected,
            "path": str(destination),
        }
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {
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
    queue = tasks(args.through)
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(download_one, task, args.data_root): task for task in queue
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"{row['status']} {row['symbol']} {row['month']}", flush=True)
    rows.sort(key=lambda row: (str(row["symbol"]), str(row["month"])))
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    verified = sum(str(row["status"]).endswith("VERIFIED") for row in rows)
    unavailable = sum(row["status"] == "NOT_PUBLISHED" for row in rows)
    print(
        f"verified={verified} unavailable={unavailable} total={len(rows)} "
        f"bytes={sum(int(row['bytes']) for row in rows)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
