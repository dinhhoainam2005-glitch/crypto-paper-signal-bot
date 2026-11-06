"""Build causal BTC microstructure state frames from canonical events."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from .book_replay import OrderBook, ReplayError

BANDS_BPS = (5, 10, 25)
ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def _text(value: Decimal) -> str:
    return format(value, "f")


def _fresh_flow() -> dict[str, dict[str, Decimal]]:
    return {
        venue: {
            "buy": ZERO,
            "sell": ZERO,
            "long_liquidation": ZERO,
            "short_liquidation": ZERO,
        }
        for venue in ("binance", "bybit")
    }


def _book_metrics(book: OrderBook, frame_end_ns: int, last_book_ns: int | None) -> dict[str, Any]:
    best_bid = max(book.bids)
    best_ask = min(book.asks)
    mid = (best_bid + best_ask) / 2
    result: dict[str, Any] = {
        "best_bid": _text(best_bid),
        "best_ask": _text(best_ask),
        "mid": _text(mid),
        "spread_bps": _text((best_ask - best_bid) / mid * TEN_THOUSAND),
        "book_age_ms": None if last_book_ns is None else (frame_end_ns - last_book_ns) // 1_000_000,
    }
    for band in BANDS_BPS:
        fraction = Decimal(band) / TEN_THOUSAND
        bid_floor = mid * (1 - fraction)
        ask_ceiling = mid * (1 + fraction)
        bid_notional = sum(
            (price * quantity for price, quantity in book.bids.items() if price >= bid_floor),
            ZERO,
        )
        ask_notional = sum(
            (price * quantity for price, quantity in book.asks.items() if price <= ask_ceiling),
            ZERO,
        )
        total = bid_notional + ask_notional
        imbalance = ZERO if total == 0 else (bid_notional - ask_notional) / total
        result[f"bid_notional_{band}bps"] = _text(bid_notional)
        result[f"ask_notional_{band}bps"] = _text(ask_notional)
        result[f"imbalance_{band}bps"] = _text(imbalance)
    return result


def _emit_frame(
    bucket: int,
    books: dict[str, OrderBook],
    flow: dict[str, dict[str, Decimal]],
    last_book_ns: dict[str, int | None],
    last_trade_ns: dict[str, int | None],
    market_state: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    if not all(book.started_from_snapshot for book in books.values()):
        return None
    frame_end_ns = (bucket + 1) * 1_000_000_000
    venue_metrics = {
        venue: _book_metrics(book, frame_end_ns, last_book_ns[venue])
        for venue, book in books.items()
    }
    for venue in venue_metrics:
        venue_flow = flow[venue]
        venue_metrics[venue]["aggressive_buy_notional"] = _text(venue_flow["buy"])
        venue_metrics[venue]["aggressive_sell_notional"] = _text(venue_flow["sell"])
        venue_metrics[venue]["net_aggressive_notional"] = _text(
            venue_flow["buy"] - venue_flow["sell"]
        )
        venue_metrics[venue]["long_liquidation_notional"] = _text(
            venue_flow["long_liquidation"]
        )
        venue_metrics[venue]["short_liquidation_notional"] = _text(
            venue_flow["short_liquidation"]
        )
        venue_metrics[venue]["trade_age_ms"] = (
            None
            if last_trade_ns[venue] is None
            else (frame_end_ns - int(last_trade_ns[venue])) // 1_000_000
        )
        venue_metrics[venue]["market_state"] = dict(sorted(market_state[venue].items()))

    binance_mid = Decimal(venue_metrics["binance"]["mid"])
    bybit_mid = Decimal(venue_metrics["bybit"]["mid"])
    consolidated = (binance_mid + bybit_mid) / 2
    return {
        "schema_version": 1,
        "instrument": "BTCUSDT_PERPETUAL",
        "frame_start_ns": bucket * 1_000_000_000,
        "frame_end_ns": frame_end_ns,
        "consolidated_mid": _text(consolidated),
        "binance_minus_bybit_bps": _text(
            (binance_mid - bybit_mid) / consolidated * TEN_THOUSAND
        ),
        "venues": venue_metrics,
    }


def build_frames(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    books = {venue: OrderBook(venue) for venue in ("binance", "bybit")}
    flow = _fresh_flow()
    last_book_ns: dict[str, int | None] = {venue: None for venue in books}
    last_trade_ns: dict[str, int | None] = {venue: None for venue in books}
    market_state: dict[str, dict[str, str]] = {venue: {} for venue in books}
    frames: list[dict[str, Any]] = []
    current_bucket: int | None = None
    previous_receipt_ns: int | None = None

    for event in events:
        receipt_ns = int(event["receipt_time_ns"])
        if previous_receipt_ns is not None and receipt_ns < previous_receipt_ns:
            raise ReplayError("canonical events are not ordered by receipt time")
        previous_receipt_ns = receipt_ns
        bucket = receipt_ns // 1_000_000_000
        if current_bucket is None:
            current_bucket = bucket
        while current_bucket < bucket:
            frame = _emit_frame(
                current_bucket, books, flow, last_book_ns, last_trade_ns, market_state
            )
            if frame is not None:
                frames.append(frame)
            current_bucket += 1
            flow = _fresh_flow()

        venue = event["venue"]
        event_type = event["event_type"]
        data = event["data"]
        if event_type == "book":
            books[venue].apply(event)
            last_book_ns[venue] = receipt_ns
        elif event_type == "trade":
            notional = Decimal(data["price"]) * Decimal(data["quantity"])
            key = "buy" if data["aggressor_side"] == "BUY" else "sell"
            flow[venue][key] += notional
            last_trade_ns[venue] = receipt_ns
        elif event_type == "liquidation":
            notional = Decimal(data["price"]) * Decimal(data["quantity"])
            key = (
                "long_liquidation"
                if data["liquidated_position_side"] == "LONG"
                else "short_liquidation"
            )
            flow[venue][key] += notional
        elif event_type in {"mark", "ticker"}:
            market_state[venue].update({str(key): str(value) for key, value in data.items()})

    return frames


def build_file(normalized_path: Path) -> Path:
    with normalized_path.open(encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle]
    frames = build_frames(events)
    output_path = normalized_path.with_name("state_frames.jsonl")
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for frame in frames:
            output.write(json.dumps(frame, separators=(",", ":"), sort_keys=True) + "\n")
    manifest = {
        "schema_version": 1,
        "source": str(normalized_path),
        "output": output_path.name,
        "frames": len(frames),
        "first_frame_end_ns": frames[0]["frame_end_ns"] if frames else None,
        "last_frame_end_ns": frames[-1]["frame_end_ns"] if frames else None,
        "bands_bps": list(BANDS_BPS),
        "contains_labels": False,
        "contains_pnl": False,
    }
    manifest_path = normalized_path.with_name("state_frame_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normalized_file", type=Path)
    args = parser.parse_args()
    try:
        manifest_path = build_file(args.normalized_file)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        passed = manifest["frames"] > 0
        result = {"manifest": str(manifest_path), "passed": passed, **manifest}
    except (OSError, ValueError, json.JSONDecodeError, ReplayError) as exc:
        result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
