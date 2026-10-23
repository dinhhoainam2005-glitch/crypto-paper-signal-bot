from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from .decision_support import cluster_performance


V21_GATE_ID = "CORE4_V21_PRODUCTION_READINESS"
MIN_OBSERVATION_DAYS = 90.0
MIN_CLOSED_CLUSTERS = 20
MIN_PROFIT_FACTOR = 1.30
MIN_WIN_RATE = 0.52
MIN_MEAN_CLUSTER_R = 0.15
MAX_DRAWDOWN = -0.05
MAX_PROFIT_CONCENTRATION = 0.50
MIN_TRUSTED_SOURCE_RATE = 0.999
MIN_HEALTHY_SCAN_RATE = 0.999
MAX_DATA_GAP_SCAN_RATE = 0.001
INCIDENT_LOOKBACK_DAYS = 30


def _parse_utc(value: Any, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return fallback


def _safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _cluster_drawdown(items: list[dict[str, Any]]) -> float:
    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for item in sorted(items, key=lambda row: str(row.get("entry_time_utc", ""))):
        equity += float(item.get("net_pnl", 0.0))
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0 if peak > 0.0 else -1.0
        maximum_drawdown = min(maximum_drawdown, drawdown)
    return maximum_drawdown


def _profit_concentration(trades: list[dict[str, Any]]) -> float:
    positive_by_symbol: dict[str, float] = {}
    for trade in trades:
        pnl = max(0.0, float(trade.get("net_pnl", 0.0)))
        symbol = str(trade.get("symbol", "UNKNOWN"))
        positive_by_symbol[symbol] = positive_by_symbol.get(symbol, 0.0) + pnl
    gross_profit = sum(positive_by_symbol.values())
    if gross_profit <= 0.0:
        return 1.0
    return max(positive_by_symbol.values(), default=0.0) / gross_profit


def ensure_v21_telemetry(state: dict[str, Any], now_utc: str) -> dict[str, Any]:
    telemetry = state.setdefault(
        "v21_telemetry",
        {
            "gate_id": V21_GATE_ID,
            "started_utc": now_utc,
            "scan_samples": 0,
            "healthy_scans": 0,
            "trusted_source_scans": 0,
            "data_gap_scans": 0,
            "critical_error_scans": 0,
            "incidents": [],
        },
    )
    telemetry.setdefault("gate_id", V21_GATE_ID)
    telemetry.setdefault("started_utc", now_utc)
    for key in (
        "scan_samples",
        "healthy_scans",
        "trusted_source_scans",
        "data_gap_scans",
        "critical_error_scans",
    ):
        telemetry.setdefault(key, 0)
    telemetry.setdefault("incidents", [])
    return telemetry


def update_v21_telemetry(
    state: dict[str, Any], scan: dict[str, Any], now_utc: str
) -> dict[str, Any]:
    telemetry = ensure_v21_telemetry(state, now_utc)
    telemetry["scan_samples"] = int(telemetry["scan_samples"]) + 1
    healthy = scan.get("status") == "OK"
    trusted = len(scan.get("groups", [])) == 4 and all(
        group.get("source_trusted") is True for group in scan.get("groups", [])
    )
    v20_positions = state.get("shadow_v20", {}).get("active_positions", [])
    data_gap = any(position.get("status") == "DATA_GAP" for position in v20_positions)
    critical_errors = [
        str(error)
        for error in scan.get("errors", [])
        if "V20" in str(error) or "source" in str(error).lower()
    ]
    if healthy:
        telemetry["healthy_scans"] = int(telemetry["healthy_scans"]) + 1
    if trusted:
        telemetry["trusted_source_scans"] = int(telemetry["trusted_source_scans"]) + 1
    if data_gap:
        telemetry["data_gap_scans"] = int(telemetry["data_gap_scans"]) + 1
    if critical_errors:
        telemetry["critical_error_scans"] = int(telemetry["critical_error_scans"]) + 1
    incidents = telemetry.setdefault("incidents", [])
    if not healthy or not trusted or data_gap or critical_errors:
        incident_types = []
        if not healthy:
            incident_types.append("DEGRADED_SCAN")
        if not trusted:
            incident_types.append("UNTRUSTED_SOURCE")
        if data_gap:
            incident_types.append("V20_DATA_GAP")
        if critical_errors:
            incident_types.append("V20_RUNTIME_ERROR")
        incidents.append(
            {
                "time_utc": now_utc,
                "types": incident_types,
                "errors": critical_errors[:3],
            }
        )
        telemetry["incidents"] = incidents[-1000:]
    telemetry["last_updated_utc"] = now_utc
    return telemetry


def production_readiness(
    state: dict[str, Any], now_utc: str | None = None
) -> dict[str, Any]:
    now = _parse_utc(now_utc, datetime.now(timezone.utc)) if now_utc else datetime.now(timezone.utc)
    v20 = state.get("shadow_v20", {})
    created = _parse_utc(
        v20.get("created_utc") or state.get("v21_telemetry", {}).get("started_utc"),
        now,
    )
    observation_days = max(0.0, (now - created).total_seconds() / 86_400.0)
    metrics = cluster_performance(v20)
    trades = v20.get("closed_trades", [])
    maximum_drawdown = _cluster_drawdown(metrics["items"])
    concentration = _profit_concentration(trades)
    telemetry = ensure_v21_telemetry(state, now.isoformat())
    samples = int(telemetry.get("scan_samples", 0))
    healthy_rate = _safe_rate(int(telemetry.get("healthy_scans", 0)), samples)
    trusted_rate = _safe_rate(int(telemetry.get("trusted_source_scans", 0)), samples)
    data_gap_rate = _safe_rate(int(telemetry.get("data_gap_scans", 0)), samples)
    cutoff = now - timedelta(days=INCIDENT_LOOKBACK_DAYS)
    recent_critical_incidents = sum(
        any(kind in {"V20_DATA_GAP", "V20_RUNTIME_ERROR"} for kind in item.get("types", []))
        and _parse_utc(item.get("time_utc"), now) >= cutoff
        for item in telemetry.get("incidents", [])
    )
    pf = float(metrics["profit_factor"])
    checks = {
        "observation_days": {
            "passed": observation_days >= MIN_OBSERVATION_DAYS,
            "value": observation_days,
            "minimum": MIN_OBSERVATION_DAYS,
        },
        "closed_clusters": {
            "passed": int(metrics["clusters"]) >= MIN_CLOSED_CLUSTERS,
            "value": int(metrics["clusters"]),
            "minimum": MIN_CLOSED_CLUSTERS,
        },
        "profit_factor": {
            "passed": pf >= MIN_PROFIT_FACTOR,
            "value": pf,
            "minimum": MIN_PROFIT_FACTOR,
        },
        "win_rate": {
            "passed": float(metrics["win_rate"]) >= MIN_WIN_RATE,
            "value": float(metrics["win_rate"]),
            "minimum": MIN_WIN_RATE,
        },
        "mean_cluster_r": {
            "passed": float(metrics["mean_cluster_r"]) >= MIN_MEAN_CLUSTER_R,
            "value": float(metrics["mean_cluster_r"]),
            "minimum": MIN_MEAN_CLUSTER_R,
        },
        "maximum_drawdown": {
            "passed": maximum_drawdown >= MAX_DRAWDOWN,
            "value": maximum_drawdown,
            "minimum": MAX_DRAWDOWN,
        },
        "profit_concentration": {
            "passed": concentration <= MAX_PROFIT_CONCENTRATION and bool(trades),
            "value": concentration,
            "maximum": MAX_PROFIT_CONCENTRATION,
        },
        "trusted_source_rate": {
            "passed": trusted_rate >= MIN_TRUSTED_SOURCE_RATE and samples > 0,
            "value": trusted_rate,
            "minimum": MIN_TRUSTED_SOURCE_RATE,
        },
        "healthy_scan_rate": {
            "passed": healthy_rate >= MIN_HEALTHY_SCAN_RATE and samples > 0,
            "value": healthy_rate,
            "minimum": MIN_HEALTHY_SCAN_RATE,
        },
        "data_gap_scan_rate": {
            "passed": data_gap_rate <= MAX_DATA_GAP_SCAN_RATE and samples > 0,
            "value": data_gap_rate,
            "maximum": MAX_DATA_GAP_SCAN_RATE,
        },
        "critical_incidents_30d": {
            "passed": recent_critical_incidents == 0 and samples > 0,
            "value": recent_critical_incidents,
            "maximum": 0,
        },
    }
    passed_checks = sum(bool(check["passed"]) for check in checks.values())
    gate_passed = passed_checks == len(checks)
    if gate_passed:
        status = "PAPER_GATE_PASS_MANUAL_APPROVAL_REQUIRED"
    elif observation_days < MIN_OBSERVATION_DAYS or metrics["clusters"] < MIN_CLOSED_CLUSTERS:
        status = "LOCKED_INSUFFICIENT_FORWARD_EVIDENCE"
    else:
        status = "LOCKED_QUALITY_OR_OPERATIONAL_GATE"
    signal_statuses: dict[str, int] = {}
    for signal in v20.get("signals", []):
        key = str(signal.get("status", "UNKNOWN"))
        signal_statuses[key] = signal_statuses.get(key, 0) + 1
    return {
        "gate_id": V21_GATE_ID,
        "status": status,
        "gate_passed": gate_passed,
        "manual_approval_required": True,
        "real_money_authorized": False,
        "passed_checks": passed_checks,
        "total_checks": len(checks),
        "checks": checks,
        "forward": {
            **metrics,
            "observation_days": observation_days,
            "maximum_drawdown": maximum_drawdown,
            "profit_concentration": concentration,
        },
        "operations": {
            "scan_samples": samples,
            "healthy_scan_rate": healthy_rate,
            "trusted_source_rate": trusted_rate,
            "data_gap_scan_rate": data_gap_rate,
            "critical_incidents_30d": recent_critical_incidents,
            "signal_statuses": signal_statuses,
        },
        "updated_utc": now.isoformat(),
    }


def update_production_readiness(
    state: dict[str, Any], scan: dict[str, Any], now_utc: str
) -> dict[str, Any]:
    update_v21_telemetry(state, scan, now_utc)
    result = production_readiness(state, now_utc)
    state["production_readiness_v21"] = result
    return result
