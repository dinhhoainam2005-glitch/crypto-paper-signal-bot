from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


READINESS_RANK = {
    "WAIT": 0,
    "WATCH": 1,
    "NEAR": 2,
    "TESTING": 3,
}

CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")

# Frozen results from the exact 1h execution and event-time funding audit.
HISTORICAL_REFERENCE = {
    "period": "2021-01-01/2026-08-31",
    "trades": 156,
    "frequency_basis": "PER_COIN_AVERAGE",
    "trades_per_week": 0.132,
    "trades_per_coin_per_week": 0.132,
    "portfolio_trades_per_week": 0.528,
    "symbol_count": len(CORE_SYMBOLS),
    "win_rate": 0.6153846153846154,
    "profit_factor": 1.8829853639540033,
    "sharpe": 1.0064208993716734,
    "max_drawdown": -0.03148676581053067,
}

# Diagnostic only: simultaneous signals were not promoted to a new strategy.
CROSS_MARKET_DIAGNOSTIC = {
    "clusters": 26,
    "cluster_win_rate": 0.7307692307692307,
    "cluster_profit_factor": 3.8738679297017184,
    "status": "HISTORICAL_DIAGNOSTIC_FORWARD_UNPROVEN",
}


def readiness_band(distance_pct: float) -> str:
    if distance_pct <= 0.0:
        return "TESTING"
    if distance_pct <= 3.0:
        return "NEAR"
    if distance_pct <= 10.0:
        return "WATCH"
    return "WAIT"


def annotate_market_readiness(
    groups: list[dict[str, Any]],
    btc_completed: list[Any],
    btc_current: Any | None,
) -> None:
    """Add a projected end-of-day playbook without creating trade signals."""
    if btc_current is None or len(btc_completed) < 199:
        return

    projected_sma200 = (
        sum(float(candle.close) for candle in btc_completed[-199:])
        + float(btc_current.close)
    ) / 200.0
    projected_btc_close = float(btc_current.close)
    allowed_side = "LONG" if projected_btc_close > projected_sma200 else "SHORT"

    for group in groups:
        current = group.get("current_price")
        if current is None:
            continue
        if allowed_side == "LONG":
            trigger = group.get("long_breakout_trigger")
            distance = group.get("distance_to_long_breakout_pct")
        else:
            trigger = group.get("short_breakout_trigger")
            distance = group.get("distance_to_short_breakout_pct")
        if trigger is None or distance is None:
            continue
        group.update(
            projected_regime_side=allowed_side,
            projected_btc_close=projected_btc_close,
            projected_btc_sma200=projected_sma200,
            projected_regime_confirmed=False,
            actionable_side=allowed_side,
            actionable_trigger=float(trigger),
            actionable_distance_pct=float(distance),
            readiness_band=readiness_band(float(distance)),
            next_decision_utc=datetime.fromtimestamp(
                int(btc_current.close_time_ms) / 1000.0,
                tz=timezone.utc,
            ).isoformat(),
        )


def update_readiness_state(
    state: dict[str, Any],
    groups: list[dict[str, Any]],
    now_ms: int,
) -> dict[str, Any] | None:
    current = {
        str(group["symbol"]): {
            "side": group.get("actionable_side"),
            "band": group.get("readiness_band"),
            "distance_pct": group.get("actionable_distance_pct"),
            "trigger": group.get("actionable_trigger"),
            "current_price": group.get("current_price"),
            "next_decision_utc": group.get("next_decision_utc"),
        }
        for group in groups
        if group.get("readiness_band") in READINESS_RANK
    }
    if not current:
        return None

    previous = state.get("market_readiness")
    state["market_readiness"] = current
    if not isinstance(previous, dict) or not previous:
        return None

    changed: list[dict[str, Any]] = []
    regime_changed = False
    for symbol, item in current.items():
        old = previous.get(symbol, {})
        old_side = old.get("side")
        new_side = item.get("side")
        if old_side and new_side and old_side != new_side:
            regime_changed = True
            changed.append({"symbol": symbol, **item, "change": "REGIME"})
            continue
        old_rank = READINESS_RANK.get(str(old.get("band")), -1)
        new_rank = READINESS_RANK.get(str(item.get("band")), -1)
        if new_rank >= READINESS_RANK["WATCH"] and new_rank > old_rank:
            changed.append({"symbol": symbol, **item, "change": "CLOSER"})

    if not changed:
        return None
    event_key = ":".join(
        f"{item['symbol']}-{item['side']}-{item['band']}" for item in changed
    )
    return {
        "event_id": f"CORE4V7:READINESS:{now_ms // 86_400_000}:{event_key}",
        "reason": "READINESS_ALERT",
        "time_utc": datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).isoformat(),
        "regime_changed": regime_changed,
        "markets": changed,
        "paper_only": True,
    }


def cluster_performance(state: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = state.get("closed_trades", [])
    for trade in trades:
        buckets[str(trade.get("entry_time_utc", trade.get("signal_id", "unknown")))].append(
            trade
        )

    clusters: list[dict[str, Any]] = []
    for entry_time, members in buckets.items():
        pnl = sum(float(trade.get("net_pnl", 0.0)) for trade in members)
        risk = sum(float(trade.get("initial_risk_cash", 0.0)) for trade in members)
        clusters.append(
            {
                "entry_time_utc": entry_time,
                "symbols": sorted(str(trade.get("symbol", "")) for trade in members),
                "trade_count": len(members),
                "net_pnl": pnl,
                "realized_r": pnl / risk if risk > 0.0 else 0.0,
            }
        )

    gross_profit = sum(max(cluster["net_pnl"], 0.0) for cluster in clusters)
    gross_loss = -sum(min(cluster["net_pnl"], 0.0) for cluster in clusters)
    profit_factor = gross_profit / gross_loss if gross_loss > 0.0 else (
        math.inf if gross_profit > 0.0 else 0.0
    )
    wins = sum(cluster["net_pnl"] > 0.0 for cluster in clusters)
    count = len(clusters)
    mean_r = sum(cluster["realized_r"] for cluster in clusters) / count if count else 0.0
    return {
        "trades": len(trades),
        "clusters": count,
        "multi_symbol_clusters": sum(cluster["trade_count"] >= 2 for cluster in clusters),
        "wins": wins,
        "win_rate": wins / count if count else 0.0,
        "profit_factor": profit_factor,
        "mean_cluster_r": mean_r,
        "items": sorted(clusters, key=lambda item: item["entry_time_utc"]),
    }


def per_coin_frequency(
    state: dict[str, Any], as_of_utc: str | None = None
) -> dict[str, Any]:
    """Return trade frequency with one coin as the primary unit of measure."""
    if as_of_utc:
        as_of = datetime.fromisoformat(as_of_utc.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    else:
        as_of = datetime.now(timezone.utc)
    created = datetime.fromisoformat(
        str(state.get("created_utc", as_of.isoformat())).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    observation_days = max(0.0, (as_of - created).total_seconds() / 86_400.0)
    observation_weeks = max(observation_days / 7.0, 1.0 / 168.0)

    trades = [
        *state.get("closed_trades", []),
        *state.get("active_positions", []),
    ]
    counts = {
        symbol: sum(str(trade.get("symbol")) == symbol for trade in trades)
        for symbol in CORE_SYMBOLS
    }
    by_symbol = {
        symbol: counts[symbol] / observation_weeks for symbol in CORE_SYMBOLS
    }
    cluster_keys = {
        str(trade.get("entry_time_utc", trade.get("signal_id", "unknown")))[:10]
        for trade in trades
    }
    return {
        "frequency_basis": "PER_COIN",
        "observation_days": observation_days,
        "trades": len(trades),
        "trades_per_coin_per_week": (
            len(trades) / (len(CORE_SYMBOLS) * observation_weeks)
        ),
        "trades_per_week_by_symbol": by_symbol,
        "portfolio_trades_per_week": len(trades) / observation_weeks,
        "clusters": len(cluster_keys),
        "clusters_per_week": len(cluster_keys) / observation_weeks,
    }


def forward_assessment(state: dict[str, Any]) -> dict[str, Any]:
    metrics = cluster_performance(state)
    created = datetime.fromisoformat(
        str(state.get("created_utc", datetime.now(timezone.utc).isoformat())).replace(
            "Z", "+00:00"
        )
    ).astimezone(timezone.utc)
    observation_days = max(
        0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86_400.0
    )
    enough_sample = metrics["clusters"] >= 20
    weak_now = (
        metrics["clusters"] > 0
        and (
            metrics["profit_factor"] < 1.0
            or float(state.get("equity", 1.0)) < 0.995
        )
    )
    if not enough_sample and weak_now:
        status = "INSUFFICIENT_SAMPLE_CURRENTLY_WEAK"
    elif not enough_sample:
        status = "INSUFFICIENT_SAMPLE"
    elif metrics["profit_factor"] >= 1.30 and metrics["mean_cluster_r"] > 0.0:
        status = "PRELIMINARY_PASS"
    else:
        status = "BELOW_FORWARD_GATE"
    return {
        **metrics,
        "observation_days": observation_days,
        "minimum_clusters_for_preliminary_read": 20,
        "status": status,
    }


def decision_support_payload(
    scan: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "generated_utc": scan.get("time_utc"),
        "strategy_id": state.get("strategy_id", "CORE4_V7_BETA_REGIME_DONCHIAN"),
        "paper_only": True,
        "historical_reference": HISTORICAL_REFERENCE,
        "cross_market_diagnostic": CROSS_MARKET_DIAGNOSTIC,
        "forward": forward_assessment(state),
        "forward_frequency": per_coin_frequency(state, scan.get("time_utc")),
        "market_playbook": [
            {
                key: group.get(key)
                for key in (
                    "symbol",
                    "status",
                    "current_price",
                    "actionable_side",
                    "actionable_trigger",
                    "actionable_distance_pct",
                    "readiness_band",
                    "projected_btc_close",
                    "projected_btc_sma200",
                    "projected_regime_confirmed",
                    "next_decision_utc",
                    "data_source",
                    "source_trusted",
                )
            }
            for group in scan.get("groups", [])
        ],
    }
