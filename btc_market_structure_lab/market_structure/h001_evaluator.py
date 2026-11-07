"""Sealed H001 outcome, matched-control, and pass-gate evaluation."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
from array import array
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

DAY_SECONDS = 86_400
DAY_NS = DAY_SECONDS * 1_000_000_000
ONE_SECOND_NS = 1_000_000_000
IMPULSE_SECONDS = 10
REJECTION_SECONDS = 5
VOLATILITY_SECONDS = 3_600
OUTCOME_SECONDS = 120
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20_261_106
MIN_TEST_EVENTS = 100
MIN_DIRECTION_EVENTS = 20
MIN_EXCLUSION_EVENTS = 20
TEN_THOUSAND = 10_000.0
STUDY_DAYS = 30
CALIBRATION_DAYS = 15
VALIDATION_DAYS = 7
TEST_DAYS = 8


@dataclass(slots=True)
class FrameSeries:
    end_ns: array
    mid: array
    funding: bytearray
    stale: bytearray

    def __len__(self) -> int:
        return len(self.end_ns)

    def index_of(self, frame_end_ns: int) -> int | None:
        if not self.end_ns:
            return None
        offset = frame_end_ns - int(self.end_ns[0])
        if offset < 0 or offset % ONE_SECOND_NS:
            return None
        index = offset // ONE_SECOND_NS
        if index >= len(self.end_ns) or int(self.end_ns[index]) != frame_end_ns:
            return None
        return int(index)


@dataclass(slots=True, frozen=True)
class RawFeature:
    decision_index: int
    candidate_index: int
    impulse_abs_bps: float
    impulse_sign: int
    realized_volatility: float
    hour_of_week: int
    funding_sign: str


@dataclass(slots=True)
class ControlPool:
    decision_index: array
    impulse_abs_bps: array
    realized_volatility: array
    impulse_sign: array

    @classmethod
    def create(cls) -> ControlPool:
        return cls(array("I"), array("d"), array("d"), array("b"))

    def add(self, raw: RawFeature) -> None:
        self.decision_index.append(raw.decision_index)
        self.impulse_abs_bps.append(raw.impulse_abs_bps)
        self.realized_volatility.append(raw.realized_volatility)
        self.impulse_sign.append(raw.impulse_sign)


@dataclass(slots=True, frozen=True)
class EventFeature:
    record: dict[str, Any]
    raw: RawFeature
    split: str
    stratum: tuple[int, int, int, str]


def load_frame_series(path: Path) -> FrameSeries:
    end_ns = array("q")
    mid = array("d")
    funding = bytearray()
    stale = bytearray()
    previous: int | None = None
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            frame = json.loads(line)
            current = int(frame["frame_end_ns"])
            if previous is not None and current != previous + ONE_SECOND_NS:
                raise ValueError(
                    f"line {line_number}: H001 epoch is not consecutive by one second"
                )
            previous = current
            end_ns.append(current)
            mid.append(float(frame["consolidated_mid"]))
            value = frame.get("binance_funding_rate")
            if value is None:
                funding.append(0)
            else:
                numeric = float(value)
                funding.append(1 if numeric < 0 else 3 if numeric > 0 else 2)
            venues = frame["venues"]
            is_stale = any(
                venues[venue].get("book_age_ms") is None
                or int(venues[venue]["book_age_ms"]) > 2_000
                for venue in ("binance", "bybit")
            )
            stale.append(1 if is_stale else 0)
    return FrameSeries(end_ns=end_ns, mid=mid, funding=funding, stale=stale)


def _funding_sign(value: int) -> str | None:
    if value == 3:
        return "POSITIVE"
    if value == 1:
        return "NEGATIVE"
    if value == 2:
        return "ZERO"
    return None


def _hour_of_week(frame_end_ns: int) -> int:
    moment = datetime.fromtimestamp(frame_end_ns / 1_000_000_000, UTC)
    return moment.weekday() * 24 + moment.hour


def _iter_raw_features(series: FrameSeries) -> Iterable[RawFeature]:
    returns: deque[float] = deque()
    total = 0.0
    square_total = 0.0
    for candidate_index in range(1, len(series) - REJECTION_SECONDS):
        value = math.log(
            series.mid[candidate_index] / series.mid[candidate_index - 1]
        )
        returns.append(value)
        total += value
        square_total += value * value
        if len(returns) > VOLATILITY_SECONDS:
            expired = returns.popleft()
            total -= expired
            square_total -= expired * expired
        if len(returns) < VOLATILITY_SECONDS:
            continue
        decision_index = candidate_index + REJECTION_SECONDS
        if any(
            series.stale[candidate_index - IMPULSE_SECONDS : decision_index + 1]
        ):
            continue
        funding_sign = _funding_sign(series.funding[decision_index])
        if funding_sign is None:
            continue
        raw_return = series.mid[candidate_index] / series.mid[
            candidate_index - IMPULSE_SECONDS
        ] - 1.0
        if raw_return == 0:
            continue
        variance = max(
            0.0,
            square_total / VOLATILITY_SECONDS
            - (total / VOLATILITY_SECONDS) ** 2,
        )
        yield RawFeature(
            decision_index=decision_index,
            candidate_index=candidate_index,
            impulse_abs_bps=abs(raw_return) * TEN_THOUSAND,
            impulse_sign=1 if raw_return > 0 else -1,
            realized_volatility=math.sqrt(variance),
            hour_of_week=_hour_of_week(int(series.end_ns[decision_index])),
            funding_sign=funding_sign,
        )


def nearest_rank_deciles(values: Iterable[float]) -> tuple[float, ...]:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot compute deciles from an empty calibration sample")
    boundaries = []
    for decile in range(1, 10):
        rank = math.ceil(decile / 10 * len(ordered))
        boundaries.append(float(ordered[rank - 1]))
    return tuple(boundaries)


def assign_decile(value: float, boundaries: tuple[float, ...]) -> int:
    return bisect.bisect_left(boundaries, value)


def complete_day_splits(
    series: FrameSeries,
    study_start_day: int | None = None,
) -> tuple[dict[int, str], dict[str, int | None]]:
    counts = Counter((int(value) - 1) // DAY_NS for value in series.end_ns)
    days = sorted(day for day, count in counts.items() if count == DAY_SECONDS)
    if not days:
        return {}, {
            "complete_days": 0,
            "required_days": STUDY_DAYS,
            "study_start_day": study_start_day,
            "calibration": 0,
            "validation": 0,
            "test": 0,
        }
    start = days[0] if study_start_day is None else study_start_day
    planned = list(range(start, start + STUDY_DAYS))
    complete = set(days)
    observed = sum(day in complete for day in planned)
    day_counts: dict[str, int | None] = {
        "complete_days": observed,
        "required_days": STUDY_DAYS,
        "study_start_day": start,
        "calibration": sum(day in complete for day in planned[:CALIBRATION_DAYS]),
        "validation": sum(
            day in complete
            for day in planned[
                CALIBRATION_DAYS : CALIBRATION_DAYS + VALIDATION_DAYS
            ]
        ),
        "test": sum(
            day in complete
            for day in planned[CALIBRATION_DAYS + VALIDATION_DAYS :]
        ),
    }
    if observed < STUDY_DAYS:
        return {}, day_counts
    assignments: dict[int, str] = {}
    for position, day in enumerate(planned):
        if position < CALIBRATION_DAYS:
            assignments[day] = "calibration"
        elif position < CALIBRATION_DAYS + VALIDATION_DAYS:
            assignments[day] = "validation"
        else:
            assignments[day] = "test"
    return assignments, day_counts


def _day_for_frame(frame_end_ns: int) -> int:
    return (frame_end_ns - 1) // DAY_NS


def _outcome_split(
    series: FrameSeries,
    day_assignments: dict[int, str],
    decision_index: int,
) -> str | None:
    if decision_index + OUTCOME_SECONDS >= len(series):
        return None
    if any(
        series.stale[
            decision_index + 1 : decision_index + OUTCOME_SECONDS + 1
        ]
    ):
        return None
    decision_split = day_assignments.get(
        _day_for_frame(int(series.end_ns[decision_index]))
    )
    outcome_split = day_assignments.get(
        _day_for_frame(int(series.end_ns[decision_index + OUTCOME_SECONDS]))
    )
    return decision_split if decision_split == outcome_split else None


def _stratum(
    raw: RawFeature,
    impulse_boundaries: tuple[float, ...],
    volatility_boundaries: tuple[float, ...],
) -> tuple[int, int, int, str]:
    return (
        assign_decile(raw.impulse_abs_bps, impulse_boundaries),
        assign_decile(raw.realized_volatility, volatility_boundaries),
        raw.hour_of_week,
        raw.funding_sign,
    )


def label_outcome(
    series: FrameSeries,
    decision_index: int,
    direction: str,
    sweep_extreme_mid: float | None = None,
    impulse_direction: str | None = None,
) -> dict[str, Any]:
    if decision_index + OUTCOME_SECONDS >= len(series):
        raise ValueError("complete 120-second outcome window is unavailable")
    multiplier = 1.0 if direction == "UP" else -1.0
    base = series.mid[decision_index]
    path = [
        multiplier * (series.mid[index] / base - 1.0) * TEN_THOUSAND
        for index in range(decision_index + 1, decision_index + OUTCOME_SECONDS + 1)
    ]
    revisited: bool | None = None
    if sweep_extreme_mid is not None and impulse_direction is not None:
        future = series.mid[decision_index + 1 : decision_index + OUTCOME_SECONDS + 1]
        revisited = (
            any(value >= sweep_extreme_mid for value in future)
            if impulse_direction == "UP"
            else any(value <= sweep_extreme_mid for value in future)
        )
    return {
        "signed_return_10s_bps": path[9],
        "signed_return_30s_bps": path[29],
        "signed_return_120s_bps": path[119],
        "mfe_120s_bps": max(path),
        "mae_120s_bps": min(path),
        "sweep_extreme_revisited": revisited,
    }


def moving_block_bootstrap_ci(
    values: list[float],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap sample is empty")
    size = len(values)
    block = math.ceil(math.sqrt(size))
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        selected: list[float] = []
        while len(selected) < size:
            start = rng.randrange(size)
            selected.extend(values[(start + offset) % size] for offset in range(block))
        means.append(sum(selected[:size]) / size)
    means.sort()
    lower = means[math.floor(0.025 * (samples - 1))]
    upper = means[math.ceil(0.975 * (samples - 1))]
    return lower, upper


def _quality_gate(
    audit_path: Path,
    active_epoch: int,
    expected_epoch_segments: int | None = None,
    expected_epoch_frames: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    committed = []
    if audit_path.exists():
        with audit_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if (
                    record.get("status") == "COMMITTED"
                    and int(record.get("active_epoch", -1)) == active_epoch
                ):
                    committed.append(record)
    problems = []
    for expected_counter, record in enumerate(committed, start=1):
        if int(record.get("epoch_segments_committed", -1)) != expected_counter:
            problems.append(f"{record.get('segment_id')}:audit_sequence")
        if int(record.get("normalization_rejects", -1)) != 0:
            problems.append(f"{record.get('segment_id')}:normalization_rejects")
        if record.get("book_replay_passed") is not True:
            problems.append(f"{record.get('segment_id')}:replay_failed")
        for venue, report in (record.get("book_replay_venues") or {}).items():
            stats = report.get("stats") or {}
            if int(stats.get("gaps", -1)) != 0:
                problems.append(f"{record.get('segment_id')}:{venue}_gaps")
            if int(stats.get("crossed_books", -1)) != 0:
                problems.append(f"{record.get('segment_id')}:{venue}_crossed")
    if expected_epoch_segments is not None and len(committed) != expected_epoch_segments:
        problems.append("epoch_segment_count_mismatch")
    if expected_epoch_frames is not None:
        audited_frames = int(committed[-1].get("epoch_frames", -1)) if committed else -1
        if audited_frames != expected_epoch_frames:
            problems.append("epoch_frame_count_mismatch")
    return bool(committed) and not problems, {
        "committed_segments": len(committed),
        "expected_epoch_segments": expected_epoch_segments,
        "expected_epoch_frames": expected_epoch_frames,
        "problems": problems,
    }


def _mark_range(mask: bytearray, start: int, end_inclusive: int) -> None:
    start = max(0, start)
    end_inclusive = min(len(mask) - 1, end_inclusive)
    if start <= end_inclusive:
        mask[start : end_inclusive + 1] = b"\x01" * (end_inclusive - start + 1)


def _range_marked(mask: bytearray, start: int, end_inclusive: int) -> bool:
    start = max(0, start)
    end_inclusive = min(len(mask) - 1, end_inclusive)
    return start <= end_inclusive and any(mask[start : end_inclusive + 1])


def _effect_summary(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if not pairs:
        return {"n": 0}
    effects: dict[str, list[float]] = {}
    for horizon in (10, 30, 120):
        key = f"effect_{horizon}s_bps"
        effects[key] = [float(pair[key]) for pair in pairs]
    primary_ci = moving_block_bootstrap_ci(effects["effect_30s_bps"])
    return {
        "n": len(pairs),
        "mean_effect_10s_bps": sum(effects["effect_10s_bps"]) / len(pairs),
        "mean_effect_30s_bps": sum(effects["effect_30s_bps"]) / len(pairs),
        "mean_effect_120s_bps": sum(effects["effect_120s_bps"]) / len(pairs),
        "effect_30s_ci95_bps": [primary_ci[0], primary_ci[1]],
    }


def evaluate_epoch(
    frame_path: Path,
    candidate_path: Path,
    audit_path: Path,
    active_epoch: int,
    study_start_day: int | None = None,
    expected_epoch_segments: int | None = None,
    expected_epoch_frames: int | None = None,
) -> dict[str, Any]:
    series = load_frame_series(frame_path)
    candidates = [
        json.loads(line)
        for line in candidate_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if candidate_path.exists() else []
    quality_passed, quality = _quality_gate(
        audit_path,
        active_epoch,
        expected_epoch_segments,
        expected_epoch_frames,
    )
    day_assignments, day_counts = complete_day_splits(series, study_start_day)
    base_report: dict[str, Any] = {
        "schema_version": 1,
        "hypothesis": "H001",
        "active_epoch": active_epoch,
        "frames": len(series),
        "days": day_counts,
        "detector_candidates": len(candidates),
        "accepted_candidates": sum(bool(item.get("accepted")) for item in candidates),
        "data_quality_passed": quality_passed,
        "data_quality": quality,
        "sealed_outcomes_released": False,
        "contains_pnl": False,
        "evaluation_spec": "H001_EVALUATION_SPEC_V2",
        "frozen_parameters": {
            "outcome_seconds": OUTCOME_SECONDS,
            "volatility_seconds": VOLATILITY_SECONDS,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "minimum_test_events": MIN_TEST_EVENTS,
            "minimum_direction_events": MIN_DIRECTION_EVENTS,
            "minimum_venue_exclusion_events": MIN_EXCLUSION_EVENTS,
            "study_days": STUDY_DAYS,
            "calibration_days": CALIBRATION_DAYS,
            "validation_days": VALIDATION_DAYS,
            "test_days": TEST_DAYS,
        },
    }
    if not day_assignments:
        status = (
            "INCONCLUSIVE_INSUFFICIENT_DAYS"
            if int(day_counts["complete_days"] or 0) == 0
            else "INCONCLUSIVE_COLLECTION_ACTIVE"
        )
        return {**base_report, "status": status}

    calibration_impulse: list[float] = []
    calibration_volatility: list[float] = []
    for raw in _iter_raw_features(series):
        if _outcome_split(series, day_assignments, raw.decision_index) == "calibration":
            calibration_impulse.append(raw.impulse_abs_bps)
            calibration_volatility.append(raw.realized_volatility)
    if not calibration_impulse or not calibration_volatility:
        return {**base_report, "status": "INCONCLUSIVE_NO_CALIBRATION_FEATURES"}

    impulse_boundaries = nearest_rank_deciles(calibration_impulse)
    volatility_boundaries = nearest_rank_deciles(calibration_volatility)
    blocked = bytearray(len(series))
    for candidate in candidates:
        decision_index = series.index_of(int(candidate["decision_frame_end_ns"]))
        if decision_index is None:
            continue
        _mark_range(
            blocked,
            decision_index - OUTCOME_SECONDS,
            decision_index + OUTCOME_SECONDS,
        )

    accepted_records: dict[int, tuple[dict[str, Any], int]] = {}
    for record in candidates:
        if not record.get("accepted"):
            continue
        decision_index = series.index_of(int(record["decision_frame_end_ns"]))
        candidate_index = series.index_of(int(record["candidate_frame_end_ns"]))
        if decision_index is not None and candidate_index is not None:
            accepted_records[decision_index] = (record, candidate_index)

    controls: dict[
        tuple[str, tuple[int, int, int, str]], ControlPool
    ] = defaultdict(ControlPool.create)
    event_features: dict[int, EventFeature] = {}
    for raw in _iter_raw_features(series):
        split = _outcome_split(series, day_assignments, raw.decision_index)
        if split is None:
            continue
        stratum = _stratum(raw, impulse_boundaries, volatility_boundaries)
        accepted = accepted_records.get(raw.decision_index)
        if accepted is not None and accepted[1] == raw.candidate_index:
            event_features[raw.decision_index] = EventFeature(
                record=accepted[0], raw=raw, split=split, stratum=stratum
            )
        if not blocked[raw.decision_index]:
            controls[(split, stratum)].add(raw)

    accepted_events: list[EventFeature] = []
    last_event_decision = -OUTCOME_SECONDS
    for decision_index in sorted(event_features):
        if decision_index - last_event_decision < OUTCOME_SECONDS:
            continue
        accepted_events.append(event_features[decision_index])
        last_event_decision = decision_index

    used_windows = bytearray(len(series))
    for event in accepted_events:
        _mark_range(
            used_windows,
            event.raw.decision_index + 1,
            event.raw.decision_index + OUTCOME_SECONDS,
        )
    used_controls: set[int] = set()
    pairs_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched_by_split: Counter[str] = Counter()
    for event in accepted_events:
        pool = controls.get((event.split, event.stratum))
        selected_position: int | None = None
        selected_key: tuple[float, float, int] | None = None
        if pool is not None:
            for position, index_value in enumerate(pool.decision_index):
                index = int(index_value)
                if index >= event.raw.decision_index:
                    break
                if index in used_controls:
                    continue
                if _range_marked(
                    used_windows, index + 1, index + OUTCOME_SECONDS
                ):
                    continue
                key = (
                    abs(pool.impulse_abs_bps[position] - event.raw.impulse_abs_bps),
                    abs(
                        pool.realized_volatility[position]
                        - event.raw.realized_volatility
                    ),
                    -index,
                )
                if selected_key is None or key < selected_key:
                    selected_position = position
                    selected_key = key
        if selected_position is None or pool is None:
            unmatched_by_split[event.split] += 1
            continue
        selected_index = int(pool.decision_index[selected_position])
        selected_sign = int(pool.impulse_sign[selected_position])
        used_controls.add(selected_index)
        _mark_range(
            used_windows,
            selected_index + 1,
            selected_index + OUTCOME_SECONDS,
        )
        event_outcome = label_outcome(
            series,
            event.raw.decision_index,
            str(event.record["research_direction"]),
            float(event.record["sweep_extreme_mid"]),
            str(event.record["impulse_direction"]),
        )
        control_direction = "DOWN" if selected_sign > 0 else "UP"
        control_outcome = label_outcome(
            series, selected_index, control_direction
        )
        pair = {
            "event_decision_frame_end_ns": int(
                series.end_ns[event.raw.decision_index]
            ),
            "control_decision_frame_end_ns": int(series.end_ns[selected_index]),
            "research_direction": event.record["research_direction"],
            "trigger_venue": event.record["trigger_venue"],
            "stratum": list(event.stratum),
            "event": event_outcome,
            "control": control_outcome,
        }
        for horizon in (10, 30, 120):
            pair[f"effect_{horizon}s_bps"] = (
                event_outcome[f"signed_return_{horizon}s_bps"]
                - control_outcome[f"signed_return_{horizon}s_bps"]
            )
        pairs_by_split[event.split].append(pair)

    counts = {
        split: {
            "eligible_events": sum(event.split == split for event in accepted_events),
            "matched_events": len(pairs_by_split[split]),
            "unmatched_events": int(unmatched_by_split[split]),
        }
        for split in ("calibration", "validation", "test")
    }
    test_pairs = pairs_by_split["test"]
    direction_counts = Counter(str(pair["research_direction"]) for pair in test_pairs)
    exclusion_counts = {
        venue: sum(pair["trigger_venue"] != venue for pair in test_pairs)
        for venue in ("binance", "bybit")
    }
    sample_gate = (
        len(test_pairs) >= MIN_TEST_EVENTS
        and all(direction_counts[direction] >= MIN_DIRECTION_EVENTS for direction in ("UP", "DOWN"))
        and all(count >= MIN_EXCLUSION_EVENTS for count in exclusion_counts.values())
    )
    report = {
        **base_report,
        "calibration_decile_boundaries": {
            "impulse_abs_bps": list(impulse_boundaries),
            "realized_volatility": list(volatility_boundaries),
        },
        "matching_counts": counts,
        "sealed_test_sample_gate": {
            "passed": sample_gate,
            "matched": len(test_pairs),
            "directions": dict(direction_counts),
            "venue_exclusions": exclusion_counts,
            "requirements": {
                "matched": MIN_TEST_EVENTS,
                "each_direction": MIN_DIRECTION_EVENTS,
                "each_venue_exclusion": MIN_EXCLUSION_EVENTS,
            },
        },
    }
    if not sample_gate:
        return {**report, "status": "INCONCLUSIVE_INSUFFICIENT_SAMPLE"}
    if not quality_passed:
        return {**report, "status": "INCONCLUSIVE_DATA_QUALITY"}

    overall = _effect_summary(test_pairs)
    by_direction = {
        direction: _effect_summary(
            [pair for pair in test_pairs if pair["research_direction"] == direction]
        )
        for direction in ("UP", "DOWN")
    }
    excluding = {
        venue: _effect_summary(
            [pair for pair in test_pairs if pair["trigger_venue"] != venue]
        )
        for venue in ("binance", "bybit")
    }
    conditions = {
        "primary_positive_ci_excludes_zero": overall["mean_effect_30s_bps"] > 0
        and overall["effect_30s_ci95_bps"][0] > 0,
        "horizon_10_positive": overall["mean_effect_10s_bps"] > 0,
        "horizon_120_positive": overall["mean_effect_120s_bps"] > 0,
        "exclude_binance_positive": excluding["binance"]["mean_effect_30s_bps"] > 0,
        "exclude_bybit_positive": excluding["bybit"]["mean_effect_30s_bps"] > 0,
        "up_nonnegative": by_direction["UP"]["mean_effect_30s_bps"] >= 0,
        "down_nonnegative": by_direction["DOWN"]["mean_effect_30s_bps"] >= 0,
        "data_quality": quality_passed,
    }
    passed = all(conditions.values())
    return {
        **report,
        "status": "PASSED" if passed else "REJECTED",
        "sealed_outcomes_released": True,
        "sealed_test": {
            "overall": overall,
            "by_direction": by_direction,
            "excluding_trigger_venue": excluding,
            "conditions": conditions,
        },
    }


def run_data_root(data_root: Path) -> Path:
    state = json.loads((data_root / "forward_state.json").read_text(encoding="utf-8"))
    active_epoch = int(state["active_epoch"])
    frame_path = data_root / str(state["active_epoch_file"])
    candidate_path = frame_path.with_name("h001_candidates.jsonl")
    plan_path = frame_path.with_name("h001_study_plan.json")
    sealed_path = frame_path.with_name("h001_sealed_result.json")
    if sealed_path.exists():
        return sealed_path
    study_start_day: int | None = None
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        study_start_day = int(plan["study_start_day"])
    report = evaluate_epoch(
        frame_path,
        candidate_path,
        data_root / "segment_audit.jsonl",
        active_epoch,
        study_start_day,
        int(state.get("active_epoch_segments_committed", 0)),
        int(state.get("epoch_frames", 0)),
    )
    planned_start = report["days"].get("study_start_day")
    if study_start_day is None and planned_start is not None:
        plan = {
            "schema_version": 1,
            "hypothesis": "H001",
            "active_epoch": active_epoch,
            "study_start_day": int(planned_start),
            "study_days": STUDY_DAYS,
            "calibration_days": CALIBRATION_DAYS,
            "validation_days": VALIDATION_DAYS,
            "test_days": TEST_DAYS,
            "frozen_before_outcomes": True,
        }
        temporary_plan = plan_path.with_name(plan_path.name + ".tmp")
        temporary_plan.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary_plan.replace(plan_path)
    report["frame_source"] = str(frame_path)
    report["candidate_source"] = str(candidate_path)
    report["study_plan"] = str(plan_path)
    output = frame_path.with_name("h001_evaluation_status.json")
    if int(report["days"]["complete_days"] or 0) >= STUDY_DAYS:
        output = sealed_path
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    output = run_data_root(args.data_root)
    report = json.loads(output.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "output": str(output),
                "status": report["status"],
                "sealed_outcomes_released": report["sealed_outcomes_released"],
                "matched_test_events": report.get("sealed_test_sample_gate", {}).get(
                    "matched", 0
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
