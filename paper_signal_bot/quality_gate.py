from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any


GATE_ID = "R30A_FORWARD_TRADE_A_PLUS_GATE"
WATCH_TIER = "WATCH"
TRADE_A_PLUS_TIER = "TRADE_A_PLUS"

# This switch is deliberately code-owned. Historical research alone cannot unlock
# real-money eligibility; an untouched forward review must change it explicitly.
RESEARCH_PROMOTION_APPROVED = False

DEFAULT_POLICY: dict[str, Any] = {
    "min_forward_days": 90.0,
    "min_closed_trades": 150,
    "min_win_rate": 0.60,
    "target_win_rate": 0.70,
    "min_profit_factor_12bps": 1.50,
    "min_profit_factor_20bps": 1.20,
    "min_sharpe_12bps": 1.20,
    "max_drawdown_pct": 10.0,
    "min_probability_positive": 0.80,
    "min_long_trades": 30,
    "min_short_trades": 30,
    "min_trades_per_timeframe": 30,
    "min_cell_trades": 20,
    "min_cell_win_rate": 0.55,
    "min_cell_profit_factor_12bps": 1.30,
    "min_cell_profit_factor_20bps": 1.10,
    "required_timeframes": ("1h", "4h"),
    "max_symbol_profit_contribution": 0.50,
    "min_signal_delivery_rate": 0.99,
    "min_scan_coverage": 0.90,
    "require_durable_state": True,
    "assumed_scan_interval_seconds": 60,
    "bootstrap_iterations": 1000,
}


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _closed_trades(state: dict[str, Any]) -> list[dict[str, Any]]:
    trades = [
        item
        for item in state.get("signals", [])
        if item.get("status") == "PAPER_CLOSED"
        and isinstance(item.get("net_return_12bps"), (int, float))
        and isinstance(item.get("net_return_20bps"), (int, float))
    ]
    return sorted(trades, key=lambda item: int(item.get("actual_exit_time_ms", 0)))


def _profit_factor(returns: list[float]) -> float:
    gross_win = sum(value for value in returns if value > 0.0)
    gross_loss = abs(sum(value for value in returns if value < 0.0))
    if gross_loss > 0.0:
        return gross_win / gross_loss
    return 999.0 if gross_win > 0.0 else 0.0


def _max_drawdown_pct(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= max(1.0 + value, 0.000001)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst * 100.0


def _annualized_trade_sharpe(returns: list[float], trades_per_week: float) -> float:
    if len(returns) < 2:
        return 0.0
    std = pstdev(returns)
    if std <= 0.0:
        return 0.0
    return fmean(returns) / std * math.sqrt(max(trades_per_week * 52.0, 1.0))


def _block_bootstrap_probability_positive(returns: list[float], iterations: int) -> float:
    if not returns:
        return 0.0
    if len(returns) == 1:
        return 1.0 if returns[0] > 0.0 else 0.0
    rng = random.Random(20260910)
    block_size = max(2, int(math.sqrt(len(returns))))
    positive = 0
    for _ in range(max(iterations, 1)):
        sample: list[float] = []
        while len(sample) < len(returns):
            start = rng.randrange(len(returns))
            sample.extend(returns[(start + offset) % len(returns)] for offset in range(block_size))
        if fmean(sample[: len(returns)]) > 0.0:
            positive += 1
    return positive / max(iterations, 1)


def _symbol_profit_contribution(trades: list[dict[str, Any]]) -> float:
    by_symbol: dict[str, float] = defaultdict(float)
    for item in trades:
        by_symbol[str(item.get("symbol", "UNKNOWN"))] += float(item["net_return_12bps"])
    total = sum(by_symbol.values())
    if total <= 0.0:
        return 1.0
    return max((max(value, 0.0) / total for value in by_symbol.values()), default=1.0)


def _cell_key(item: dict[str, Any]) -> str:
    return "|".join(
        (
            str(item.get("symbol", "UNKNOWN")),
            str(item.get("timeframe", "UNKNOWN")),
            str(item.get("side", "UNKNOWN")).upper(),
        )
    )


def _cell_summaries(trades: list[dict[str, Any]], rules: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in trades:
        grouped[_cell_key(item)].append(item)
    summaries: dict[str, dict[str, Any]] = {}
    for key, items in sorted(grouped.items()):
        returns_12 = [float(item["net_return_12bps"]) for item in items]
        returns_20 = [float(item["net_return_20bps"]) for item in items]
        win_rate = sum(value > 0.0 for value in returns_12) / len(items)
        pf12 = _profit_factor(returns_12)
        pf20 = _profit_factor(returns_20)
        eligible = (
            len(items) >= int(rules["min_cell_trades"])
            and win_rate >= float(rules["min_cell_win_rate"])
            and pf12 >= float(rules["min_cell_profit_factor_12bps"])
            and pf20 >= float(rules["min_cell_profit_factor_20bps"])
        )
        summaries[key] = {
            "closed_trades": len(items),
            "win_rate": round(win_rate, 6),
            "profit_factor_12bps": round(pf12, 6),
            "profit_factor_20bps": round(pf20, 6),
            "max_drawdown_pct": round(_max_drawdown_pct(returns_12), 6),
            "eligible": eligible,
        }
    return summaries


def _gate(name: str, passed: bool, actual: Any, requirement: str, reason_vi: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "requirement": requirement,
        "reason_vi": reason_vi,
    }


def evaluate_trade_readiness(
    state: dict[str, Any],
    *,
    at_ms: int,
    policy: dict[str, Any] | None = None,
    research_approved: bool = RESEARCH_PROMOTION_APPROVED,
) -> dict[str, Any]:
    rules = {**DEFAULT_POLICY, **(policy or {})}
    trades = _closed_trades(state)
    returns_12 = [float(item["net_return_12bps"]) for item in trades]
    returns_20 = [float(item["net_return_20bps"]) for item in trades]
    started = _parse_utc(state.get("forward_evidence_started_utc") or state.get("created_utc"))
    at = datetime.fromtimestamp(at_ms / 1000.0, tz=timezone.utc)
    forward_days = max((at - started).total_seconds() / 86400.0, 0.0) if started else 0.0
    observed_days = max(forward_days, 1.0 / 24.0)
    trades_per_week = len(trades) / observed_days * 7.0
    wins = sum(value > 0.0 for value in returns_12)
    win_rate = wins / len(trades) if trades else 0.0
    direction_counts = Counter(str(item.get("side", "UNKNOWN")).upper() for item in trades)
    timeframe_counts = Counter(str(item.get("timeframe", "UNKNOWN")) for item in trades)

    delivered = [item for item in state.get("signals", []) if item.get("delivery_status")]
    delivery_rate = (
        sum(item.get("delivery_status") == "SENT" for item in delivered) / len(delivered)
        if delivered
        else 0.0
    )
    expected_scans = max(
        forward_days * 86400.0 / float(rules["assumed_scan_interval_seconds"]),
        1.0,
    )
    scan_coverage = min(float(state.get("scan_count", 0)) / expected_scans, 1.0)
    pf12 = _profit_factor(returns_12)
    pf20 = _profit_factor(returns_20)
    sharpe12 = _annualized_trade_sharpe(returns_12, trades_per_week)
    drawdown_pct = _max_drawdown_pct(returns_12)
    probability_positive = _block_bootstrap_probability_positive(
        returns_12,
        int(rules["bootstrap_iterations"]),
    )
    symbol_contribution = _symbol_profit_contribution(trades)
    cell_metrics = _cell_summaries(trades, rules)

    gates = [
        _gate(
            "durable_state",
            not rules["require_durable_state"] or bool(state.get("durable_state_configured")),
            bool(state.get("durable_state_configured")),
            "persistent state path configured",
            "Sổ lệnh forward chưa được lưu trên bộ nhớ bền vững.",
        ),
        _gate(
            "research_promotion",
            research_approved,
            research_approved,
            "untouched forward research approved in code",
            "Nghiên cứu forward độc lập chưa được phê duyệt.",
        ),
        _gate(
            "forward_days",
            forward_days >= float(rules["min_forward_days"]),
            round(forward_days, 3),
            f">= {rules['min_forward_days']} days",
            "Chưa đủ số ngày kiểm chứng forward.",
        ),
        _gate(
            "closed_trades",
            len(trades) >= int(rules["min_closed_trades"]),
            len(trades),
            f">= {rules['min_closed_trades']}",
            "Chưa đủ số lệnh paper đã đóng.",
        ),
        _gate(
            "win_rate",
            win_rate >= float(rules["min_win_rate"]),
            round(win_rate, 6),
            f">= {rules['min_win_rate']:.0%}; target {rules['target_win_rate']:.0%}",
            "Tỷ lệ thắng forward chưa đạt ngưỡng.",
        ),
        _gate(
            "profit_factor_12bps",
            pf12 >= float(rules["min_profit_factor_12bps"]),
            round(pf12, 6),
            f">= {rules['min_profit_factor_12bps']}",
            "Profit Factor sau 12 bps chi phí chưa đạt.",
        ),
        _gate(
            "profit_factor_20bps",
            pf20 >= float(rules["min_profit_factor_20bps"]),
            round(pf20, 6),
            f">= {rules['min_profit_factor_20bps']}",
            "Stress Profit Factor sau 20 bps chi phí chưa đạt.",
        ),
        _gate(
            "sharpe_12bps",
            sharpe12 >= float(rules["min_sharpe_12bps"]),
            round(sharpe12, 6),
            f">= {rules['min_sharpe_12bps']}",
            "Sharpe forward chưa đạt ngưỡng.",
        ),
        _gate(
            "max_drawdown",
            drawdown_pct >= -float(rules["max_drawdown_pct"]),
            round(drawdown_pct, 6),
            f">= -{rules['max_drawdown_pct']}%",
            "Drawdown forward vượt giới hạn.",
        ),
        _gate(
            "probability_positive",
            probability_positive >= float(rules["min_probability_positive"]),
            round(probability_positive, 6),
            f">= {rules['min_probability_positive']:.0%}",
            "Xác suất bootstrap có kỳ vọng dương chưa đạt 80%.",
        ),
        _gate(
            "long_coverage",
            direction_counts.get("LONG", 0) >= int(rules["min_long_trades"]),
            direction_counts.get("LONG", 0),
            f">= {rules['min_long_trades']}",
            "Chưa đủ mẫu LONG.",
        ),
        _gate(
            "short_coverage",
            direction_counts.get("SHORT", 0) >= int(rules["min_short_trades"]),
            direction_counts.get("SHORT", 0),
            f">= {rules['min_short_trades']}",
            "Chưa đủ mẫu SHORT.",
        ),
        *[
            _gate(
                f"timeframe_{timeframe}_coverage",
                timeframe_counts.get(timeframe, 0) >= int(rules["min_trades_per_timeframe"]),
                timeframe_counts.get(timeframe, 0),
                f">= {rules['min_trades_per_timeframe']}",
                f"Chưa đủ mẫu khung {timeframe}.",
            )
            for timeframe in rules["required_timeframes"]
        ],
        _gate(
            "symbol_concentration",
            symbol_contribution <= float(rules["max_symbol_profit_contribution"]),
            round(symbol_contribution, 6),
            f"<= {rules['max_symbol_profit_contribution']:.0%}",
            "Lợi nhuận đang phụ thuộc quá nhiều vào một symbol.",
        ),
        _gate(
            "delivery_rate",
            delivery_rate >= float(rules["min_signal_delivery_rate"]),
            round(delivery_rate, 6),
            f">= {rules['min_signal_delivery_rate']:.0%}",
            "Tỷ lệ gửi tín hiệu thành công chưa đạt.",
        ),
        _gate(
            "scan_coverage",
            scan_coverage >= float(rules["min_scan_coverage"]),
            round(scan_coverage, 6),
            f">= {rules['min_scan_coverage']:.0%}",
            "Độ phủ quét liên tục chưa đạt.",
        ),
    ]
    passed = all(item["passed"] for item in gates)
    failed = [item["reason_vi"] for item in gates if not item["passed"]]
    return {
        "gate_id": GATE_ID,
        "status": "TRADE_A_PLUS_ELIGIBLE" if passed else "WATCH_ONLY",
        "trade_a_plus_eligible": passed,
        "paper_only": True,
        "evaluated_utc": at.isoformat(),
        "policy": rules,
        "metrics": {
            "forward_days": round(forward_days, 3),
            "closed_trades": len(trades),
            "trades_per_week": round(trades_per_week, 6),
            "win_rate": round(win_rate, 6),
            "profit_factor_12bps": round(pf12, 6),
            "profit_factor_20bps": round(pf20, 6),
            "sharpe_12bps": round(sharpe12, 6),
            "max_drawdown_pct": round(drawdown_pct, 6),
            "probability_positive": round(probability_positive, 6),
            "long_trades": direction_counts.get("LONG", 0),
            "short_trades": direction_counts.get("SHORT", 0),
            "timeframe_counts": dict(timeframe_counts),
            "max_symbol_profit_contribution": round(symbol_contribution, 6),
            "signal_delivery_rate": round(delivery_rate, 6),
            "scan_coverage": round(scan_coverage, 6),
            "durable_state_configured": bool(state.get("durable_state_configured")),
        },
        "cell_metrics": cell_metrics,
        "eligible_cells": [key for key, value in cell_metrics.items() if value["eligible"]],
        "gates": gates,
        "failed_reasons_vi": failed,
    }


def classify_signal(readiness: dict[str, Any], signal: dict[str, Any] | None = None) -> str:
    if not readiness.get("trade_a_plus_eligible"):
        return WATCH_TIER
    if signal is None:
        return TRADE_A_PLUS_TIER
    return TRADE_A_PLUS_TIER if _cell_key(signal) in readiness.get("eligible_cells", []) else WATCH_TIER
