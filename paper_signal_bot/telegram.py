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


def bot_label(strategy_id: str | None = None) -> str:
    text = strategy_id or ""
    if text.startswith("R22C"):
        return "R22C Paper Bot"
    if text.startswith("R15C"):
        return "R15C Paper Bot"
    if text.startswith("R14H"):
        return "R14H Paper Bot"
    return "Paper Signal Bot"


def format_startup_message(*, strategy_id: str, scan_interval_seconds: int, heartbeat_interval_seconds: int) -> str:
    return "\n".join(
        [
            f"<b>STARTUP | {bot_label(strategy_id)}</b>",
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
            "ETHUSDT 4h",
            "SOLUSDT 4h",
            "BNBUSDT 4h",
        ]
    )


def format_heartbeat_message(scan_summary: dict[str, Any]) -> str:
    groups = scan_summary.get("groups", [])
    strategy_id = scan_summary.get("strategy_id", "")
    lines = [
        f"<b>HEARTBEAT | {bot_label(strategy_id)}</b>",
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
                "Breadth: <code>{count}/{assets}</code> >= <code>{need}</code>".format(
                    count=fmt_float(group.get("market_breadth_count"), 0),
                    assets=fmt_float(group.get("market_breadth_assets"), 0),
                    need=fmt_float(group.get("breadth_n"), 0),
                ),
                "Market mean: <code>{mean}</code>".format(
                    mean=fmt_float(group.get("market_directional_mean"), 4),
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
    strategy_id = signal.get("strategy_id", "")
    return "\n".join(
        [
            "<b>TRADE | PAPER | {label}</b>".format(label=esc(bot_label(strategy_id).replace(" Paper Bot", ""))),
            "<b>{symbol} {side}</b>".format(
                symbol=esc(signal.get("symbol", "")),
                side=esc(signal.get("side", "")),
            ),
            "",
            "<b>Setup</b>",
            "TF: <code>{tf}</code>".format(tf=esc(signal.get("timeframe", ""))),
            "Sleeve: <code>{sleeve}</code>".format(sleeve=esc(signal.get("sleeve_id", features.get("sleeve_id", "")))),
            "Risk unit: <code>{risk}</code>".format(risk=fmt_float(signal.get("risk_fraction", features.get("risk_fraction")), 2)),
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
            "<b>Router</b>",
            "Breadth: <code>{count}/{assets}</code> >= <code>{need}</code>".format(
                count=fmt_float(features.get("market_breadth_count"), 0),
                assets=fmt_float(features.get("market_breadth_assets"), 0),
                need=fmt_float(features.get("breadth_n"), 0),
            ),
            "Breadth min: <code>{minv}</code> | Market mean: <code>{mean}</code>".format(
                minv=fmt_float(features.get("breadth_min"), 4),
                mean=fmt_float(features.get("market_directional_mean"), 4),
            ),
            "Trigger: <code>{family}</code>".format(
                family=esc(candidate.get("family", features.get("family", ""))),
            ),
            "Volume z20: <code>{volume_z}</code> >= <code>{min_z}</code>".format(
                volume_z=fmt_float(features.get("quote_volume_prior_z_20"), 2),
                min_z=fmt_float(features.get("volz_min"), 2),
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
