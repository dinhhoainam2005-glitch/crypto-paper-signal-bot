"""Normalize captured BTC venue messages without deriving market features."""

from __future__ import annotations

import argparse
import heapq
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import SYMBOL

INSTRUMENT = "BTCUSDT_PERPETUAL"
SCHEMA_VERSION = 1


def _envelope(
    raw: dict[str, Any],
    *,
    event_type: str,
    exchange_event_time_ms: int | None,
    matching_time_ms: int | None,
    source_sequence: str | int | None,
    source: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "venue": raw["venue"],
        "instrument": INSTRUMENT,
        "event_type": event_type,
        "exchange_event_time_ms": exchange_event_time_ms,
        "matching_time_ms": matching_time_ms,
        "receipt_time_ns": int(raw["receipt_time_ns"]),
        "source_sequence": source_sequence,
        "source": source,
        "data": data,
    }


def _require_symbol(value: Any) -> None:
    if value != SYMBOL:
        raise ValueError(f"unexpected symbol: {value!r}")


def _normalize_binance(raw: dict[str, Any]) -> list[dict[str, Any]]:
    payload = raw["payload"]
    event = payload.get("e")
    if event is None and {"lastUpdateId", "bids", "asks"}.issubset(payload):
        return [
            _envelope(
                raw,
                event_type="book",
                exchange_event_time_ms=None,
                matching_time_ms=None,
                source_sequence=int(payload["lastUpdateId"]),
                source="fapi/v1/depth",
                data={
                    "update_kind": "snapshot",
                    "last_update_id": int(payload["lastUpdateId"]),
                    "bids": payload["bids"],
                    "asks": payload["asks"],
                },
            )
        ]
    if event is None:
        return []
    if event == "aggTrade":
        _require_symbol(payload.get("s"))
        aggressor = "SELL" if bool(payload["m"]) else "BUY"
        return [
            _envelope(
                raw,
                event_type="trade",
                exchange_event_time_ms=int(payload["E"]),
                matching_time_ms=int(payload["T"]),
                source_sequence=int(payload["a"]),
                source="btcusdt@aggTrade",
                data={
                    "trade_id": str(payload["a"]),
                    "first_trade_id": str(payload["f"]),
                    "last_trade_id": str(payload["l"]),
                    "price": str(payload["p"]),
                    "quantity": str(payload["q"]),
                    "normal_quantity": str(payload.get("nq", payload["q"])),
                    "aggressor_side": aggressor,
                },
            )
        ]
    if event == "depthUpdate":
        _require_symbol(payload.get("s"))
        return [
            _envelope(
                raw,
                event_type="book",
                exchange_event_time_ms=int(payload["E"]),
                matching_time_ms=int(payload["T"]),
                source_sequence=int(payload["u"]),
                source="btcusdt@depth@100ms",
                data={
                    "update_kind": "delta",
                    "first_update_id": int(payload["U"]),
                    "last_update_id": int(payload["u"]),
                    "previous_update_id": int(payload["pu"]),
                    "bids": payload["b"],
                    "asks": payload["a"],
                },
            )
        ]
    if event == "markPriceUpdate":
        _require_symbol(payload.get("s"))
        return [
            _envelope(
                raw,
                event_type="mark",
                exchange_event_time_ms=int(payload["E"]),
                matching_time_ms=None,
                source_sequence=None,
                source="btcusdt@markPrice@1s",
                data={
                    "mark_price": str(payload["p"]),
                    "index_price": str(payload["i"]),
                    "funding_rate": str(payload["r"]),
                    "next_funding_time_ms": int(payload["T"]),
                    "estimated_settle_price": str(payload.get("P", "")),
                    "moving_average_price": str(payload.get("ap", "")),
                },
            )
        ]
    if event == "forceOrder":
        order = payload["o"]
        _require_symbol(order.get("s"))
        raw_side = str(order["S"]).upper()
        liquidated_side = {"SELL": "LONG", "BUY": "SHORT"}[raw_side]
        return [
            _envelope(
                raw,
                event_type="liquidation",
                exchange_event_time_ms=int(payload["E"]),
                matching_time_ms=int(order["T"]),
                source_sequence=None,
                source="btcusdt@forceOrder",
                data={
                    "raw_side": raw_side,
                    "liquidated_position_side": liquidated_side,
                    "price": str(order["p"]),
                    "average_price": str(order.get("ap", "")),
                    "quantity": str(order["q"]),
                    "filled_quantity": str(order.get("z", "")),
                    "status": str(order.get("X", "")),
                },
            )
        ]
    raise ValueError(f"unsupported Binance event type: {event}")


def _normalize_bybit(raw: dict[str, Any]) -> list[dict[str, Any]]:
    payload = raw["payload"]
    topic = payload.get("topic")
    if topic is None:
        return []
    if topic == f"publicTrade.{SYMBOL}":
        events = []
        for trade in payload["data"]:
            _require_symbol(trade.get("s"))
            events.append(
                _envelope(
                    raw,
                    event_type="trade",
                    exchange_event_time_ms=int(payload["ts"]),
                    matching_time_ms=int(trade["T"]),
                    source_sequence=int(trade["seq"]),
                    source=topic,
                    data={
                        "trade_id": str(trade["i"]),
                        "price": str(trade["p"]),
                        "quantity": str(trade["v"]),
                        "aggressor_side": str(trade["S"]).upper(),
                        "block_trade": bool(trade.get("BT", False)),
                        "rpi_trade": bool(trade.get("RPI", False)),
                    },
                )
            )
        return events
    if topic == f"orderbook.50.{SYMBOL}":
        data = payload["data"]
        _require_symbol(data.get("s"))
        return [
            _envelope(
                raw,
                event_type="book",
                exchange_event_time_ms=int(payload["ts"]),
                matching_time_ms=int(payload["cts"]),
                source_sequence=int(data["u"]),
                source=topic,
                data={
                    "update_kind": str(payload["type"]),
                    "update_id": int(data["u"]),
                    "cross_sequence": int(data["seq"]),
                    "bids": data["b"],
                    "asks": data["a"],
                },
            )
        ]
    if topic == f"tickers.{SYMBOL}":
        data = payload["data"]
        _require_symbol(data.get("symbol"))
        allowed = {
            "lastPrice",
            "indexPrice",
            "markPrice",
            "openInterest",
            "openInterestValue",
            "fundingRate",
            "nextFundingTime",
            "bid1Price",
            "bid1Size",
            "ask1Price",
            "ask1Size",
        }
        return [
            _envelope(
                raw,
                event_type="ticker",
                exchange_event_time_ms=int(payload["ts"]),
                matching_time_ms=None,
                source_sequence=int(payload["cs"]),
                source=topic,
                data={key: str(value) for key, value in data.items() if key in allowed},
            )
        ]
    if topic == f"allLiquidation.{SYMBOL}":
        events = []
        for item in payload["data"]:
            _require_symbol(item.get("s"))
            raw_side = str(item["S"])
            liquidated_side = {"Buy": "LONG", "Sell": "SHORT"}[raw_side]
            events.append(
                _envelope(
                    raw,
                    event_type="liquidation",
                    exchange_event_time_ms=int(payload["ts"]),
                    matching_time_ms=int(item["T"]),
                    source_sequence=None,
                    source=topic,
                    data={
                        "raw_side": raw_side,
                        "liquidated_position_side": liquidated_side,
                        "price": str(item["p"]),
                        "quantity": str(item["v"]),
                    },
                )
            )
        return events
    raise ValueError(f"unsupported Bybit topic: {topic}")


def normalize_record(raw: dict[str, Any]) -> list[dict[str, Any]]:
    venue = raw.get("venue")
    if venue == "binance":
        return _normalize_binance(raw)
    if venue == "bybit":
        return _normalize_bybit(raw)
    raise ValueError(f"unsupported venue: {venue!r}")


def _raw_files(run_dir: Path) -> Iterable[Path]:
    return sorted(path for path in run_dir.glob("*.jsonl") if path.name != "normalized.jsonl")


def _event_stream(
    path: Path,
    counters: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line in source:
            counters["input_records"] += 1
            try:
                raw = json.loads(line)
                events = normalize_record(raw)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                counters["rejected"] += 1
                counters["rejection_types"][type(exc).__name__] += 1
                continue
            yield from events


def normalize_capture(run_dir: Path) -> Path:
    counts: Counter[str] = Counter()
    output_events = 0
    counters: dict[str, Any] = {
        "input_records": 0,
        "rejected": 0,
        "rejection_types": Counter(),
    }
    output_path = run_dir / "normalized.jsonl"
    raw_files = list(_raw_files(run_dir))
    streams = [_event_stream(path, counters) for path in raw_files]

    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for event in heapq.merge(*streams, key=lambda item: int(item["receipt_time_ns"])):
            output.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
            counts[event["event_type"]] += 1
            output_events += 1

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "instrument": INSTRUMENT,
        "input_files": [path.name for path in raw_files],
        "ordering": "stable_kway_merge_by_receipt_time_ns",
        "input_records": counters["input_records"],
        "output_events": output_events,
        "event_counts": dict(sorted(counts.items())),
        "rejected_records": counters["rejected"],
        "rejection_types": dict(sorted(counters["rejection_types"].items())),
        "output_file": output_path.name,
    }
    manifest_path = run_dir / "normalization_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    manifest_path = normalize_capture(args.run_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(json.dumps({"manifest": str(manifest_path), **manifest}, sort_keys=True))
    return 0 if manifest["rejected_records"] == 0 and manifest["output_events"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
