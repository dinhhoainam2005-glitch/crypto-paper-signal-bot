"""Deterministic BTC order-book reconstruction and integrity audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ReplayError(RuntimeError):
    """Raised when a source stream cannot produce a trustworthy book."""


def _decimal(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReplayError(f"invalid {label}: {value!r}") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ReplayError(f"invalid {label}: {value!r}")
    return number


@dataclass
class ReplayStats:
    venue: str
    snapshots: int = 0
    applied_deltas: int = 0
    stale_deltas: int = 0
    duplicate_deltas: int = 0
    gaps: int = 0
    crossed_books: int = 0


class OrderBook:
    def __init__(self, venue: str) -> None:
        self.venue = venue
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.update_id: int | None = None
        self.started_from_snapshot = False
        self.first_binance_delta = True
        self.stats = ReplayStats(venue)

    @staticmethod
    def _replace_side(target: dict[Decimal, Decimal], levels: list[list[str]]) -> None:
        target.clear()
        OrderBook._apply_levels(target, levels)

    @staticmethod
    def _apply_levels(target: dict[Decimal, Decimal], levels: list[list[str]]) -> None:
        for level in levels:
            if len(level) != 2:
                raise ReplayError("book level must contain price and quantity")
            price = _decimal(level[0], "price")
            quantity = _decimal(level[1], "quantity", allow_zero=True)
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = quantity

    def _assert_uncrossed(self) -> None:
        if not self.bids or not self.asks:
            raise ReplayError(f"{self.venue} book has an empty side")
        if max(self.bids) >= min(self.asks):
            self.stats.crossed_books += 1
            raise ReplayError(f"{self.venue} book is crossed")

    def apply(self, event: dict[str, Any]) -> None:
        data = event["data"]
        kind = data["update_kind"]
        if kind == "snapshot":
            self._replace_side(self.bids, data["bids"])
            self._replace_side(self.asks, data["asks"])
            self.update_id = int(data.get("last_update_id", data.get("update_id")))
            self.started_from_snapshot = True
            self.first_binance_delta = True
            self.stats.snapshots += 1
            self._assert_uncrossed()
            return

        if not self.started_from_snapshot or self.update_id is None:
            raise ReplayError(f"{self.venue} delta arrived before snapshot")
        if self.venue == "binance":
            self._apply_binance_delta(data)
        elif self.venue == "bybit":
            self._apply_bybit_delta(data)
        else:
            raise ReplayError(f"unsupported venue: {self.venue}")

    def _apply_binance_delta(self, data: dict[str, Any]) -> None:
        first = int(data["first_update_id"])
        last = int(data["last_update_id"])
        previous = int(data["previous_update_id"])
        assert self.update_id is not None
        if last <= self.update_id:
            self.stats.stale_deltas += 1
            return
        if self.first_binance_delta:
            if not (first <= self.update_id + 1 <= last):
                self.stats.gaps += 1
                raise ReplayError("Binance first delta does not bridge snapshot")
            self.first_binance_delta = False
        elif previous != self.update_id:
            self.stats.gaps += 1
            raise ReplayError("Binance pu does not match prior u")
        self._apply_levels(self.bids, data["bids"])
        self._apply_levels(self.asks, data["asks"])
        self.update_id = last
        self.stats.applied_deltas += 1
        self._assert_uncrossed()

    def _apply_bybit_delta(self, data: dict[str, Any]) -> None:
        current = int(data["update_id"])
        assert self.update_id is not None
        if current == self.update_id:
            self.stats.duplicate_deltas += 1
            return
        if current != self.update_id + 1:
            self.stats.gaps += 1
            raise ReplayError("Bybit update ID is not consecutive")
        self._apply_levels(self.bids, data["bids"])
        self._apply_levels(self.asks, data["asks"])
        self.update_id = current
        self.stats.applied_deltas += 1
        self._assert_uncrossed()

    def report(self) -> dict[str, Any]:
        self._assert_uncrossed()
        canonical = {
            "bids": [[format(price, "f"), format(self.bids[price], "f")] for price in sorted(self.bids)],
            "asks": [[format(price, "f"), format(self.asks[price], "f")] for price in sorted(self.asks)],
            "update_id": self.update_id,
        }
        checksum = hashlib.sha256(
            json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "venue": self.venue,
            "initialized": self.started_from_snapshot,
            "update_id": self.update_id,
            "bid_levels": len(self.bids),
            "ask_levels": len(self.asks),
            "best_bid": format(max(self.bids), "f"),
            "best_ask": format(min(self.asks), "f"),
            "state_sha256": checksum,
            "stats": asdict(self.stats),
        }


def replay(path: Path) -> dict[str, Any]:
    books = {venue: OrderBook(venue) for venue in ("binance", "bybit")}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            event = json.loads(line)
            if event.get("event_type") != "book":
                continue
            venue = event.get("venue")
            if venue not in books:
                raise ReplayError(f"line {line_number}: unsupported venue {venue!r}")
            try:
                books[venue].apply(event)
            except ReplayError as exc:
                raise ReplayError(f"line {line_number}: {exc}") from exc
    return {venue: book.report() for venue, book in books.items()}


def audit_replay(path: Path) -> dict[str, Any]:
    first = replay(path)
    second = replay(path)
    deterministic = first == second
    return {
        "schema_version": 1,
        "source": str(path),
        "deterministic": deterministic,
        "passed": deterministic and all(report["initialized"] for report in first.values()),
        "venues": first,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized_file", type=Path)
    args = parser.parse_args()
    try:
        report = audit_replay(args.normalized_file)
    except (OSError, ValueError, json.JSONDecodeError, ReplayError) as exc:
        report = {
            "schema_version": 1,
            "source": str(args.normalized_file),
            "deterministic": False,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    output = args.normalized_file.with_name("book_replay_report.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
