from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from typing import Any

from ..signal_contract import TradePlan
from .clients import BinanceClient, Candle, SOURCE_USDM_FUTURES
from .config import RuntimeConfig, load_locked_spec, spec_sha256
from .state import ForwardStore, now_iso


DAY_MS = 86_400_000
MINUTE_MS = 60_000
COST_RATE = 0.0020
ENTRY_CHANNEL = 55
EXIT_CHANNEL = 20
SMA_DAYS = 200
ATR_DAYS = 20
ATR_MULTIPLIER = 2.0
TP_R = (1.0, 2.0, 4.0)
TP_FRACTIONS = (0.20, 0.30, 0.50)
MAX_HOLD_HOURS = 2160
RISK_PER_TRADE = 0.0035
MAX_SYMBOL_NOTIONAL = 0.20
MAX_INITIAL_GROSS = 0.60


def utc_iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc).isoformat()


def true_ranges(candles: list[Candle]) -> list[float]:
    output: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        candidates = [candle.high - candle.low]
        if previous_close is not None:
            candidates.extend([abs(candle.high - previous_close), abs(candle.low - previous_close)])
        output.append(max(candidates))
        previous_close = candle.close
    return output


def completed_and_current(candles: list[Candle], now_ms: int) -> tuple[list[Candle], Candle | None]:
    completed = [candle for candle in candles if candle.close_time_ms <= now_ms]
    current = next(
        (
            candle
            for candle in reversed(candles)
            if candle.open_time_ms <= now_ms < candle.close_time_ms
        ),
        None,
    )
    return completed, current


def evaluate_candidate(
    symbol: str,
    daily_by_symbol: dict[str, list[Candle]],
    now_ms: int,
) -> dict[str, Any] | None:
    completed, current = completed_and_current(daily_by_symbol[symbol], now_ms)
    btc_completed, _ = completed_and_current(daily_by_symbol["BTCUSDT"], now_ms)
    if len(completed) < max(ENTRY_CHANNEL + 1, ATR_DAYS + 1) or len(btc_completed) < SMA_DAYS:
        return None
    decision = completed[-1]
    if current is None or current.open_time_ms != decision.open_time_ms + DAY_MS:
        return None
    prior = completed[-(ENTRY_CHANNEL + 1) : -1]
    entry_high = max(candle.high for candle in prior)
    entry_low = min(candle.low for candle in prior)
    side = None
    if decision.close > entry_high:
        side = "LONG"
    elif decision.close < entry_low:
        side = "SHORT"
    if side is None:
        return None
    btc_sma = sum(candle.close for candle in btc_completed[-SMA_DAYS:]) / SMA_DAYS
    btc_close = btc_completed[-1].close
    if side == "LONG" and not btc_close > btc_sma:
        return None
    if side == "SHORT" and not btc_close < btc_sma:
        return None
    atr = sum(true_ranges(completed)[-ATR_DAYS:]) / ATR_DAYS
    distance = ATR_MULTIPLIER * atr
    entry = current.open
    sign = 1.0 if side == "LONG" else -1.0
    if not math.isfinite(distance) or distance <= 0.0 or distance >= entry:
        return None
    targets = tuple(entry + sign * multiple * distance for multiple in TP_R)
    if min(targets) <= 0.0:
        return None
    generated = datetime.fromtimestamp(current.open_time_ms / 1000.0, tz=timezone.utc)
    plan = TradePlan(
        symbol=symbol,
        side=side,
        timeframe="1d",
        generated_at_utc=generated,
        entry=entry,
        stop_loss=entry - sign * distance,
        take_profit_1=targets[0],
        take_profit_2=targets[1],
        take_profit_3=targets[2],
        invalid_after_utc=datetime.fromtimestamp(
            (current.open_time_ms + DAY_MS) / 1000.0, tz=timezone.utc
        ),
        max_holding_hours=MAX_HOLD_HOURS,
        review_interval_hours=24,
        capital_at_risk_fraction=RISK_PER_TRADE,
        early_exit_condition="Dong cua ngay cat kenh Donchian 20 ngay nguoc huong",
        paper_only=True,
    )
    plan.validate()
    risk_fraction = distance / entry
    return {
        "signal_id": f"CORE4V7:{symbol}:{side}:{decision.open_time_ms}",
        "strategy_id": "CORE4_V7_BETA_REGIME_DONCHIAN",
        "signal_time_utc": utc_iso(decision.close_time_ms),
        "signal_close_ms": decision.close_time_ms,
        "entry_time_ms": current.open_time_ms,
        "entry_time_utc": utc_iso(current.open_time_ms),
        "entry_high": entry_high,
        "entry_low": entry_low,
        "btc_close": btc_close,
        "btc_sma200": btc_sma,
        "atr20": atr,
        "notional_fraction_requested": min(MAX_SYMBOL_NOTIONAL, RISK_PER_TRADE / risk_fraction),
        "plan": plan.as_payload(),
        "status": "CANDIDATE",
        "paper_only": True,
    }


def opposite_channel_exit(
    position: dict[str, Any], daily: list[Candle], decision_open_ms: int
) -> bool:
    index_by_time = {candle.open_time_ms: index for index, candle in enumerate(daily)}
    index = index_by_time.get(decision_open_ms)
    if index is None or index < EXIT_CHANNEL:
        return False
    decision = daily[index]
    prior = daily[index - EXIT_CHANNEL : index]
    if position["side"] == "LONG":
        return decision.close < min(candle.low for candle in prior)
    return decision.close > max(candle.high for candle in prior)


def new_position(signal: dict[str, Any], equity: float, notional_fraction: float) -> dict[str, Any]:
    plan = signal["plan"]
    notional = equity * notional_fraction
    quantity = notional / float(plan["entry"])
    risk_cash = quantity * abs(float(plan["entry"]) - float(plan["stop_loss"]))
    return {
        "signal_id": signal["signal_id"],
        "symbol": plan["symbol"],
        "side": plan["side"],
        "entry_time_ms": signal["entry_time_ms"],
        "entry_time_utc": signal["entry_time_utc"],
        "entry": float(plan["entry"]),
        "initial_stop": float(plan["stop_loss"]),
        "current_stop": float(plan["stop_loss"]),
        "targets": [float(plan["take_profit_1"]), float(plan["take_profit_2"]), float(plan["take_profit_3"])],
        "next_target_index": 0,
        "tp_fractions": list(TP_FRACTIONS),
        "initial_quantity": quantity,
        "remaining_quantity": quantity,
        "initial_notional": notional,
        "initial_risk_cash": risk_cash,
        "entry_fee": notional * COST_RATE,
        "exit_fees": 0.0,
        "price_pnl": 0.0,
        "funding_pnl": 0.0,
        "events": [],
        "last_processed_minute_ms": None,
        "last_funding_time_ms": signal["entry_time_ms"] - 1,
        "max_exit_time_ms": signal["entry_time_ms"] + MAX_HOLD_HOURS * 3_600_000,
        "paper_only": True,
        "status": "OPEN",
    }


def _direction(position: dict[str, Any]) -> float:
    return 1.0 if position["side"] == "LONG" else -1.0


def _fill(
    state: dict[str, Any],
    position: dict[str, Any],
    quantity: float,
    price: float,
    reason: str,
    time_ms: int,
) -> dict[str, Any]:
    quantity = min(quantity, float(position["remaining_quantity"]))
    fee = quantity * price * COST_RATE
    price_pnl = _direction(position) * quantity * (price - float(position["entry"]))
    position["remaining_quantity"] -= quantity
    position["exit_fees"] += fee
    position["price_pnl"] += price_pnl
    state["cash"] += price_pnl - fee
    event = {
        "event_id": f"{position['signal_id']}:{reason}:{time_ms}",
        "signal_id": position["signal_id"],
        "symbol": position["symbol"],
        "side": position["side"],
        "reason": reason,
        "time_ms": time_ms,
        "time_utc": utc_iso(time_ms),
        "price": price,
        "quantity": quantity,
        "remaining_fraction": max(0.0, position["remaining_quantity"] / position["initial_quantity"]),
        "paper_only": True,
    }
    position["events"].append(event)
    state.setdefault("events", []).append(event)
    return event


def _move_stop(position: dict[str, Any]) -> None:
    if position["next_target_index"] == 1:
        position["current_stop"] = position["entry"]
    elif position["next_target_index"] == 2:
        position["current_stop"] = position["targets"][0]


def _close_trade(state: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    final_event = position["events"][-1]
    net_pnl = (
        position["price_pnl"]
        + position["funding_pnl"]
        - position["entry_fee"]
        - position["exit_fees"]
    )
    trade = {
        **{key: position[key] for key in ("signal_id", "symbol", "side", "entry_time_utc", "entry", "initial_stop", "initial_notional", "initial_risk_cash", "paper_only")},
        "tp1": position["targets"][0],
        "tp2": position["targets"][1],
        "tp3": position["targets"][2],
        "exit_time_utc": final_event["time_utc"],
        "exit_price": final_event["price"],
        "exit_reason": "+".join(dict.fromkeys(event["reason"] for event in position["events"])),
        "holding_hours": (final_event["time_ms"] - position["entry_time_ms"]) / 3_600_000.0,
        "price_pnl": position["price_pnl"],
        "funding_pnl": position["funding_pnl"],
        "fees": position["entry_fee"] + position["exit_fees"],
        "net_pnl": net_pnl,
        "realized_r": net_pnl / position["initial_risk_cash"],
        "status": "CLOSED",
    }
    state.setdefault("closed_trades", []).append(trade)
    for signal in state.get("signals", []):
        if signal.get("signal_id") == position["signal_id"]:
            signal.update(status="CLOSED", closed_trade=trade)
            break
    return trade


def process_position_path(
    state: dict[str, Any],
    position: dict[str, Any],
    minutes: list[Candle],
    daily: list[Candle],
    funding_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    notifications: list[dict[str, Any]] = []
    funding_by_minute: dict[int, list[dict[str, Any]]] = {}
    for row in funding_rows:
        funding_by_minute.setdefault(int(row["fundingTime"]) // MINUTE_MS * MINUTE_MS, []).append(row)
    for candle in minutes:
        if position["remaining_quantity"] <= 1e-12:
            break
        timestamp = candle.open_time_ms
        if timestamp % DAY_MS == 0 and timestamp > position["entry_time_ms"]:
            decision_open = timestamp - DAY_MS
            reason = None
            if timestamp >= position["max_exit_time_ms"]:
                reason = "MAX_HOLD"
            elif opposite_channel_exit(position, daily, decision_open):
                reason = "CHANNEL_EXIT"
            if reason is not None:
                notifications.append(
                    _fill(state, position, position["remaining_quantity"], candle.open, reason, timestamp)
                )
                position["last_processed_minute_ms"] = timestamp
                break

        adverse_open = (
            candle.open <= position["current_stop"]
            if position["side"] == "LONG"
            else candle.open >= position["current_stop"]
        )
        if adverse_open:
            notifications.append(
                _fill(state, position, position["remaining_quantity"], candle.open, "STOP_GAP", timestamp)
            )
            position["last_processed_minute_ms"] = timestamp
            break
        while position["next_target_index"] < 3:
            index = position["next_target_index"]
            target = position["targets"][index]
            favorable_open = candle.open >= target if position["side"] == "LONG" else candle.open <= target
            if not favorable_open:
                break
            notifications.append(
                _fill(
                    state,
                    position,
                    position["initial_quantity"] * position["tp_fractions"][index],
                    target,
                    f"TP{index + 1}_GAP",
                    timestamp,
                )
            )
            position["next_target_index"] += 1
            _move_stop(position)

        for funding in funding_by_minute.get(timestamp, []):
            funding_time = int(funding["fundingTime"])
            if funding_time <= int(position["last_funding_time_ms"]):
                continue
            mark = float(funding.get("markPrice") or candle.open)
            rate = float(funding["fundingRate"])
            funding_pnl = -_direction(position) * position["remaining_quantity"] * mark * rate
            position["funding_pnl"] += funding_pnl
            state["cash"] += funding_pnl
            position["last_funding_time_ms"] = funding_time

        stop_hit = (
            candle.low <= position["current_stop"]
            if position["side"] == "LONG"
            else candle.high >= position["current_stop"]
        )
        if stop_hit:
            notifications.append(
                _fill(
                    state,
                    position,
                    position["remaining_quantity"],
                    position["current_stop"],
                    "STOP",
                    timestamp,
                )
            )
        else:
            while position["next_target_index"] < 3:
                index = position["next_target_index"]
                target = position["targets"][index]
                hit = candle.high >= target if position["side"] == "LONG" else candle.low <= target
                if not hit:
                    break
                notifications.append(
                    _fill(
                        state,
                        position,
                        position["initial_quantity"] * position["tp_fractions"][index],
                        target,
                        f"TP{index + 1}",
                        timestamp,
                    )
                )
                position["next_target_index"] += 1
                _move_stop(position)
        position["last_processed_minute_ms"] = timestamp

    trade = None
    if position["remaining_quantity"] <= 1e-12:
        trade = _close_trade(state, position)
    return notifications, trade


class ForwardEngine:
    def __init__(
        self,
        client: BinanceClient | None = None,
        store: ForwardStore | None = None,
        config: RuntimeConfig | None = None,
    ) -> None:
        self.spec = load_locked_spec()
        self.config = config or RuntimeConfig.from_env()
        self.client = client or BinanceClient()
        self.store = store or ForwardStore(self.config.state_path, spec_sha256())
        self.lock = threading.RLock()
        self.symbols = tuple(self.spec["universe"])

    def _daily_candles(self, symbol: str) -> tuple[list[Candle], str]:
        loader = getattr(self.client, "daily_candles_with_source", None)
        if loader is None:
            return self.client.daily_candles(symbol), SOURCE_USDM_FUTURES
        return loader(symbol)

    def _minute_candles(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> tuple[list[Candle], str]:
        loader = getattr(self.client, "minute_candles_with_source", None)
        if loader is None:
            return (
                self.client.minute_candles(
                    symbol, start_ms, end_ms, self.config.max_replay_minutes
                ),
                SOURCE_USDM_FUTURES,
            )
        return loader(symbol, start_ms, end_ms, self.config.max_replay_minutes)

    def _replay_position(
        self,
        state: dict[str, Any],
        position: dict[str, Any],
        daily: list[Candle],
        now_ms: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        start_ms = (
            position["entry_time_ms"]
            if position["last_processed_minute_ms"] is None
            else int(position["last_processed_minute_ms"]) + MINUTE_MS
        )
        end_ms = now_ms - 1
        if end_ms < start_ms:
            return [], None
        minutes, minute_source = self._minute_candles(
            position["symbol"], start_ms, end_ms
        )
        if (
            self.config.require_futures_for_signals
            and minute_source != SOURCE_USDM_FUTURES
        ):
            raise RuntimeError(f"untrusted minute data source: {minute_source}")
        minutes = [candle for candle in minutes if candle.close_time_ms <= now_ms]
        funding = self.client.funding_rates(
            position["symbol"], int(position["last_funding_time_ms"]) + 1, now_ms
        )
        return process_position_path(state, position, minutes, daily, funding)

    def scan(self, now_ms: int | None = None) -> dict[str, Any]:
        with self.lock:
            now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
            state = self.store.load()
            daily_by_symbol: dict[str, list[Candle]] = {}
            daily_sources: dict[str, str] = {}
            groups: list[dict[str, Any]] = []
            notifications: list[dict[str, Any]] = []
            errors: list[str] = []
            for symbol in self.symbols:
                try:
                    candles, source = self._daily_candles(symbol)
                    daily_by_symbol[symbol] = candles
                    daily_sources[symbol] = source
                    completed, current = completed_and_current(daily_by_symbol[symbol], now_ms)
                    ready = len(completed) >= SMA_DAYS and current is not None
                    source_trusted = source == SOURCE_USDM_FUTURES
                    group_status = "READY" if ready else "DATA_NOT_READY"
                    if ready and self.config.require_futures_for_signals and not source_trusted:
                        group_status = "SOURCE_MISMATCH"
                    groups.append(
                        {
                            "symbol": symbol,
                            "status": group_status,
                            "completed_bars": len(completed),
                            "latest_close_utc": utc_iso(completed[-1].close_time_ms) if completed else None,
                            "data_source": source,
                            "source_trusted": source_trusted,
                        }
                    )
                except Exception as exc:
                    errors.append(f"{symbol} daily: {type(exc).__name__}: {exc}")
                    groups.append({"symbol": symbol, "status": "ERROR", "completed_bars": 0})
            if len(daily_by_symbol) != len(self.symbols):
                state["last_scan_utc"] = utc_iso(now_ms)
                state["scan_count"] = int(state.get("scan_count", 0)) + 1
                state["errors"] = (state.get("errors", []) + [{"time_utc": now_iso(), "message": item} for item in errors])[-50:]
                scan = {
                    "status": "DEGRADED",
                    "time_utc": utc_iso(now_ms),
                    "groups": groups,
                    "new_notifications": 0,
                    "active_positions": len(state.get("active_positions", [])),
                    "closed_trades": len(state.get("closed_trades", [])),
                    "equity": state.get("equity", 1.0),
                    "errors": errors,
                    "source_mismatches": [
                        group["symbol"]
                        for group in groups
                        if group.get("status") == "SOURCE_MISMATCH"
                    ],
                    "require_futures_for_signals": self.config.require_futures_for_signals,
                }
                state["last_scan"] = scan
                self.store.save(state)
                return {**scan, "notifications": [], "state": state}

            active_after_replay: list[dict[str, Any]] = []
            for position in state.get("active_positions", []):
                try:
                    position_source = daily_sources[position["symbol"]]
                    if (
                        self.config.require_futures_for_signals
                        and position_source != SOURCE_USDM_FUTURES
                    ):
                        raise RuntimeError(
                            f"untrusted daily data source: {position_source}"
                        )
                    events, trade = self._replay_position(
                        state, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is None:
                        active_after_replay.append(position)
                    else:
                        notifications.append({"event_id": f"{position['signal_id']}:CLOSED", "reason": "TRADE_CLOSED", "trade": trade})
                except Exception as exc:
                    errors.append(f"{position['symbol']} replay: {type(exc).__name__}: {exc}")
                    position["status"] = "DATA_GAP"
                    active_after_replay.append(position)
            state["active_positions"] = active_after_replay

            active_symbols = {position["symbol"] for position in active_after_replay}
            existing_signal_ids = {signal["signal_id"] for signal in state.get("signals", [])}
            pending: list[dict[str, Any]] = []
            for symbol in self.symbols:
                if symbol in active_symbols:
                    continue
                candidate = evaluate_candidate(symbol, daily_by_symbol, now_ms)
                if candidate is None or candidate["signal_id"] in existing_signal_ids:
                    continue
                _, current = completed_and_current(daily_by_symbol[symbol], now_ms)
                assert current is not None
                candidate["current_price"] = current.close
                candidate["detected_at_utc"] = utc_iso(now_ms)
                candidate["entry_window_seconds"] = self.config.max_entry_lag_seconds
                candidate["max_chase_bps"] = self.config.max_chase_bps
                candidate["data_sources"] = {
                    symbol: daily_sources[symbol],
                    "BTCUSDT": daily_sources["BTCUSDT"],
                }
                candidate["source_gate_passed"] = all(
                    source == SOURCE_USDM_FUTURES
                    for source in candidate["data_sources"].values()
                )
                lag_seconds = max(0.0, (now_ms - candidate["entry_time_ms"]) / 1000.0)
                direction = 1.0 if candidate["plan"]["side"] == "LONG" else -1.0
                chase_bps = direction * (current.close / candidate["plan"]["entry"] - 1.0) * 10_000.0
                candidate["entry_lag_seconds"] = lag_seconds
                candidate["directional_chase_bps"] = chase_bps
                if (
                    self.config.require_futures_for_signals
                    and not candidate["source_gate_passed"]
                ):
                    candidate["status"] = "SUPPRESSED_SOURCE_MISMATCH"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                elif errors:
                    candidate["status"] = "SUPPRESSED_DEGRADED"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                elif lag_seconds > self.config.max_entry_lag_seconds:
                    candidate["status"] = "SUPPRESSED_STALE"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                elif chase_bps > self.config.max_chase_bps:
                    candidate["status"] = "SUPPRESSED_CHASE"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                else:
                    pending.append(candidate)
                existing_signal_ids.add(candidate["signal_id"])

            marks = {
                symbol: completed_and_current(daily_by_symbol[symbol], now_ms)[1].close
                for symbol in self.symbols
                if completed_and_current(daily_by_symbol[symbol], now_ms)[1] is not None
            }
            unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in state["active_positions"]
                if position["symbol"] in marks
            )
            equity = float(state["cash"]) + unrealized
            existing_gross = sum(
                position["remaining_quantity"] * marks[position["symbol"]]
                for position in state["active_positions"]
                if position["symbol"] in marks
            ) / equity
            requested = sum(signal["notional_fraction_requested"] for signal in pending)
            available = max(0.0, MAX_INITIAL_GROSS - existing_gross)
            scale = min(1.0, available / requested) if requested > 0.0 else 0.0
            for signal in pending:
                allocated = signal["notional_fraction_requested"] * scale
                signal["notional_fraction_allocated"] = allocated
                if allocated <= 0.0:
                    signal["status"] = "SUPPRESSED_CAPACITY"
                    state.setdefault("signals", []).append(signal)
                    notifications.append(signal)
                    continue
                signal["status"] = "OPEN"
                position = new_position(signal, equity, allocated)
                state["cash"] -= position["entry_fee"]
                state.setdefault("signals", []).append(signal)
                state["active_positions"].append(position)
                notifications.append(signal)
                try:
                    events, trade = self._replay_position(
                        state, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is not None:
                        state["active_positions"].remove(position)
                        notifications.append({"event_id": f"{position['signal_id']}:CLOSED", "reason": "TRADE_CLOSED", "trade": trade})
                except Exception as exc:
                    errors.append(f"{position['symbol']} initial replay: {type(exc).__name__}: {exc}")
                    position["status"] = "DATA_GAP"

            unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in state["active_positions"]
                if position["symbol"] in marks
            )
            state["equity"] = float(state["cash"]) + unrealized
            state["last_scan_utc"] = utc_iso(now_ms)
            state["scan_count"] = int(state.get("scan_count", 0)) + 1
            state["signals"] = state.get("signals", [])[-500:]
            state["closed_trades"] = state.get("closed_trades", [])[-500:]
            state["events"] = state.get("events", [])[-1000:]
            if errors:
                state["errors"] = (state.get("errors", []) + [{"time_utc": now_iso(), "message": item} for item in errors])[-50:]
            source_mismatches = [
                group["symbol"]
                for group in groups
                if group.get("status") == "SOURCE_MISMATCH"
            ]
            status = "OK" if not errors and not source_mismatches else "DEGRADED"
            scan = {
                "status": status,
                "time_utc": utc_iso(now_ms),
                "groups": groups,
                "new_notifications": len(notifications),
                "active_positions": len(state["active_positions"]),
                "closed_trades": len(state["closed_trades"]),
                "equity": state["equity"],
                "errors": errors,
                "source_mismatches": source_mismatches,
                "require_futures_for_signals": self.config.require_futures_for_signals,
            }
            state["last_scan"] = scan
            self.store.save(state)
            return {**scan, "notifications": notifications, "state": state}
