import json
import tempfile
import unittest
from pathlib import Path

from market_structure.forward_recorder import (
    _write_heartbeat,
    commit_frames,
    prune_completed_segments,
    project_h001_frame,
)


def _frame(second: int) -> dict:
    venue = {
        "mid": "100.0",
        "book_age_ms": 10,
        "net_aggressive_notional": "1.0",
        "imbalance_10bps": "0.1",
        "best_bid": "99.9",
        "best_ask": "100.1",
        "imbalance_5bps": "0.2",
    }
    return {
        "frame_end_ns": second * 1_000_000_000,
        "consolidated_mid": "100.0",
        "binance_minus_bybit_bps": "0.0",
        "venues": {"binance": dict(venue), "bybit": dict(venue)},
    }


def _write_frames(path: Path, seconds: list[int]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for second in seconds:
            handle.write(json.dumps(_frame(second)) + "\n")


class ForwardRecorderTests(unittest.TestCase):
    def test_projection_keeps_only_frozen_h001_fields(self) -> None:
        projected = project_h001_frame(_frame(10))
        self.assertEqual(
            set(projected["venues"]["binance"]),
            {"mid", "book_age_ms", "net_aggressive_notional", "imbalance_10bps"},
        )
        self.assertNotIn("best_bid", projected["venues"]["binance"])

    def test_overlap_is_idempotent_and_appends_only_new_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            _write_frames(first, [1, 2, 3])
            _write_frames(second, [2, 3, 4, 5])

            result_one = commit_frames(first, root / "forward", "one")
            result_two = commit_frames(second, root / "forward", "two")

            self.assertEqual(result_one["continuity"], "NEW_EPOCH")
            self.assertEqual(result_two["continuity"], "CONTIGUOUS")
            self.assertEqual(result_two["appended_frames"], 2)
            self.assertEqual(result_two["epoch_frames"], 5)

            ledger = root / "forward" / result_two["active_epoch_file"]
            ends = [json.loads(line)["frame_end_ns"] for line in ledger.read_text().splitlines()]
            self.assertEqual(ends, [1e9, 2e9, 3e9, 4e9, 5e9])

    def test_gap_starts_new_epoch_instead_of_contaminating_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.jsonl"
            gapped = root / "gapped.jsonl"
            _write_frames(first, [1, 2, 3])
            _write_frames(gapped, [5, 6])

            commit_frames(first, root / "forward", "one")
            result = commit_frames(gapped, root / "forward", "two")

            self.assertEqual(result["continuity"], "GAP_NEW_EPOCH")
            self.assertEqual(result["active_epoch"], 2)
            self.assertEqual(result["epoch_frames"], 2)
            self.assertFalse(result["baseline_ready"])

    def test_nonconsecutive_segment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bad.jsonl"
            _write_frames(source, [1, 3])
            with self.assertRaisesRegex(ValueError, "not consecutive"):
                commit_frames(source, root / "forward", "bad")

    def test_heartbeat_updates_preserve_prior_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_heartbeat(root, {"status": "OK", "last_segment": {"id": "one"}})
            _write_heartbeat(root, {"status": "OK", "h001": {"candidates": 0}})
            heartbeat = json.loads((root / "heartbeat.json").read_text())
            self.assertEqual(heartbeat["last_segment"], {"id": "one"})
            self.assertEqual(heartbeat["h001"], {"candidates": 0})

    def test_pruning_keeps_latest_completed_and_ignores_active_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = root / "segments"
            for name in ("001", "002", "003"):
                directory = segments / name
                directory.mkdir(parents=True)
                (directory / "manifest.json").write_text("{}", encoding="utf-8")
            active = segments / "004"
            active.mkdir()
            (active / "partial.jsonl").write_text("", encoding="utf-8")
            with (root / "segment_audit.jsonl").open("w", encoding="utf-8") as audit:
                for name in ("001", "002", "003"):
                    audit.write(json.dumps({"segment_id": name, "status": "COMMITTED"}) + "\n")

            retention = prune_completed_segments(root, 2)

            self.assertEqual(retention["removed"], ["001"])
            self.assertEqual(retention["failed"], [])
            self.assertFalse((segments / "001").exists())
            self.assertTrue((segments / "002").exists())
            self.assertTrue((segments / "003").exists())
            self.assertTrue(active.exists())

    def test_pruning_never_deletes_downloaded_but_unprocessed_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = root / "segments"
            for name in ("001", "002"):
                directory = segments / name
                directory.mkdir(parents=True)
                (directory / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "segment_audit.jsonl").write_text(
                json.dumps({"segment_id": "001", "status": "COMMITTED"}) + "\n",
                encoding="utf-8",
            )

            retention = prune_completed_segments(root, 0)

            self.assertEqual(retention["removed"], ["001"])
            self.assertFalse((segments / "001").exists())
            self.assertTrue((segments / "002").exists())

    def test_pruning_retries_identifiable_tombstones(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tombstone = root / "segments" / "001.prune_pending"
            tombstone.mkdir(parents=True)
            (tombstone / "leftover").write_text("x", encoding="utf-8")

            retention = prune_completed_segments(root, 2)

            self.assertEqual(retention["removed"], ["001"])
            self.assertFalse(tombstone.exists())


if __name__ == "__main__":
    unittest.main()
