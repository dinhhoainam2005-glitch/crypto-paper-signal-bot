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
    if text.startswith("R26A"):
        return "R26A Quality Core Bot"
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
    if text.startswith("R26A"):
        return "R26A-QUALITY-CORE-R25A-PULSE"
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
            "• SOLUSDT 4h",
            "• BNBUSDT 4h",
            "• Context breadth: BTC/ETH/SOL/BNB 1h + 4h",
            "",
            "📡 <b>R25A MARKET PULSE / WATCH</b>",
            "• BTC / ETH / SOL / BNB: 15m + 1h + 4h",
            "• LONG + SHORT | Watch only",
            "",
            "🧲 <b>R27A LIQUIDITY INTEL / WATCH</b>",
            "• Binance depth + OI + volume",
            "• Hyperliquid L2 cross-check",
            "• Liquidity/liquidation proxy | Watch only",
            "",
            "🗓️ <b>R28A MACRO EVENT WATCH</b>",
            "• FOMC / CPI / NFP / PCE / GDP / Retail Sales",
            "• Official calendar + nowcast/consensus when available",
            "• Macro risk window | Watch only",
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
        parsed = parse_utc(group.get("latest_candle_close_utc"))
        if parsed is not None and (latest_dt is None or parsed > latest_dt):
            latest_dt = parsed
            latest_candle = compact_utc(group.get("latest_candle_close_utc"))
    pulse_groups = scan_summary.get("pulse_groups", [])
    liquidity_groups = scan_summary.get("liquidity_groups", [])
    macro_groups = scan_summary.get("macro_groups", [])
    all_groups = [*groups, *pulse_groups]
    if liquidity_groups:
        all_groups = [*all_groups, *liquidity_groups]
    if macro_groups:
        all_groups = [*all_groups, *macro_groups]
    states = [str(g.get("data_state", "UNKNOWN")) for g in all_groups]
    data_state = "FRESH" if states and all(s == "FRESH" for s in states) else "DEGRADED" if "FRESH" in states else "UNKNOWN"
    state = "OK" if data_state == "FRESH" and all(g.get("status") not in {"ERROR", "INVALID_DATA", "DATA_GAP", "INSUFFICIENT_HISTORY"} for g in all_groups) else "DEGRADED"
    data_age = data_age_label(latest_dt.isoformat() if latest_dt else None, scanned_utc)
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
        "• New pulse alerts: <b>{count}</b> (watch)".format(count=esc(scan_summary.get("new_pulse_event_count", 0))),
        "• Liquidity alerts: <b>{count}</b> (watch)".format(count=esc(scan_summary.get("new_liquidity_event_count", 0))),
        "• Macro alerts: <b>{count}</b> (watch)".format(count=esc(scan_summary.get("new_macro_event_count", 0))),
        "• Suppressed: <b>{count}</b>".format(count=esc(scan_summary.get("suppressed_signal_count", 0))),
        "• Active positions: <b>{active}</b>".format(active=esc(scan_summary.get("active_position_count", 0))),
        "• Rules scanned: <b>{rules}</b>".format(rules=rules_scanned),
        "• Data: <b>{data}</b>".format(data=esc(data_state)),
        "• Latest candle close: <b>{candle}</b>".format(candle=esc(latest_candle or "n/a")),
        "• Since close: <b>{age}</b>".format(age=esc(data_age)),
        "• Scan duration: <b>{duration}</b>".format(duration=esc(duration_label(scan_summary.get("scan_duration_seconds")))),
        "• Scan gap: <b>{duration}</b>".format(duration=esc(duration_label(scan_summary.get("scan_gap_seconds")))),
        "• At: <b>{at}</b>".format(at=esc(compact_utc(scanned_utc))),
        "",
        "📡 <b>MARKET SNAPSHOT</b>",
    ]
    if pulse_groups:
        fresh = sum(g.get("data_state") == "FRESH" and g.get("status") not in {"INSUFFICIENT_HISTORY", "INVALID_DATA", "DATA_GAP"} for g in pulse_groups)
        lines.insert(-1, f"📡 R25A watch: <b>{fresh}/{len(pulse_groups)} feeds ready</b> | 15m + 1h + 4h | LONG + SHORT")
    if liquidity_groups:
        ready = sum(g.get("data_state") == "FRESH" and g.get("status") not in {"DATA_NOT_READY", "ERROR"} for g in liquidity_groups)
        strongest = max(
            liquidity_groups,
            key=lambda group: float((group.get("features") or {}).get("binance_wall_intensity") or 0.0),
        )
        strong_features = strongest.get("features") or {}
        lines.extend(
            [
                "",
                "🧲 <b>LIQUIDITY INTEL</b>",
                "• R27A feeds: <b>{ready}/{total}</b> ready | Binance depth/OI + Hyperliquid L2".format(
                    ready=esc(ready),
                    total=esc(len(liquidity_groups)),
                ),
                "• Strongest wall: <b>{symbol}</b> <code>{side}</code> @ <code>{price}</code> | <code>{distance}</code> bps".format(
                    symbol=esc(strongest.get("symbol", "n/a")),
                    side=esc(strong_features.get("binance_wall_side", "n/a")),
                    price=fmt_float(strong_features.get("binance_wall_price"), 4),
                    distance=fmt_float(strong_features.get("binance_wall_distance_bps"), 1),
                ),
            ]
        )
    if macro_groups:
        macro_group = macro_groups[0]
        source_states = macro_group.get("source_states") or []
        ready_sources = sum(1 for item in source_states if item.get("status") == "OK")
        disabled_sources = sum(1 for item in source_states if item.get("status") == "DISABLED")
        next_event = macro_group.get("next_event") or {}
        next_line = "n/a"
        if next_event:
            next_line = "{priority} {title} | {time}".format(
                priority=next_event.get("priority", "n/a"),
                title=next_event.get("title", "n/a"),
                time=utc_vn_label(next_event.get("event_time_utc")),
            )
        lines.extend(
            [
                "",
                "🗓️ <b>MACRO EVENT WATCH</b>",
                "• R28A sources: <b>{ready}/{total}</b> OK | optional disabled: <b>{disabled}</b>".format(
                    ready=esc(ready_sources),
                    total=esc(len(source_states)),
                    disabled=esc(disabled_sources),
                ),
                "• Upcoming tracked: <b>{count}</b> | Alert window: <b>{alerts}</b>".format(
                    count=esc(macro_group.get("upcoming_count", 0)),
                    alerts=esc(macro_group.get("alert_count", 0)),
                ),
                "• Next: <code>{next}</code>".format(next=esc(next_line)),
            ]
        )
    for group in groups:
        group_lines = [
            "",
            "{icon} <b>{symbol} {tf}</b>".format(
                icon=status_icon(group.get("status")),
                symbol=esc(group.get("symbol", "")),
                tf=esc(group.get("timeframe", "")),
            ),
            "• State: <code>{status}</code>".format(status=esc(group.get("status", ""))),
            "• Data: <code>{data}</code> | Close: {close}".format(
                data=esc(group.get("data_state", "UNKNOWN")),
                close=esc(compact_utc(group.get("latest_candle_close_utc")) or "n/a"),
            ),
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
        "• Candle open: <b>{signal_time}</b>".format(
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
                current=fmt_float(signal.get("market_price_at_scan"), 4),
            ),
            "• Signal candle close: <code>{price}</code>".format(price=fmt_float(features.get("close"), 4)),
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


def format_market_pulse_message(event: dict[str, Any]) -> str:
    icon = "📈" if event.get("side") == "LONG" else "📉"
    return "\n".join([
        f"📡 <b>R25A MARKET PULSE | {esc(event.get('symbol'))}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{icon} Direction: <b>{esc(event.get('side'))}</b> | TF: <b>{esc(event.get('timeframe'))}</b>",
        "👀 <b>WATCH ONLY | Chua phai lenh vao trade</b>",
        "",
        f"• Move: <b>{float(event['return_pct']):+.2f}%</b>",
        f"• Candle close price: <code>{fmt_float(event.get('candle_close_price'))}</code>",
        f"• Volume z20: <code>{fmt_float(event.get('volume_z20'), 2)}</code>",
        f"• Breadth: <code>{event.get('market_breadth_count')}/{event.get('market_breadth_assets')}</code>",
        "",
        f"• Candle closed: <b>{esc(utc_vn_label(event.get('candle_close_time_utc')))}</b>",
        f"• Notify: <b>{esc(utc_vn_label(event.get('notify_time_utc')))}</b>",
        f"• Delay since close: <code>{duration_label(event.get('freshness_lag_seconds'))}</code>",
        "",
        "🔒 <b>PAPER WATCH / NO AUTO-TRADE</b>",
        clock_line(event.get("notify_time_utc")),
    ])


def format_liquidity_event_message(event: dict[str, Any]) -> str:
    features = event.get("features") or {}
    side = str(event.get("side", "")).upper()
    icon = "🟢📈" if side == "LONG" else "🔴📉" if side == "SHORT" else "🧲"
    return "\n".join(
        [
            f"🧲🔥 <b>R27A LIQUIDITY MAP — {esc(event.get('symbol'))}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"{icon} Bias: <b>{esc(side)}</b> | TF: <b>{esc(event.get('timeframe'))}</b>",
            "👀 <b>WATCH ONLY | Chua phai lenh vao trade</b>",
            "",
            "📊 <b>DATA</b>",
            "• Reason: <code>{reason}</code>".format(reason=esc(event.get("reason", ""))),
            "• Score: <code>{score}</code> | Confidence: <b>{confidence}</b>".format(
                score=fmt_float(event.get("score"), 2),
                confidence=esc(event.get("confidence", "n/a")),
            ),
            "• Price: <code>{price}</code> | Move 15m: <code>{move}</code>".format(
                price=fmt_float(features.get("price"), 4),
                move=fmt_signed_bps(safe_bps(features.get("return_fraction"))),
            ),
            "• Volume z20: <code>{volz}</code> | Taker imbalance: <code>{taker}</code>".format(
                volz=fmt_float(features.get("volume_z20"), 2),
                taker=fmt_float(features.get("taker_imbalance"), 3),
            ),
            "",
            "🧱 <b>LIQUIDITY HEATMAP</b>",
            "• Wall: <code>{side}</code> @ <code>{price}</code>".format(
                side=esc(features.get("binance_wall_side", "n/a")),
                price=fmt_float(features.get("binance_wall_price"), 4),
            ),
            "• Distance: <code>{distance}</code> bps | Notional: <code>{notional}</code>".format(
                distance=fmt_float(features.get("binance_wall_distance_bps"), 1),
                notional=human_usd(features.get("binance_wall_quote")),
            ),
            "• Depth 100bps: bid <code>{bid}</code> | ask <code>{ask}</code>".format(
                bid=human_usd(features.get("binance_bid_depth_100bps")),
                ask=human_usd(features.get("binance_ask_depth_100bps")),
            ),
            "• Book imbalance: <code>{imbalance}</code>".format(
                imbalance=fmt_float(features.get("binance_imbalance_100bps"), 3),
            ),
            "",
            "💥 <b>LIQUIDATION PRESSURE PROXY</b>",
            "• State: <code>{state}</code>".format(
                state=esc(features.get("liquidation_pressure_proxy", "UNKNOWN")),
            ),
            "• OI change 12x5m: <code>{oi12}</code>% | last: <code>{oi1}</code>%".format(
                oi12=fmt_float(features.get("open_interest_change_pct_12"), 2),
                oi1=fmt_float(features.get("open_interest_change_pct_1"), 2),
            ),
            "",
            "🌊 <b>HYPERLIQUID MAP</b>",
            "• State: <code>{state}</code> | Mid diff: <code>{diff}</code> bps".format(
                state=esc(features.get("hyperliquid_state", "n/a")),
                diff=fmt_float(features.get("hyperliquid_mid_diff_bps"), 1),
            ),
            "• HL wall: <code>{side}</code> | Distance: <code>{distance}</code> bps".format(
                side=esc(features.get("hyperliquid_wall_side", "n/a")),
                distance=fmt_float(features.get("hyperliquid_wall_distance_bps"), 1),
            ),
            "",
            "• Candle close: <b>{closed}</b>".format(
                closed=esc(utc_vn_label(event.get("candle_close_time_utc"))),
            ),
            "• Notify: <b>{notify}</b>".format(
                notify=esc(utc_vn_label(event.get("notify_time_utc"))),
            ),
            "",
            "🔒 <b>PAPER WATCH / NO AUTO-TRADE</b>",
            clock_line(event.get("notify_time_utc")),
        ]
    )


def format_macro_event_message(event: dict[str, Any]) -> str:
    priority = str(event.get("priority", "MEDIUM")).upper()
    icon = "🚨" if priority == "CRITICAL" else "🟠" if priority == "HIGH" else "🟡"
    minutes = event.get("minutes_until")
    if isinstance(minutes, (int, float)) and minutes < 0:
        timing = "Released/Live {age} ago".format(age=duration_label(abs(float(minutes)) * 60))
    elif isinstance(minutes, (int, float)):
        timing = "In {age}".format(age=duration_label(float(minutes) * 60))
    else:
        timing = "n/a"
    forecast = event.get("forecast_summary") or "No quantified forecast attached yet; official schedule alert only."
    forecast_sources = event.get("forecast_sources") or []
    source_line = ", ".join(str(item) for item in forecast_sources) if forecast_sources else event.get("source", "official schedule")
    return "\n".join(
        [
            f"{icon}🗓️ <b>R28A MACRO RISK WATCH — {esc(event.get('title'))}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📌 Priority: <b>{priority}</b> | Score: <code>{score}</code> | Phase: <b>{phase}</b>".format(
                priority=esc(priority),
                score=esc(event.get("impact_score", "n/a")),
                phase=esc(event.get("phase", "n/a")),
            ),
            "👀 <b>WATCH ONLY | Khong phai lenh vao trade</b>",
            "",
            "⏱️ <b>EVENT TIME</b>",
            "• Time: <b>{time}</b>".format(time=esc(utc_vn_label(event.get("event_time_utc")))),
            "• Status: <code>{timing}</code>".format(timing=esc(timing)),
            "• Category: <code>{category}</code>".format(category=esc(event.get("category", "n/a"))),
            "• Reference: <code>{reference}</code>".format(reference=esc(event.get("reference") or "n/a")),
            "",
            "🔮 <b>FORECAST / CONSENSUS</b>",
            "• {forecast}".format(forecast=esc(forecast)),
            "• Confidence: <code>{confidence}</code>".format(confidence=esc(event.get("forecast_confidence", "SCHEDULE_ONLY"))),
            "• Sources: <code>{sources}</code>".format(sources=esc(source_line)),
            "",
            "₿ <b>CRYPTO PLAYBOOK</b>",
            "• Expect volatility expansion, fakeout risk and liquidity sweeps around the release window.",
            "• Prefer waiting for candle close / liquidity confirmation before treating moves as directional.",
            "• This layer can override urgency, but it does not create an entry by itself.",
            "",
            "🧾 <b>WHY IT MATTERS</b>",
            "• {rationale}".format(rationale=esc(event.get("rationale", ""))),
            "",
            "🔒 <b>MACRO WATCH / NO AUTO-TRADE</b>",
            clock_line(event.get("notify_time_utc")),
        ]
    )


def safe_bps(value: Any) -> float:
    try:
        return float(value) * 10000.0
    except (TypeError, ValueError):
        return 0.0


def human_usd(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(number) >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:.0f}"


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
