from __future__ import annotations

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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


def utc_vn_label(value: Any) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        return compact_utc(value)
    vn = parsed.astimezone(timezone(timedelta(hours=7)))
    return "{utc} | VN {vn}".format(
        utc=parsed.strftime("%Y-%m-%d %H:%M UTC"),
        vn=vn.strftime("%Y-%m-%d %H:%M"),
    )


def interval_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def parse_utc(value: Any) -> datetime | None:
    text = "" if value is None else str(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def clock_line(value: Any | None = None) -> str:
    stamp = parse_utc(value) or datetime.now(timezone.utc)
    asia = stamp.astimezone(timezone(timedelta(hours=7)))
    eu = stamp.astimezone(timezone(timedelta(hours=2)))
    us = stamp.astimezone(timezone(timedelta(hours=-4)))
    return "🕒 UTC:{utc} | 🌏 Asia:{asia} | 🇪🇺 EU:{eu} | 🇺🇸 US:{us}".format(
        utc=stamp.strftime("%H:%M"),
        asia=asia.strftime("%H:%M"),
        eu=eu.strftime("%H:%M"),
        us=us.strftime("%H:%M"),
    )


def fmt_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def fmt_signed_bps(value: Any) -> str:
    try:
        return f"{float(value):+.1f} bps"
    except (TypeError, ValueError):
        return "n/a"


def duration_label(seconds: Any) -> str:
    try:
        total = max(int(round(float(seconds))), 0)
    except (TypeError, ValueError):
        return "n/a"
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 120:
        return f"{minutes}m"
    hours = minutes // 60
    remainder = minutes % 60
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def short_text(value: Any, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def bot_label(strategy_id: str | None = None) -> str:
    text = strategy_id or ""
    if text.startswith("R24A"):
        return "R24A Strict Quality Bot"
    if text.startswith("R23B"):
        return "R23B Quality Bot"
    if text.startswith("R22C"):
        return "R22C Paper Bot"
    if text.startswith("R15C"):
        return "R15C Paper Bot"
    if text.startswith("R14H"):
        return "R14H Paper Bot"
    return "Paper Signal Bot"


def engine_label(strategy_id: str) -> str:
    text = strategy_id or ""
    if text.startswith("R24A"):
        return "R24A-STRICT-QUALITY-R15C-BNB"
    if text.startswith("R23B"):
        return "R23B-QUALITY-R15C-R22A"
    if text.startswith("R22C"):
        return "R22C-REGIME-SLEEVE"
    return text or "PAPER-SIGNAL"


def status_icon(status: Any) -> str:
    text = str(status or "").upper()
    if text == "SIGNAL":
        return "✅"
    if text == "SUPPRESSED":
        return "🟠"
    if text == "ERROR":
        return "🚨"
    if text == "INSUFFICIENT_HISTORY":
        return "⚠️"
    return "🟡"


def side_banner(symbol: Any, side: Any) -> str:
    text = str(side or "").upper()
    if text == "SHORT":
        return "🔻🔴 <b>PAPER SHORT — {symbol}</b> 🔴🔻".format(symbol=esc(symbol))
    if text == "LONG":
        return "🟢🔺 <b>PAPER LONG — {symbol}</b> 🔺🟢".format(symbol=esc(symbol))
    return "📡 <b>PAPER SIGNAL — {symbol}</b>".format(symbol=esc(symbol))


def data_age_label(latest_closed_bar_utc: Any, scanned_utc: Any) -> str:
    latest = parse_utc(latest_closed_bar_utc)
    scanned = parse_utc(scanned_utc) or datetime.now(timezone.utc)
    if latest is None:
        return "n/a"
    minutes = max(int((scanned - latest).total_seconds() // 60), 0)
    if minutes < 120:
        return f"{minutes} min"
    hours = minutes // 60
    remainder = minutes % 60
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def format_startup_message(*, strategy_id: str, scan_interval_seconds: int, heartbeat_interval_seconds: int) -> str:
    return "\n".join(
        [
            f"💞📡 <b>{bot_label(strategy_id).upper()} STARTUP — MARKET WATCH ACTIVE</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "🧬 Engine: <code>{engine}</code>".format(engine=esc(engine_label(strategy_id))),
            "📌 Mode: <b>PAPER SIGNAL ONLY</b>",
            "⏱️ TF: <b>1h + 4h</b>",
            "",
            "📊 <b>SIGNAL MARKETS</b>",
            "• BTCUSDT 1h, 4h",
            "• ETHUSDT 1h, 4h",
            "• BNBUSDT 4h",
            "• Context breadth: BTC/ETH/SOL/BNB 1h + 4h",
            "",
            "🛡️ <b>SAFETY</b>",
            "• SIGNAL_SEND: <b>ON</b>",
            "• AUTO_TRADE: <b>OFF</b>",
            "• LIVE_MODIFY: <b>OFF</b>",
            "• REAL_MONEY: <b>OFF</b>",
            "",
            "🗓️ <b>SCHEDULE</b>",
            f"• Scan: every {interval_label(scan_interval_seconds)}",
            f"• Heartbeat: every {interval_label(heartbeat_interval_seconds)}",
            "",
            "🔒 <b>SIGNAL ONLY / NO AUTO-TRADE</b>",
            clock_line(),
        ]
    )


def format_heartbeat_message(scan_summary: dict[str, Any]) -> str:
    groups = scan_summary.get("groups", [])
    strategy_id = scan_summary.get("strategy_id", "")
    scanned_utc = scan_summary.get("time_utc")
    timeframes = sorted({str(group.get("timeframe", "")).strip() for group in groups if group.get("timeframe")})
    timeframe_label = " + ".join(timeframes) if timeframes else "n/a"
    latest_candle = ""
    latest_dt = None
    for group in groups:
        parsed = parse_utc(group.get("latest_closed_bar_utc"))
        if parsed is not None and (latest_dt is None or parsed > latest_dt):
            latest_dt = parsed
            latest_candle = compact_utc(group.get("latest_closed_bar_utc"))
    state = "OK" if all(str(group.get("status", "")).upper() != "ERROR" for group in groups) else "DEGRADED"
    data_age = data_age_label(latest_dt.isoformat() if latest_dt else None, scanned_utc)
    data_state = "FRESH" if latest_dt is not None and data_age != "n/a" else "UNKNOWN"
    rules_scanned = sum(int(float(group.get("candidate_count") or 0)) for group in groups)
    lines = [
        f"💞📡 <b>{bot_label(strategy_id).upper()} HEARTBEAT — MARKET WATCH ACTIVE</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🧬 Engine: <code>{engine}</code>".format(engine=esc(engine_label(strategy_id))),
        "📌 Mode: <b>PAPER SIGNAL ONLY</b>",
        "⏱️ TF: <b>{tf}</b>".format(tf=esc(timeframe_label)),
        "",
        "📊 <b>LAST SCAN</b>",
        "• State: <b>{state}</b>".format(state=esc(state)),
        "• New signals: <b>{count}</b>".format(count=esc(scan_summary.get("new_signal_count", 0))),
        "• Suppressed: <b>{count}</b>".format(count=esc(scan_summary.get("suppressed_signal_count", 0))),
        "• Active positions: <b>{active}</b>".format(active=esc(scan_summary.get("active_position_count", 0))),
        "• Rules scanned: <b>{rules}</b>".format(rules=rules_scanned),
        "• Data: <b>{data}</b>".format(data=esc(data_state)),
        "• Latest candle: <b>{candle}</b>".format(candle=esc(latest_candle or "n/a")),
        "• Data age: <b>{age}</b>".format(age=esc(data_age)),
        "• At: <b>{at}</b>".format(at=esc(compact_utc(scanned_utc))),
        "",
        "📡 <b>MARKET SNAPSHOT</b>",
    ]
    for group in groups:
        group_lines = [
            "",
            "{icon} <b>{symbol} {tf}</b>".format(
                icon=status_icon(group.get("status")),
                symbol=esc(group.get("symbol", "")),
                tf=esc(group.get("timeframe", "")),
            ),
            "• State: <code>{status}</code>".format(status=esc(group.get("status", ""))),
        ]
        if group.get("error"):
            group_lines.append("• Error: <code>{error}</code>".format(error=esc(short_text(group.get("error")))))
        if group.get("suppressed_signal_count"):
            group_lines.append(
                "• Suppressed: <code>{count}</code> {reasons}".format(
                    count=esc(group.get("suppressed_signal_count")),
                    reasons=esc(", ".join(group.get("suppressed_reasons") or [])),
                )
            )
        if group.get("market_breadth_count") is not None:
            group_lines.extend(
                [
                    "• Breadth: <code>{count}/{assets}</code> >= <code>{need}</code>".format(
                        count=fmt_float(group.get("market_breadth_count"), 0),
                        assets=fmt_float(group.get("market_breadth_assets"), 0),
                        need=fmt_float(group.get("breadth_n"), 0),
                    ),
                    "• Market mean: <code>{mean}</code>".format(
                        mean=fmt_float(group.get("market_directional_mean"), 4),
                    ),
                ]
            )
        elif group.get("flow_thr") is not None:
            group_lines.append(
                "• Flow mode: <code>R15C taker-flow quality</code>"
            )
            group_lines.append(
                "• Taker flow: <code>{flow}</code> >= <code>{thr}</code>".format(
                    flow=fmt_float(group.get("flow_directional"), 4),
                    thr=fmt_float(group.get("flow_thr"), 4),
                )
            )
            group_lines.append(
                "• Realized vol24: <code>{rv}</code> >= <code>{need}</code>".format(
                    rv=fmt_float(group.get("realized_vol_24"), 4),
                    need=fmt_float(group.get("quality_realized_vol_24_min"), 4),
                )
            )
        else:
            group_lines.append("• Gate: <code>n/a</code>")
        group_lines.append(
            "• Volume z20: <code>{volz}</code>".format(
                volz=fmt_float(group.get("quote_volume_prior_z_20"), 2),
            )
        )
        lines.extend(
            group_lines
        )
    lines.extend(
        [
            "",
            "🛡️ <b>SAFETY</b>",
            "• SIGNAL_SEND: <b>ON</b>",
            "• AUTO_TRADE: <b>OFF</b>",
            "• LIVE_MODIFY: <b>OFF</b>",
            "• REAL_MONEY: <b>OFF</b>",
            "",
            "🔒 <b>SIGNAL ONLY / NO AUTO-TRADE</b>",
            clock_line(scanned_utc),
        ]
    )
    return "\n".join(lines)


def format_signal_message(signal: dict[str, Any]) -> str:
    candidate = signal.get("candidate", {})
    features = signal.get("features", {})
    strategy_id = signal.get("strategy_id", "")
    notify_time = signal.get("notify_time_utc") or signal.get("created_utc") or signal.get("scan_time_utc")
    side = str(signal.get("side", "")).upper()
    side_icon = "📈" if side == "LONG" else "📉" if side == "SHORT" else "📡"
    quality_lines = [
        "• Candidate: <code>{candidate_id}</code>".format(
            candidate_id=esc(candidate.get("candidate_id", "")),
        ),
        "• Score: <code>{score}</code>".format(
            score=fmt_float(candidate.get("selection_score"), 4),
        ),
        "• Trigger: <code>{family}</code>".format(
            family=esc(candidate.get("family", features.get("family", ""))),
        ),
    ]
    if features.get("market_breadth_count") is not None:
        quality_lines.extend(
            [
                "• Breadth: <code>{count}/{assets}</code> >= <code>{need}</code>".format(
                    count=fmt_float(features.get("market_breadth_count"), 0),
                    assets=fmt_float(features.get("market_breadth_assets"), 0),
                    need=fmt_float(features.get("breadth_n"), 0),
                ),
                "• Breadth min: <code>{minv}</code> | Market mean: <code>{mean}</code>".format(
                    minv=fmt_float(features.get("breadth_min"), 4),
                    mean=fmt_float(features.get("market_directional_mean"), 4),
                ),
            ]
        )
    if features.get("flow_thr") is not None:
        quality_lines.append(
            "• Taker flow: <code>{flow}</code> >= <code>{thr}</code>".format(
                flow=fmt_float(features.get("flow_directional"), 4),
                thr=fmt_float(features.get("flow_thr"), 4),
            )
        )
    if features.get("pullback_min") is not None:
        quality_lines.append(
            "• Pullback/reclaim: <code>{pullback}</code> >= <code>{need}</code> | Reclaim: <code>{reclaim}</code>".format(
                pullback=fmt_float(features.get("pullback"), 4),
                need=fmt_float(features.get("pullback_min"), 4),
                reclaim=esc(features.get("reclaim")),
            )
        )
    if features.get("volz_min") is not None:
        quality_lines.append(
            "• Volume z20: <code>{volume_z}</code> >= <code>{min_z}</code>".format(
                volume_z=fmt_float(features.get("quote_volume_prior_z_20"), 2),
                min_z=fmt_float(features.get("volz_min"), 2),
            )
        )
    if features.get("quality_realized_vol_24_min") is not None:
        quality_lines.append(
            "• Realized vol24: <code>{rv}</code> >= <code>{need}</code>".format(
                rv=fmt_float(features.get("realized_vol_24"), 4),
                need=fmt_float(features.get("quality_realized_vol_24_min"), 4),
            )
        )
    quality_lines.append(
        "• Signal time: <b>{signal_time}</b>".format(
            signal_time=esc(utc_vn_label(signal.get("signal_time_utc", ""))),
        )
    )
    return "\n".join(
        [
            side_banner(signal.get("symbol", ""), side),
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "✅ <b>LIVE PAPER SIGNAL</b>",
            "{icon} Side: <b>{side}</b>".format(icon=side_icon, side=esc(side)),
            "⏱️ TF: <b>{tf}</b>".format(tf=esc(signal.get("timeframe", ""))),
            "🧠 Model: <code>{engine}</code>".format(engine=esc(engine_label(strategy_id))),
            "🧩 Sleeve: <code>{sleeve}</code>".format(sleeve=esc(signal.get("sleeve_id", features.get("sleeve_id", "")))),
            "⚖️ Risk unit: <code>{risk}</code>".format(risk=fmt_float(signal.get("risk_fraction", features.get("risk_fraction")), 2)),
            "",
            "📍 <b>PRICE PLAN</b>",
            "• Current: <code>{current}</code>".format(
                current=fmt_float(features.get("close"), 4),
            ),
            "• Entry: <code>{entry_price}</code>".format(
                entry_price=esc(signal.get("entry_price", "pending_next_open")),
            ),
            "• Entry model: <code>NEXT_OPEN</code>",
            "• Entry time: <b>{entry_time}</b>".format(
                entry_time=esc(utc_vn_label(signal.get("entry_time_utc", ""))),
            ),
            "• Notify time: <b>{notify_time}</b>".format(
                notify_time=esc(utc_vn_label(notify_time)),
            ),
            "• Entry age: <code>{age}</code> | Max lag: <code>{max_lag}</code>".format(
                age=esc(duration_label(signal.get("entry_lag_seconds"))),
                max_lag=esc(duration_label(signal.get("max_entry_lag_seconds"))),
            ),
            "• Market at notify: <code>{market}</code>".format(
                market=fmt_float(signal.get("market_price_at_scan"), 4),
            ),
            "• Move since entry: <code>{move}</code> | Max chase: <code>{max_chase}</code>".format(
                move=esc(fmt_signed_bps(signal.get("entry_price_move_bps"))),
                max_chase=esc(fmt_signed_bps(signal.get("max_chase_bps"))),
            ),
            "• Exit model: <code>HOLD_{hold}_BARS</code>".format(hold=esc(candidate.get("hold_bars", ""))),
            "• Planned exit: <b>{exit_time}</b>".format(
                exit_time=esc(utc_vn_label(signal.get("planned_exit_time_utc", ""))),
            ),
            "",
            "📊 <b>SIGNAL QUALITY</b>",
            *quality_lines,
            "",
            "🔒 <b>PAPER ONLY / NO AUTO-TRADE</b>",
            clock_line(notify_time),
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
