"""Frozen H001 liquidity-sweep candidate detector without outcome labels."""

from __future__ import annotations

import argparse
import bisect
import heapq
import json
import math
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

IMPULSE_SECONDS = 10
REJECTION_SECONDS = 5
COOLDOWN_SECONDS = 120
BASELINE_SECONDS = 7 * 24 * 60 * 60
MIN_BASELINE_SECONDS = 24 * 60 * 60
TAIL_QUANTILE = Decimal("0.995")
MIN_RETRACE = Decimal("0.35")
MAX_FLOW_RATE_RATIO = Decimal("0.60")
MAX_BOOK_AGE_MS = 2000
ASYMMETRIC_LEAD_RATIO = Decimal("1.25")
NUMERICAL_FLOOR_BPS = Decimal("0.01")
TEN_THOUSAND = Decimal("10000")


class RollingQuantile:
    """Exact nearest-rank rolling quantile with lazy heap deletion."""

    def __init__(self, quantile: Decimal, maxlen: int) -> None:
        if not (Decimal("0") < quantile <= Decimal("1")):
            raise ValueError("quantile must be in (0, 1]")
        self.quantile = quantile
        self.maxlen = maxlen
        self.lower: list[tuple[Decimal, int]] = []  # max heap via negative values
        self.upper: list[tuple[Decimal, int]] = []
        self.location: dict[int, str] = {}
        self.deleted: set[int] = set()
        self.order: deque[tuple[int, Decimal]] = deque()
        self.next_id = 0
        self.lower_size = 0
        self.upper_size = 0

    def __len__(self) -> int:
        return self.lower_size + self.upper_size

    def _target_lower(self) -> int:
        return math.ceil(float(self.quantile) * len(self)) if len(self) else 0

    def _prune_lower(self) -> None:
        while self.lower and self.lower[0][1] in self.deleted:
            _, item_id = heapq.heappop(self.lower)
            self.deleted.discard(item_id)

    def _prune_upper(self) -> None:
        while self.upper and self.upper[0][1] in self.deleted:
            _, item_id = heapq.heappop(self.upper)
            self.deleted.discard(item_id)

    def _rebalance(self) -> None:
        self._prune_lower()
        self._prune_upper()
        target = self._target_lower()
        while self.lower_size > target:
            self._prune_lower()
            negative, item_id = heapq.heappop(self.lower)
            value = -negative
            heapq.heappush(self.upper, (value, item_id))
            self.location[item_id] = "upper"
            self.lower_size -= 1
            self.upper_size += 1
        while self.lower_size < target:
            self._prune_upper()
            value, item_id = heapq.heappop(self.upper)
            heapq.heappush(self.lower, (-value, item_id))
            self.location[item_id] = "lower"
            self.upper_size -= 1
            self.lower_size += 1
        self._prune_lower()
        self._prune_upper()

    def add(self, value: Decimal) -> None:
        item_id = self.next_id
        self.next_id += 1
        self._prune_lower()
        if not self.lower or value <= -self.lower[0][0]:
            heapq.heappush(self.lower, (-value, item_id))
            self.location[item_id] = "lower"
            self.lower_size += 1
        else:
            heapq.heappush(self.upper, (value, item_id))
            self.location[item_id] = "upper"
            self.upper_size += 1
        self.order.append((item_id, value))
        if len(self.order) > self.maxlen:
            expired_id, _ = self.order.popleft()
            location = self.location.pop(expired_id)
            self.deleted.add(expired_id)
            if location == "lower":
                self.lower_size -= 1
            else:
                self.upper_size -= 1
        self._rebalance()

    def value(self) -> Decimal:
        if not self.lower_size:
            raise ValueError("rolling quantile is empty")
        self._prune_lower()
        return -self.lower[0][0]


@dataclass(frozen=True)
class PendingCandidate:
    candidate_index: int
    decision_index: int
    impulse_sign: int
    impulse_start_mid: Decimal
    extreme_mid: Decimal
    impulse_size: Decimal
    impulse_flow: Decimal
    threshold_bps: Decimal
    impulse_bps: Decimal


def _mid(frame: dict[str, Any], venue: str | None = None) -> Decimal:
    if venue is None:
        return Decimal(frame["consolidated_mid"])
    return Decimal(frame["venues"][venue]["mid"])


def _net_flow(frame: dict[str, Any]) -> Decimal:
    return sum(
        (Decimal(frame["venues"][venue]["net_aggressive_notional"]) for venue in ("binance", "bybit")),
        Decimal("0"),
    )


def _eligible_quality(frames: list[dict[str, Any]]) -> bool:
    return all(
        frame["venues"][venue]["book_age_ms"] is not None
        and int(frame["venues"][venue]["book_age_ms"]) <= MAX_BOOK_AGE_MS
        for frame in frames
        for venue in ("binance", "bybit")
    )


def _evaluate_pending(
    pending: PendingCandidate,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate = frames[pending.candidate_index]
    decision = frames[pending.decision_index]
    rejection_frames = frames[pending.candidate_index + 1 : pending.decision_index + 1]
    decision_mid = _mid(decision)
    if pending.impulse_sign > 0:
        retrace = (pending.extreme_mid - decision_mid) / pending.impulse_size
        research_direction = "DOWN"
    else:
        retrace = (decision_mid - pending.extreme_mid) / pending.impulse_size
        research_direction = "UP"
    rejection_flow = sum((_net_flow(frame) for frame in rejection_frames), Decimal("0"))
    impulse_rate = abs(pending.impulse_flow) / Decimal(IMPULSE_SECONDS)
    rejection_rate = abs(rejection_flow) / Decimal(REJECTION_SECONDS)
    flow_flipped = pending.impulse_flow * rejection_flow < 0
    flow_exhausted = flow_flipped or rejection_rate <= MAX_FLOW_RATE_RATIO * impulse_rate
    quality_ok = _eligible_quality(
        frames[pending.candidate_index - IMPULSE_SECONDS : pending.decision_index + 1]
    )

    reasons = []
    if retrace < MIN_RETRACE:
        reasons.append("INSUFFICIENT_RETRACE")
    if not flow_exhausted:
        reasons.append("FLOW_NOT_EXHAUSTED")
    if not quality_ok:
        reasons.append("STALE_BOOK")

    venue_returns = {}
    start_index = pending.candidate_index - IMPULSE_SECONDS
    for venue in ("binance", "bybit"):
        start = _mid(frames[start_index], venue)
        venue_returns[venue] = (_mid(candidate, venue) / start - 1) * TEN_THOUSAND
    trigger_venue = max(venue_returns, key=lambda venue: abs(venue_returns[venue]))
    other_venue = "bybit" if trigger_venue == "binance" else "binance"
    lead_ratio = abs(venue_returns[trigger_venue]) / max(
        abs(venue_returns[other_venue]), NUMERICAL_FLOOR_BPS
    )
    depth_change = {}
    for venue in ("binance", "bybit"):
        depth_change[venue] = Decimal(decision["venues"][venue]["imbalance_10bps"]) - Decimal(
            candidate["venues"][venue]["imbalance_10bps"]
        )

    return {
        "schema_version": 1,
        "hypothesis": "H001",
        "candidate_frame_end_ns": candidate["frame_end_ns"],
        "decision_frame_end_ns": decision["frame_end_ns"],
        "accepted": not reasons,
        "rejection_reasons": reasons,
        "impulse_direction": "UP" if pending.impulse_sign > 0 else "DOWN",
        "research_direction": research_direction,
        "impulse_bps": format(pending.impulse_bps, "f"),
        "causal_tail_threshold_bps": format(pending.threshold_bps, "f"),
        "retrace_fraction": format(retrace, "f"),
        "impulse_net_flow": format(pending.impulse_flow, "f"),
        "rejection_net_flow": format(rejection_flow, "f"),
        "flow_rate_ratio": (
            None if impulse_rate == 0 else format(rejection_rate / impulse_rate, "f")
        ),
        "flow_flipped": flow_flipped,
        "trigger_venue": trigger_venue,
        "lead_ratio": format(lead_ratio, "f"),
        "cross_venue_stratum": (
            "ASYMMETRIC" if lead_ratio >= ASYMMETRIC_LEAD_RATIO else "SYNCHRONOUS"
        ),
        "candidate_dislocation_bps": candidate["binance_minus_bybit_bps"],
        "decision_dislocation_bps": decision["binance_minus_bybit_bps"],
        "depth_imbalance_10bps_change": {
            venue: format(value, "f") for venue, value in depth_change.items()
        },
        "contains_outcomes": False,
        "contains_pnl": False,
    }


def detect(frames: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    quantile = RollingQuantile(TAIL_QUANTILE, BASELINE_SECONDS)
    candidates: list[dict[str, Any]] = []
    pending: PendingCandidate | None = None
    cooldown_until = -1
    previous_end_ns: int | None = None

    for index, frame in enumerate(frames):
        end_ns = int(frame["frame_end_ns"])
        if previous_end_ns is not None and end_ns != previous_end_ns + 1_000_000_000:
            raise ValueError("state frames must be consecutive one-second buckets")
        previous_end_ns = end_ns

        if pending is not None and index == pending.decision_index:
            candidates.append(_evaluate_pending(pending, frames))
            pending = None

        if index < IMPULSE_SECONDS:
            continue
        start_mid = _mid(frames[index - IMPULSE_SECONDS])
        current_mid = _mid(frame)
        impulse_bps = (current_mid / start_mid - 1) * TEN_THOUSAND
        absolute_bps = abs(impulse_bps)
        baseline_ready = len(quantile) >= MIN_BASELINE_SECONDS
        threshold = quantile.value() if baseline_ready else None

        if (
            pending is None
            and index >= cooldown_until
            and baseline_ready
            and threshold is not None
            and absolute_bps >= threshold
            and index + REJECTION_SECONDS < len(frames)
        ):
            impulse_window = frames[index - IMPULSE_SECONDS : index + 1]
            mids = [_mid(item) for item in impulse_window]
            sign = 1 if impulse_bps > 0 else -1
            at_extreme = current_mid == (max(mids) if sign > 0 else min(mids))
            impulse_flow = sum(
                (_net_flow(item) for item in frames[index - IMPULSE_SECONDS + 1 : index + 1]),
                Decimal("0"),
            )
            flow_aligned = impulse_flow * Decimal(sign) > 0
            quality_ok = _eligible_quality(impulse_window)
            if at_extreme and flow_aligned and quality_ok:
                extreme = current_mid
                size = abs(extreme - start_mid)
                if size > 0:
                    pending = PendingCandidate(
                        candidate_index=index,
                        decision_index=index + REJECTION_SECONDS,
                        impulse_sign=sign,
                        impulse_start_mid=start_mid,
                        extreme_mid=extreme,
                        impulse_size=size,
                        impulse_flow=impulse_flow,
                        threshold_bps=threshold,
                        impulse_bps=impulse_bps,
                    )
                    cooldown_until = index + COOLDOWN_SECONDS

        quantile.add(absolute_bps)

    manifest = {
        "schema_version": 1,
        "hypothesis": "H001",
        "frames": len(frames),
        "baseline_observations": len(quantile),
        "baseline_ready": len(quantile) >= MIN_BASELINE_SECONDS,
        "candidates": len(candidates),
        "accepted": sum(bool(item["accepted"]) for item in candidates),
        "rejected": sum(not bool(item["accepted"]) for item in candidates),
        "contains_outcomes": False,
        "contains_pnl": False,
        "parameters": {
            "impulse_seconds": IMPULSE_SECONDS,
            "rejection_seconds": REJECTION_SECONDS,
            "cooldown_seconds": COOLDOWN_SECONDS,
            "baseline_seconds": BASELINE_SECONDS,
            "minimum_baseline_seconds": MIN_BASELINE_SECONDS,
            "tail_quantile": str(TAIL_QUANTILE),
            "minimum_retrace": str(MIN_RETRACE),
            "maximum_flow_rate_ratio": str(MAX_FLOW_RATE_RATIO),
            "maximum_book_age_ms": MAX_BOOK_AGE_MS,
        },
    }
    return candidates, manifest


def run_file(state_frame_path: Path) -> Path:
    frames = [json.loads(line) for line in state_frame_path.read_text(encoding="utf-8").splitlines()]
    candidates, manifest = detect(frames)
    output = state_frame_path.with_name("h001_candidates.jsonl")
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate, separators=(",", ":"), sort_keys=True) + "\n")
    manifest["source"] = str(state_frame_path)
    manifest["output"] = output.name
    manifest_path = state_frame_path.with_name("h001_detector_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_frames", type=Path)
    args = parser.parse_args()
    manifest_path = run_file(args.state_frames)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(json.dumps({"manifest": str(manifest_path), **manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
