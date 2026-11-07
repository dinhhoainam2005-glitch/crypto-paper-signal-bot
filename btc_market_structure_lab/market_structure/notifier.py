"""Optional Vietnamese Telegram notifications for research-data operations."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.request import Request, urlopen

from .forward_recorder import BASELINE_REQUIRED_FRAMES


def _vn_time() -> str:
    return (datetime.now(UTC) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")


def format_operational_message(
    heartbeat: dict[str, Any],
    *,
    startup: bool = False,
) -> str:
    state = heartbeat.get("state") or {}
    last = heartbeat.get("last_segment") or {}
    h001 = heartbeat.get("h001") or {}
    evaluation = heartbeat.get("h001_evaluation") or {}
    frames = int(state.get("epoch_frames") or 0)
    progress = min(100.0, frames / BASELINE_REQUIRED_FRAMES * 100.0)
    data_status = str(heartbeat.get("status", "STARTING"))
    continuity = str(last.get("continuity", "n/a"))
    segment_status = str(last.get("status", "n/a"))
    baseline_ready = bool(h001.get("baseline_ready", False))
    title = (
        "BTC STRUCTURE LAB - ĐÃ KHỞI ĐỘNG"
        if startup
        else "BTC STRUCTURE LAB - BÁO DỮ LIỆU"
    )
    return "\n".join(
        [
            title,
            "--------------------------------",
            f"Trạng thái dữ liệu: {data_status}",
            f"Segment gần nhất: {segment_status} | {continuity}",
            f"Epoch: {state.get('active_epoch', 0)} | Liên tục: {frames:,} giây",
            f"Warm-up H001: {progress:.2f}% | {'ĐÃ ĐỦ' if baseline_ready else 'CHƯA ĐỦ 24H'}",
            (
                "Ứng viên audit: "
                f"{int(h001.get('candidates') or 0)} | "
                f"đạt {int(h001.get('accepted') or 0)} | "
                f"loại {int(h001.get('rejected') or 0)}"
            ),
            (
                "Đánh giá sealed: "
                f"{evaluation.get('status', 'CHƯA CHẠY')} | "
                f"ngày {int(evaluation.get('complete_days') or 0)}/"
                f"{int(evaluation.get('required_days') or 30)} | "
                f"test matched {int(evaluation.get('matched_test_events') or 0)}"
            ),
            "Phạm vi: BTC | Binance + Bybit | Nghiên cứu dữ liệu",
            "KHÔNG PHẢI TÍN HIỆU TRADE / KHÔNG TỰ ĐẶT LỆNH",
            f"Giờ VN: {_vn_time()}",
        ]
    )


@dataclass(frozen=True)
class TelegramNotifier:
    token: str
    chat_id: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> "TelegramNotifier | None":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            return None
        return cls(token=token, chat_id=chat_id)

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": text,
                "link_preview_options": {"is_disabled": True},
            }
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read())
        except Exception as exc:
            raise RuntimeError(
                f"Telegram transport failed: {type(exc).__name__}"
            ) from None
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("description", "Telegram sendMessage failed")))

    async def send_async(self, text: str) -> None:
        await asyncio.to_thread(self.send, text)
