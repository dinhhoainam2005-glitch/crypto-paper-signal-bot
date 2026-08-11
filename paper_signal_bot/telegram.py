from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


def telegram_configured() -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    enabled = os.getenv("TELEGRAM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    return enabled and bool(token) and bool(chat_id)


def format_signal_message(signal: dict[str, Any]) -> str:
    candidate = signal.get("candidate", {})
    features = signal.get("features", {})
    return "\n".join(
        [
            "[PAPER][R14H] {symbol} {side}".format(
                symbol=signal.get("symbol", ""),
                side=signal.get("side", ""),
            ),
            "TF={tf} candidate={candidate_id}".format(
                tf=signal.get("timeframe", ""),
                candidate_id=candidate.get("candidate_id", ""),
            ),
            "Signal close={signal_time} entry_model=NEXT_OPEN exit_model=HOLD_{hold}_BARS".format(
                signal_time=signal.get("signal_time_utc", ""),
                hold=candidate.get("hold_bars", ""),
            ),
            "Entry time={entry_time} entry_price={entry_price}".format(
                entry_time=signal.get("entry_time_utc", ""),
                entry_price=signal.get("entry_price", "pending_next_open"),
            ),
            "premium_z_24={premium:.4f} <= 0.882 | full_derivatives_state={derivatives}".format(
                premium=float(features.get("premium_close_prior_z_24", 0.0)),
                derivatives=features.get("full_derivatives_state_available", False),
            ),
            "taker_imbalance={imbalance:.4f} | volume_z={volume_z:.2f}".format(
                imbalance=float(features.get("mkt_taker_quote_imbalance_derived", 0.0)),
                volume_z=float(features.get("quote_volume_prior_z_20", 0.0)),
            ),
            "Status=PAPER_ONLY_NOT_LIVE",
        ]
    )


class TelegramSender:
    def __init__(self, token: str | None = None, chat_id: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.token = (token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return telegram_configured()

    def send_message(self, text: str) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "skipped": True, "reason": "telegram_not_configured"}
        url = "https://api.telegram.org/bot{token}/sendMessage".format(token=self.token)
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "crypto-paper-signal-bot/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)
