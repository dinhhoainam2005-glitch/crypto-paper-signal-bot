from __future__ import annotations

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


def env_enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def telegram_configured() -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    enabled = env_enabled("TELEGRAM_ENABLED")
    return enabled and bool(token) and bool(chat_id)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def compact_utc(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return text.replace("+00:00", " UTC")


def interval_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def fmt_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def format_startup_message(*, strategy_id: str, scan_interval_seconds: int, heartbeat_interval_seconds: int) -> str:
    return "\n".join(
        [
            "<b>STARTUP | R14H Paper Bot</b>",
            "",
            "<b>Status</b>: Running",
            "<b>Mode</b>: PAPER ONLY",
            "",
            "<b>Strategy</b>",
            f"<code>{esc(strategy_id)}</code>",
            "",
            "<b>Schedule</b>",
            f"Scan: every {interval_label(scan_interval_seconds)}",
            f"Heartbeat: every {interval_label(heartbeat_interval_seconds)}",
            "",
            "<b>Markets</b>",
            "BTCUSDT 4h",
            "ETHUSDT 1h",
        ]
    )


def format_heartbeat_message(scan_summary: dict[str, Any]) -> str:
    groups = scan_summary.get("groups", [])
    lines = [
        "<b>HEARTBEAT | R14H Paper Bot</b>",
        "",
        "<b>Status</b>: Running",
        "<b>Mode</b>: PAPER ONLY",
        "<b>Signals</b>: {count} new | {active} active".format(
            count=scan_summary.get("new_signal_count", 0),
            active=scan_summary.get("active_position_count", 0),
        ),
        "",
        "<b>Market Snapshot</b>",
    ]
    for group in groups:
        lines.extend(
            [
                "",
                "<b>{symbol} {tf}</b>".format(
                    symbol=esc(group.get("symbol", "")),
                    tf=esc(group.get("timeframe", "")),
                ),
                "Status: <code>{status}</code>".format(status=esc(group.get("status", ""))),
                "Bar: {bar}".format(bar=esc(compact_utc(group.get("latest_closed_bar_utc", "")))),
                "Premium z24: <code>{premium}</code>".format(
                    premium=fmt_float(group.get("premium_close_prior_z_24"), 4),
                ),
                "Volume z20: <code>{volz}</code>".format(
                    volz=fmt_float(group.get("quote_volume_prior_z_20"), 2),
                ),
            ]
        )
    return "\n".join(lines)


def format_signal_message(signal: dict[str, Any]) -> str:
    candidate = signal.get("candidate", {})
    features = signal.get("features", {})
    return "\n".join(
        [
            "<b>TRADE | PAPER | R14H</b>",
            "<b>{symbol} {side}</b>".format(
                symbol=esc(signal.get("symbol", "")),
                side=esc(signal.get("side", "")),
            ),
            "",
            "<b>Setup</b>",
            "TF: <code>{tf}</code>".format(tf=esc(signal.get("timeframe", ""))),
            "Candidate: <code>{candidate_id}</code>".format(
                candidate_id=esc(candidate.get("candidate_id", "")),
            ),
            "",
            "<b>Timing</b>",
            "Signal close: {signal_time}".format(
                signal_time=esc(compact_utc(signal.get("signal_time_utc", ""))),
            ),
            "Entry model: <code>NEXT_OPEN</code>",
            "Entry time: {entry_time}".format(
                entry_time=esc(compact_utc(signal.get("entry_time_utc", ""))),
            ),
            "Entry price: <code>{entry_price}</code>".format(
                entry_price=esc(signal.get("entry_price", "pending_next_open")),
            ),
            "Exit model: <code>HOLD_{hold}_BARS</code>".format(hold=esc(candidate.get("hold_bars", ""))),
            "",
            "<b>Filters</b>",
            "Premium z24: <code>{premium}</code> <= <code>0.882</code>".format(
                premium=fmt_float(features.get("premium_close_prior_z_24"), 4),
            ),
            "Derivatives state: <code>{derivatives}</code>".format(
                derivatives=esc(features.get("full_derivatives_state_available", False)),
            ),
            "Taker imbalance: <code>{imbalance}</code>".format(
                imbalance=fmt_float(features.get("mkt_taker_quote_imbalance_derived"), 4),
            ),
            "Volume z20: <code>{volume_z}</code>".format(
                volume_z=fmt_float(features.get("quote_volume_prior_z_20"), 2),
            ),
            "",
            "<b>Status</b>: PAPER ONLY, NOT LIVE",
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
                "parse_mode": "HTML",
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
