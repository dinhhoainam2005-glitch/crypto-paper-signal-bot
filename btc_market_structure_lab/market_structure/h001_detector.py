"""Frozen H001 liquidity-sweep candidate detector without outcome labels."""

from __future__ import annotations

import argparse
import bisect
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
    """Exact nearest-rank rolling quantile in bounded, compact sorted blocks."""

    BLOCK_SIZE = 1_024

    def __init__(self, quantile: Decimal, maxlen: int) -> None:
        if not (Decimal("0") < quantile <= Decimal("1")):
            raise ValueError("quantile must be in (0, 1]")
        self.quantile = quantile
        self.maxlen = maxlen
        self.blocks: list[list[Decimal]] = []
        self.maxes: list[Decimal] = []
        self.order: deque[Decimal] = deque()
        self.size = 0

    def __len__(self) -> int:
        return self.size

    def _insert(self, value: Decimal) -> None:
        if not self.blocks:
            self.blocks.append([value])
            self.maxes.append(value)
            return
        block_index = bisect.bisect_left(self.maxes, value)
        if block_index == len(self.blocks):
            block_index -= 1
        block = self.blocks[block_index]
        bisect.insort_right(block, value)
        self.maxes[block_index] = block[-1]
        if len(block) > self.BLOCK_SIZE * 2:
            right = block[self.BLOCK_SIZE :]
            del block[self.BLOCK_SIZE :]
            self.blocks.insert(block_index + 1, right)
            self.maxes[block_index] = block[-1]
            self.maxes.insert(block_index + 1, right[-1])

    def _remove(self, value: Decimal) -> None:
        block_index = bisect.bisect_left(self.maxes, value)
        if block_index == len(self.blocks):
            raise RuntimeError("rolling quantile lost an active value")
        block = self.blocks[block_index]
        position = bisect.bisect_left(block, value)
        if position == len(block) or block[position] != value:
            raise RuntimeError("rolling quantile lost an active value")
        block.pop(position)
        if block:
            self.maxes[block_index] = block[-1]
        else:
            self.blocks.pop(block_index)
            self.maxes.pop(block_index)

    def add(self, value: Decimal) -> None:
        self._insert(value)
        self.order.append(value)
        self.size += 1
        if len(self.order) > self.maxlen:
            self._remove(self.order.popleft())
            self.size -= 1

    def value(self) -> Decimal:
        if not self.size:
            raise ValueError("rolling quantile is empty")
        rank = math.ceil(float(self.quantile) * self.size) - 1
        if rank <= self.size - rank - 1:
            for block in self.blocks:
                if rank < len(block):
                    return block[rank]
                rank -= len(block)
        else:
            reverse_rank = self.size - rank - 1
            for block in reversed(self.blocks):
                if reverse_rank < len(block):
                    return block[-reverse_rank - 1]
                reverse_rank -= len(block)
        raise RuntimeError("rolling quantile rank is out of bounds")


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


class StreamingDetector:
    """Causal detector that retains only the active frame window and baseline."""

    def __init__(self) -> None:
        self.quantile = RollingQuantile(TAIL_QUANTILE, BASELINE_SECONDS)
        self.history: deque[dict[str, Any]] = deque(
            maxlen=IMPULSE_SECONDS + REJECTION_SECONDS + 1
        )
        self.pending: PendingCandidate | None = None
        self.cooldown_until = -1
        self.previous_end_ns: int | None = None
        self.frames = 0
        self.candidates = 0
        self.accepted = 0
        self.rejected = 0

    def add(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        index = self.frames
        end_ns = int(frame["frame_end_ns"])
        if (
            self.previous_end_ns is not None
            and end_ns != self.previous_end_ns + 1_000_000_000
        ):
            raise ValueError("state frames must be consecutive one-second buckets")
        self.previous_end_ns = end_ns
        self.history.append(frame)
        self.frames += 1
        emitted: dict[str, Any] | None = None

        if self.pending is not None and index == self.pending.decision_index:
            window = list(self.history)
            if len(window) != IMPULSE_SECONDS + REJECTION_SECONDS + 1:
                raise ValueError("pending candidate window is incomplete")
            local_pending = PendingCandidate(
                candidate_index=IMPULSE_SECONDS,
                decision_index=IMPULSE_SECONDS + REJECTION_SECONDS,
                impulse_sign=self.pending.impulse_sign,
                impulse_start_mid=self.pending.impulse_start_mid,
                extreme_mid=self.pending.extreme_mid,
                impulse_size=self.pending.impulse_size,
                impulse_flow=self.pending.impulse_flow,
                threshold_bps=self.pending.threshold_bps,
                impulse_bps=self.pending.impulse_bps,
            )
            emitted = _evaluate_pending(local_pending, window)
            self.candidates += 1
            if emitted["accepted"]:
                self.accepted += 1
            else:
                self.rejected += 1
            self.pending = None

        if index < IMPULSE_SECONDS:
            return emitted

        impulse_window = list(self.history)[-(IMPULSE_SECONDS + 1) :]
        start_mid = _mid(impulse_window[0])
        current_mid = _mid(frame)
        impulse_bps = (current_mid / start_mid - 1) * TEN_THOUSAND
        absolute_bps = abs(impulse_bps)
        baseline_ready = len(self.quantile) >= MIN_BASELINE_SECONDS
        threshold = self.quantile.value() if baseline_ready else None

        if (
            self.pending is None
            and index >= self.cooldown_until
            and baseline_ready
            and threshold is not None
            and absolute_bps >= threshold
        ):
            mids = [_mid(item) for item in impulse_window]
            sign = 1 if impulse_bps > 0 else -1
            at_extreme = current_mid == (max(mids) if sign > 0 else min(mids))
            impulse_flow = sum(
                (_net_flow(item) for item in impulse_window[1:]), Decimal("0")
            )
            flow_aligned = impulse_flow * Decimal(sign) > 0
            quality_ok = _eligible_quality(impulse_window)
            if at_extreme and flow_aligned and quality_ok:
                size = abs(current_mid - start_mid)
                if size > 0:
                    self.pending = PendingCandidate(
                        candidate_index=index,
                        decision_index=index + REJECTION_SECONDS,
                        impulse_sign=sign,
                        impulse_start_mid=start_mid,
                        extreme_mid=current_mid,
                        impulse_size=size,
                        impulse_flow=impulse_flow,
                        threshold_bps=threshold,
                        impulse_bps=impulse_bps,
                    )
                    self.cooldown_until = index + COOLDOWN_SECONDS

        self.quantile.add(absolute_bps)
        return emitted

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "hypothesis": "H001",
            "frames": self.frames,
            "baseline_observations": len(self.quantile),
            "baseline_ready": len(self.quantile) >= MIN_BASELINE_SECONDS,
            "candidates": self.candidates,
            "accepted": self.accepted,
            "rejected": self.rejected,
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
        "impulse_start_mid": format(pending.impulse_start_mid, "f"),
        "sweep_extreme_mid": format(pending.extreme_mid, "f"),
        "decision_mid": format(decision_mid, "f"),
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
    detector = StreamingDetector()
    candidates: list[dict[str, Any]] = []
    for frame in frames:
        emitted = detector.add(frame)
        if emitted is not None:
            candidates.append(emitted)
    return candidates, detector.manifest()


def run_file(state_frame_path: Path) -> Path:
    output = state_frame_path.with_name("h001_candidates.jsonl")
    temporary_output = output.with_name(output.name + ".tmp")
    detector = StreamingDetector()
    with state_frame_path.open(encoding="utf-8") as source, temporary_output.open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for line in source:
            if not line.strip():
                continue
            candidate = detector.add(json.loads(line))
            if candidate is not None:
                handle.write(
                    json.dumps(candidate, separators=(",", ":"), sort_keys=True)
                    + "\n"
                )
    temporary_output.replace(output)
    manifest = detector.manifest()
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
