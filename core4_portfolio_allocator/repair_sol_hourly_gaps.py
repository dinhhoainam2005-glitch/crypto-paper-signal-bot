"""Fetch verified Binance 5m archives used only to repair known SOL 1h gaps."""

from __future__ import annotations

import csv
from pathlib import Path

from fetch_binance_futures import BASE_URL, DATA_ROOT, FuturesTask, download_one


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "reports" / "sol_hourly_gap_repair_manifest.csv"


class FiveMinuteTask(FuturesTask):
    @property
    def interval(self) -> str:
        return "5m"

    @property
    def filename(self) -> str:
        return f"{self.symbol}-5m-{self.month}.zip"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/klines/{self.symbol}/5m/{self.filename}"


class MarkPriceTask(FuturesTask):
    @property
    def interval(self) -> str:
        return "1h"

    @property
    def filename(self) -> str:
        return f"{self.symbol}-1h-{self.month}.zip"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/markPriceKlines/{self.symbol}/1h/{self.filename}"


def main() -> int:
    trade_rows = [
        download_one(FiveMinuteTask("klines", "SOLUSDT", month), DATA_ROOT)
        for month in ("2022-02", "2022-04")
    ]
    mark_rows = [
        download_one(MarkPriceTask("markPriceKlines", "SOLUSDT", month), DATA_ROOT)
        for month in ("2022-02", "2022-04")
    ]
    rows = trade_rows + mark_rows
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"{row['status']} {row['dataset']} {row['symbol']} {row['month']}", flush=True)
    return 0 if all(str(row["status"]).endswith("VERIFIED") for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
