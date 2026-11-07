import os
import unittest
from unittest.mock import patch

from market_structure.notifier import TelegramNotifier, format_operational_message


class NotifierTests(unittest.TestCase):
    def test_notifier_is_disabled_without_both_secrets(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(TelegramNotifier.from_environment())
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}, clear=True):
            self.assertIsNone(TelegramNotifier.from_environment())

    def test_vietnamese_operational_message_is_not_a_trade_signal(self) -> None:
        heartbeat = {
            "status": "OK",
            "state": {"active_epoch": 1, "epoch_frames": 43205},
            "last_segment": {"status": "COMMITTED", "continuity": "CONTIGUOUS"},
            "h001": {
                "baseline_ready": False,
                "candidates": 3,
                "accepted": 1,
                "rejected": 2,
            },
            "h001_evaluation": {
                "status": "INCONCLUSIVE_INSUFFICIENT_SAMPLE",
                "complete_days": 30,
                "required_days": 30,
                "matched_test_events": 12,
            },
        }
        message = format_operational_message(heartbeat, startup=True)
        self.assertIn("ĐÃ KHỞI ĐỘNG", message)
        self.assertIn("50.00%", message)
        self.assertIn("CHƯA ĐỦ 24H", message)
        self.assertIn("KHÔNG PHẢI TÍN HIỆU TRADE", message)
        self.assertIn("test matched 12", message)
        self.assertIn("ngày 30/30", message)


if __name__ == "__main__":
    unittest.main()
