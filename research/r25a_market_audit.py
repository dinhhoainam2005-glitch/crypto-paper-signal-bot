from __future__ import annotations

import json
from bisect import bisect_right
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import fmean, pstdev

from paper_signal_bot.strategy import R24A_SCAN_MARKETS, SYMBOL_BY_ASSET, evaluate_latest, parse_kline
from paper_signal_bot.market_pulse import evaluate_market_pulses


BASE = "https://fapi.binance.com"
SYMBOLS = list(SYMBOL_BY_ASSET.values())
VN = timezone(timedelta(hours=7))
INTERVAL_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


def ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def vn_day(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(VN).strftime("%Y-%m-%d")


def get(path: str, params: dict[str, object]) -> object:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-paper-signal-bot-audit/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list[object]]:
    out: list[list[object]] = []
    cursor = start_ms
    step = INTERVAL_MS[interval]
    while cursor < end_ms:
        rows = get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1500})
        if not rows:
            break
        out.extend(rows)  # type: ignore[arg-type]
        last_open = int(rows[-1][0])  # type: ignore[index]
        next_cursor = last_open + step
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(rows) < 1500:  # type: ignore[arg-type]
            break
    seen = set()
    deduped = []
    for row in out:
        key = int(row[0])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def zscore(values: list[float], index: int, window: int = 20) -> float:
    if index < window:
        return 0.0
    prior = values[index - window : index]
    mean = fmean(prior)
    std = pstdev(prior)
    return 0.0 if std <= 0 else (values[index] - mean) / std


def day_summary(rows: list[list[object]], day: str) -> dict[str, object]:
    parsed = [parse_kline(row) for row in rows if vn_day(int(row[0])) == day]
    parsed.sort(key=lambda item: item["open_time"])
    if not parsed:
        return {"day": day, "bars": 0}
    hourly = []
    for i, row in enumerate(parsed):
        prev_close = parsed[i - 1]["close"] if i else row["open"]
        hourly.append((row["open_time"], row["close"] / prev_close - 1.0 if prev_close else 0.0))
    max_hour = max(hourly, key=lambda item: item[1])
    min_hour = min(hourly, key=lambda item: item[1])
    return {
        "day": day,
        "bars": len(parsed),
        "open": parsed[0]["open"],
        "high": max(row["high"] for row in parsed),
        "low": min(row["low"] for row in parsed),
        "close": parsed[-1]["close"],
        "day_return_pct": (parsed[-1]["close"] / parsed[0]["open"] - 1.0) * 100.0,
        "day_range_pct": (max(row["high"] for row in parsed) / min(row["low"] for row in parsed) - 1.0) * 100.0,
        "max_1h_return_pct": max_hour[1] * 100.0,
        "max_1h_time": iso(max_hour[0]),
        "min_1h_return_pct": min_hour[1] * 100.0,
        "min_1h_time": iso(min_hour[0]),
    }


def pulse_candidates(rows_by_key: dict[tuple[str, str], list[list[object]]], day: str) -> list[dict[str, object]]:
    thresholds = {
        "15m": {"BTCUSDT": 0.0045, "ETHUSDT": 0.0060, "SOLUSDT": 0.0080, "BNBUSDT": 0.0070},
        "1h": {"BTCUSDT": 0.0100, "ETHUSDT": 0.0120, "SOLUSDT": 0.0180, "BNBUSDT": 0.0150},
        "4h": {"BTCUSDT": 0.0200, "ETHUSDT": 0.0240, "SOLUSDT": 0.0350, "BNBUSDT": 0.0300},
    }
    events = []
    for interval, by_symbol in thresholds.items():
        parsed_by_symbol = {symbol: [parse_kline(row) for row in rows_by_key[(symbol, interval)]] for symbol in SYMBOLS}
        for symbol, parsed in parsed_by_symbol.items():
            quote_volumes = [row["quote_volume"] for row in parsed]
            for i, row in enumerate(parsed):
                if vn_day(row["open_time"]) != day or i == 0:
                    continue
                prev_close = parsed[i - 1]["close"]
                ret = row["close"] / prev_close - 1.0 if prev_close > 0 else 0.0
                threshold = by_symbol[symbol]
                if abs(ret) < threshold:
                    continue
                side = "LONG" if ret > 0 else "SHORT"
                breadth = 0
                assets = 0
                for other, other_rows in parsed_by_symbol.items():
                    located = next((idx for idx, other_row in enumerate(other_rows) if other_row["open_time"] == row["open_time"]), None)
                    if located is None or located == 0:
                        continue
                    other_prev = other_rows[located - 1]["close"]
                    other_ret = other_rows[located]["close"] / other_prev - 1.0 if other_prev > 0 else 0.0
                    assets += 1
                    if (other_ret >= threshold * 0.5 and side == "LONG") or (other_ret <= -threshold * 0.5 and side == "SHORT"):
                        breadth += 1
                volz = zscore(quote_volumes, i, 20)
                if volz < 0.25 and abs(ret) < threshold * 1.5:
                    continue
                if breadth < 2 and abs(ret) < threshold * 1.8:
                    continue
                events.append(
                    {
                        "day": day,
                        "symbol": symbol,
                        "timeframe": interval,
                        "side": side,
                        "open_time": iso(row["open_time"]),
                        "close_time": iso(row["open_time"] + INTERVAL_MS[interval]),
                        "return_pct": ret * 100.0,
                        "range_pct": (row["high"] / row["low"] - 1.0) * 100.0 if row["low"] else 0.0,
                        "volume_z20": volz,
                        "breadth": f"{breadth}/{assets}",
                    }
                )
    return sorted(events, key=lambda item: (item["day"], item["open_time"], abs(float(item["return_pct"]))), reverse=True)


def replay_r24a(rows_by_key: dict[tuple[str, str], list[list[object]]], day: str) -> list[dict[str, object]]:
    events = []
    seen = set()
    for symbol, interval in R24A_SCAN_MARKETS:
        rows = rows_by_key[(symbol, interval)]
        for raw in rows:
            open_time = int(raw[0])
            if vn_day(open_time) != day:
                continue
            now_at_entry = open_time + INTERVAL_MS[interval] + 1
            market = {market_symbol: rows_by_key[(market_symbol, interval)] for market_symbol in SYMBOLS}
            result = evaluate_latest(
                symbol=symbol,
                timeframe=interval,
                klines=rows,
                premium_klines=[],
                derivatives_state_available=True,
                market_klines_by_symbol=market,
                now_ms=now_at_entry,
            )
            for signal in result.get("signals", []):
                key = (symbol, interval, signal["candidate"]["candidate_id"], signal["signal_time_ms"])
                if key in seen:
                    continue
                seen.add(key)
                events.append(
                    {
                        "day": day,
                        "symbol": symbol,
                        "timeframe": interval,
                        "side": signal["side"],
                        "candidate": signal["candidate"]["candidate_id"],
                        "entry_time": iso(signal["entry_time_ms"]),
                        "entry_price": signal["entry_price"],
                    }
                )
    return events


def main() -> None:
    start = ms("2026-08-01T00:00:00Z")
    end = ms("2026-09-05T00:00:00Z")
    output = Path("data/r25a_audit")
    output.mkdir(parents=True, exist_ok=True)
    rows_by_key = {}
    keys = [(s, tf) for tf in INTERVAL_MS for s in SYMBOLS]
    def load_market(key):
        symbol, interval = key
        cache = output / f"{symbol}_{interval}_{start}_{end}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        rows = fetch_klines(symbol, interval, start, end)
        cache.write_text(json.dumps(rows), encoding="utf-8")
        return rows
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, rows in zip(keys, pool.map(load_market, keys)):
            rows_by_key[key] = rows
    print("Fetched 12 market histories", flush=True)
    audit_days = ("2026-09-03", "2026-09-04")
    pulse_events = []
    seen = set()
    opens = {key: [int(r[0]) for r in rows] for key, rows in rows_by_key.items()}
    first_close = ms("2026-09-03T00:00:00+07:00")
    last_close = ms("2026-09-05T00:00:00+07:00")
    # Replay the production detector with a 60-second scan delay and 4-event cap.
    for boundary in range(first_close, last_close, INTERVAL_MS["15m"]):
        for delay in (60000, 120000):
            at = boundary + delay
            cache = {}
            for key, rows in rows_by_key.items():
                stop = bisect_right(opens[key], at - INTERVAL_MS[key[1]])
                cache[key] = rows[max(0, stop-220):stop]
            evaluated = evaluate_market_pulses(cache, at)
            new = [e for e in evaluated["events"] if e["event_id"] not in seen][:4]
            pulse_events.extend(new)
            seen.update(e["event_id"] for e in new)
    payload = {
        "source": "https://fapi.binance.com/fapi/v1/klines",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "method": "VN calendar days. R24A raw rule eligibility only, not historical deliveries or executable fills. R25A uses production closed-candle detector, 60s scan delay, cap 4/scan. No profitability inference.",
        "history_bars": {f"{s} {tf}":len(rows) for (s,tf),rows in rows_by_key.items()},
        "daily_vn": {
            symbol: [day_summary(rows_by_key[(symbol, "1h")], day) for day in audit_days]
            for symbol in SYMBOLS
        },
        "r24a_replay": {
            day: replay_r24a(rows_by_key, day)
            for day in audit_days
        },
        "pulse_events": pulse_events,
        "pulse_counts_by_day_tf_side": dict(sorted(Counter(f"{vn_day(e['candle_close_time_ms'])} {e['timeframe']} {e['side']}" for e in pulse_events).items())),
    }
    (output / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    brief = {k:v for k,v in payload.items() if k != "pulse_events"}
    brief["first_btc_long_sep3"] = next((e for e in pulse_events if e["symbol"] == "BTCUSDT" and e["side"] == "LONG" and vn_day(e["candle_close_time_ms"]) == audit_days[0]), None)
    brief["first_btc_short_sep4"] = next((e for e in pulse_events if e["symbol"] == "BTCUSDT" and e["side"] == "SHORT" and vn_day(e["candle_close_time_ms"]) == audit_days[1]), None)
    print(json.dumps(brief, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
