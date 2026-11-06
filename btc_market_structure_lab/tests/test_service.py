import json
import tempfile
import unittest
from pathlib import Path

from market_structure.service import _http_response, service_snapshot


class ServiceTests(unittest.TestCase):
    def test_snapshot_starts_without_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = service_snapshot(Path(temporary), "start")
            self.assertEqual(snapshot["mode"], "RESEARCH_DATA_ONLY")
            self.assertIsNone(snapshot["heartbeat"])

    def test_snapshot_reads_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "heartbeat.json").write_text(
                json.dumps({"status": "OK"}), encoding="utf-8"
            )
            self.assertEqual(service_snapshot(root, "start")["heartbeat"]["status"], "OK")

    def test_http_response_has_exact_content_length(self) -> None:
        response = _http_response(200, {"status": "alive"})
        headers, body = response.split(b"\r\n\r\n", 1)
        self.assertIn(f"Content-Length: {len(body)}".encode(), headers)


if __name__ == "__main__":
    unittest.main()
