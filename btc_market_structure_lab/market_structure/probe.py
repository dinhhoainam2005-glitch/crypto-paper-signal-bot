"""Finite public-data provenance probe for Binance and Bybit BTC perpetuals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import SYMBOL


@dataclass(frozen=True)
class Endpoint:
    venue: str
    name: str
    base_url: str
    path: str
    params: dict[str, str | int]

    @property
    def url(self) -> str:
        query = urlencode(self.params)
        return f"{self.base_url}{self.path}" + (f"?{query}" if query else "")


ENDPOINTS = (
    Endpoint("binance", "server_time", "https://fapi.binance.com", "/fapi/v1/time", {}),
    Endpoint(
        "binance",
        "book",
        "https://fapi.binance.com",
        "/fapi/v1/depth",
        {"symbol": SYMBOL, "limit": 20},
    ),
    Endpoint(
        "binance",
        "mark_oi_funding",
        "https://fapi.binance.com",
        "/fapi/v1/premiumIndex",
        {"symbol": SYMBOL},
    ),
    Endpoint(
        "binance",
        "open_interest",
        "https://fapi.binance.com",
        "/fapi/v1/openInterest",
        {"symbol": SYMBOL},
    ),
    Endpoint("bybit", "server_time", "https://api.bybit.com", "/v5/market/time", {}),
    Endpoint(
        "bybit",
        "instrument",
        "https://api.bybit.com",
        "/v5/market/instruments-info",
        {"category": "linear", "symbol": SYMBOL},
    ),
    Endpoint(
        "bybit",
        "book",
        "https://api.bybit.com",
        "/v5/market/orderbook",
        {"category": "linear", "symbol": SYMBOL, "limit": 50},
    ),
    Endpoint(
        "bybit",
        "ticker",
        "https://api.bybit.com",
        "/v5/market/tickers",
        {"category": "linear", "symbol": SYMBOL},
    ),
)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _positive_number(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def validate_payload(endpoint: Endpoint, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate only invariants needed before any research can begin."""

    if endpoint.venue == "binance" and endpoint.name == "server_time":
        return {"exchange_time_ms": int(payload["serverTime"])}

    if endpoint.venue == "binance" and endpoint.name == "book":
        bid = _positive_number(payload["bids"][0][0], "binance bid")
        ask = _positive_number(payload["asks"][0][0], "binance ask")
        if bid >= ask:
            raise ValueError("binance best bid must be below best ask")
        return {
            "best_bid": bid,
            "best_ask": ask,
            "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10_000,
            "update_id": int(payload["lastUpdateId"]),
        }

    if endpoint.venue == "binance" and endpoint.name in {"mark_oi_funding", "open_interest"}:
        if payload.get("symbol") != SYMBOL:
            raise ValueError("binance returned an unexpected symbol")
        field = "markPrice" if endpoint.name == "mark_oi_funding" else "openInterest"
        return {field: _positive_number(payload[field], f"binance {field}")}

    if endpoint.venue == "bybit":
        if int(payload.get("retCode", -1)) != 0:
            raise ValueError(f"bybit retCode={payload.get('retCode')}")
        if endpoint.name == "server_time":
            return {"exchange_time_ms": int(payload["time"])}
        result = payload["result"]
        if endpoint.name == "book":
            if result.get("s") != SYMBOL:
                raise ValueError("bybit returned an unexpected book symbol")
            bid = _positive_number(result["b"][0][0], "bybit bid")
            ask = _positive_number(result["a"][0][0], "bybit ask")
            if bid >= ask:
                raise ValueError("bybit best bid must be below best ask")
            return {
                "best_bid": bid,
                "best_ask": ask,
                "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10_000,
                "exchange_time_ms": int(result["ts"]),
                "matching_engine_time_ms": int(result["cts"]),
                "update_id": int(result["u"]),
                "cross_sequence": int(result["seq"]),
            }
        records = result["list"]
        if len(records) != 1 or records[0].get("symbol") != SYMBOL:
            raise ValueError("bybit returned an unexpected instrument set")
        if endpoint.name == "ticker":
            return {
                "markPrice": _positive_number(records[0]["markPrice"], "bybit markPrice"),
                "openInterest": _positive_number(records[0]["openInterest"], "bybit openInterest"),
            }
        return {"status": records[0]["status"], "contractType": records[0]["contractType"]}

    raise ValueError(f"unsupported endpoint: {endpoint.venue}/{endpoint.name}")


def fetch(endpoint: Endpoint, raw_dir: Path, timeout: float) -> dict[str, Any]:
    started_ms = _now_ms()
    request = Request(endpoint.url, headers={"User-Agent": "btc-market-structure-lab/0.1"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        http_status = response.status
    received_ms = _now_ms()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")

    filename = f"{endpoint.venue}__{endpoint.name}.json"
    (raw_dir / filename).write_bytes(raw + b"\n")
    checks = validate_payload(endpoint, payload)
    exchange_time_ms = checks.get("exchange_time_ms")
    return {
        "venue": endpoint.venue,
        "endpoint": endpoint.name,
        "url": endpoint.url,
        "http_status": http_status,
        "started_ms": started_ms,
        "received_ms": received_ms,
        "latency_ms": received_ms - started_ms,
        "exchange_clock_offset_ms": (
            received_ms - int(exchange_time_ms) if exchange_time_ms is not None else None
        ),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw_file": filename,
        "checks": checks,
        "ok": True,
    }


def run_probe(output_root: Path, timeout: float = 10.0) -> Path:
    run_dir = output_root / _stamp()
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []

    for endpoint in ENDPOINTS:
        try:
            records.append(fetch(endpoint, raw_dir, timeout))
        except Exception as exc:  # The report must preserve partial failures.
            records.append(
                {
                    "venue": endpoint.venue,
                    "endpoint": endpoint.name,
                    "url": endpoint.url,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    report = {
        "schema_version": 1,
        "project": "btc-market-structure-lab",
        "symbol": SYMBOL,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "all_passed": all(record["ok"] for record in records),
        "passed": sum(record["ok"] for record in records),
        "total": len(records),
        "endpoints": records,
        "endpoint_contracts": [asdict(endpoint) for endpoint in ENDPOINTS],
    }
    report_path = run_dir / "probe_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/probes"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    report_path = run_probe(args.output_root, args.timeout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(json.dumps({"report": str(report_path), **{k: report[k] for k in ("all_passed", "passed", "total")}}))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
