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


SIDE_LABELS = {
    "LONG": "LONG (MUA)",
    "SHORT": "SHORT (BÁN)",
}


STATUS_LABELS = {
    "SIGNAL": "CÓ TÍN HIỆU",
    "NO_SIGNAL": "CHƯA CÓ TÍN HIỆU",
    "SUPPRESSED": "BỊ CHẶN",
    "ERROR": "LỖI",
    "INSUFFICIENT_HISTORY": "THIẾU LỊCH SỬ",
    "STALE_DATA": "DỮ LIỆU CŨ",
    "DATA_GAP": "HỞ DỮ LIỆU",
    "INVALID_DATA": "DỮ LIỆU LỖI",
    "DATA_NOT_READY": "DỮ LIỆU CHƯA SẴN SÀNG",
    "NO_EVENT": "CHƯA CÓ CẢNH BÁO",
    "UPCOMING": "SẮP DIỄN RA",
    "OK": "ỔN",
    "DEGRADED": "SUY GIẢM",
    "UNKNOWN": "CHƯA RÕ",
    "UNAVAILABLE": "KHÔNG SẴN SÀNG",
    "NEUTRAL": "TRUNG LẬP",
    "LONG_LIQUIDATION_PRESSURE": "ÁP LỰC THANH LÝ LONG",
    "SHORT_LIQUIDATION_PRESSURE": "ÁP LỰC THANH LÝ SHORT",
}


DATA_STATE_LABELS = {
    "FRESH": "MỚI",
    "STALE": "CŨ",
    "DEGRADED": "SUY GIẢM",
    "UNKNOWN": "CHƯA RÕ",
}


PRIORITY_LABELS = {
    "CRITICAL": "RẤT CAO",
    "HIGH": "CAO",
    "MEDIUM": "TRUNG BÌNH",
    "LOW": "THẤP",
}


CONFIDENCE_LABELS = {
    "NOWCAST": "NOWCAST",
    "CONSENSUS": "ĐỒNG THUẬN",
    "SCHEDULE_ONLY": "CHỈ THEO LỊCH",
    "LOW": "THẤP",
    "MEDIUM": "TRUNG BÌNH",
    "HIGH": "CAO",
}


MACRO_CATEGORY_LABELS = {
    "FOMC_RATE_DECISION": "Quyết định lãi suất FOMC",
    "FOMC_PRESS_CONFERENCE": "Họp báo FOMC",
    "CPI_INFLATION": "CPI / lạm phát",
    "PCE_INFLATION": "PCE / lạm phát Fed ưu tiên",
    "PPI_INFLATION": "PPI / lạm phát đầu vào",
    "NFP_LABOR": "NFP / việc làm phi nông nghiệp",
    "JOLTS_LABOR": "JOLTS / nhu cầu lao động",
    "GDP_GROWTH": "GDP / tăng trưởng",
    "RETAIL_SALES": "Doanh số bán lẻ",
    "DURABLE_GOODS": "Đơn hàng hàng bền lâu",
    "HOUSING": "Nhà ở",
    "TRADE_BALANCE": "Cán cân thương mại",
}


MACRO_TITLE_LABELS = {
    "Consumer Price Index": "Chỉ số giá tiêu dùng CPI",
    "Producer Price Index": "Chỉ số giá sản xuất PPI",
    "Employment Situation": "Báo cáo việc làm / NFP",
    "Job Openings and Labor Turnover Survey": "JOLTS / tuyển dụng và thôi việc",
    "FOMC Rate Decision + SEP/Dot Plot": "Quyết định lãi suất FOMC + SEP/Dot Plot",
    "FOMC Press Conference": "Họp báo FOMC",
}


REASON_LABELS = {
    "SHORT_LIQUIDATION_PRESSURE": "Áp lực thanh lý SHORT",
    "LONG_LIQUIDATION_PRESSURE": "Áp lực thanh lý LONG",
    "BID_WALL_SUPPORT": "Tường BID hỗ trợ giá",
    "ASK_WALL_RESISTANCE": "Tường ASK cản giá",
    "DEPTH_IMBALANCE": "Lệch thanh khoản sổ lệnh",
}


def side_label(value: Any) -> str:
    text = str(value or "").upper()
    return SIDE_LABELS.get(text, text or "n/a")


def status_label(value: Any) -> str:
    text = str(value or "").upper()
    return STATUS_LABELS.get(text, text or "n/a")


def data_state_label(value: Any) -> str:
    text = str(value or "").upper()
    return DATA_STATE_LABELS.get(text, text or "n/a")


def priority_label(value: Any) -> str:
    text = str(value or "").upper()
    return PRIORITY_LABELS.get(text, text or "n/a")


def confidence_label(value: Any) -> str:
    text = str(value or "").upper()
    return CONFIDENCE_LABELS.get(text, text or "n/a")


def reason_label(value: Any) -> str:
    text = str(value or "").upper()
    return REASON_LABELS.get(text, text or "n/a")


def macro_title_label(event: dict[str, Any]) -> str:
    title = str(event.get("title") or "")
    category = str(event.get("category") or "").upper()
    return MACRO_TITLE_LABELS.get(title) or MACRO_CATEGORY_LABELS.get(category) or title or "Sự kiện vĩ mô"


def macro_rationale_label(event: dict[str, Any]) -> str:
    category = str(event.get("category") or "").upper()
    rationale_by_category = {
        "FOMC_RATE_DECISION": "Quyết định của Fed có thể định giá lại thanh khoản USD, lợi suất và khẩu vị rủi ro của crypto.",
        "FOMC_PRESS_CONFERENCE": "Phần trả lời của Chủ tịch Fed có thể đảo chiều phản ứng đầu tiên sau tuyên bố lãi suất.",
        "CPI_INFLATION": "CPI bất ngờ cao/thấp có thể làm lợi suất, DXY và dòng tiền vào tài sản rủi ro biến động mạnh.",
        "PCE_INFLATION": "Core PCE là thước đo lạm phát Fed ưu tiên, nên ảnh hưởng trực tiếp tới kỳ vọng chính sách.",
        "PPI_INFLATION": "PPI cho thấy áp lực lạm phát đầu vào và có thể gợi ý hướng đi của PCE.",
        "NFP_LABOR": "Dữ liệu việc làm có thể làm thị trường định giá lại đường đi lãi suất của Fed.",
        "JOLTS_LABOR": "JOLTS phản ánh nhu cầu lao động, hữu ích để đọc sức nóng của thị trường việc làm.",
        "GDP_GROWTH": "GDP ảnh hưởng kỳ vọng tăng trưởng, lợi suất và khẩu vị rủi ro.",
        "RETAIL_SALES": "Doanh số bán lẻ đo sức cầu tiêu dùng, thường tác động tới lợi suất và tài sản rủi ro.",
        "DURABLE_GOODS": "Đơn hàng hàng bền lâu phản ánh chu kỳ đầu tư và kỳ vọng tăng trưởng.",
        "HOUSING": "Dữ liệu nhà ở là tín hiệu phụ về lãi suất, tín dụng và tăng trưởng.",
        "TRADE_BALANCE": "Cán cân thương mại ảnh hưởng theo dõi GDP và bối cảnh USD.",
    }
    return rationale_by_category.get(category) or str(event.get("rationale") or "")


def forecast_label(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "Chưa có dự báo định lượng; đây là cảnh báo theo lịch chính thức."
    replacements = {
        "Cleveland Fed nowcast:": "Nowcast Cleveland Fed:",
        "updated": "cập nhật",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


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


def compact_vn_label(value: Any) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        return compact_utc(value) or "n/a"
    return parsed.astimezone(timezone(timedelta(hours=7))).strftime("%d/%m %H:%M VN")


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
    return "🕒 UTC:{utc} | 🇻🇳 VN:{asia} | 🇪🇺 EU:{eu} | 🇺🇸 US:{us}".format(
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


def percent(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
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
        return "Bot R26A Lõi Chất Lượng"
    if text.startswith("R24A"):
        return "Bot R24A Chất Lượng Nghiêm Ngặt"
    if text.startswith("R23B"):
        return "Bot R23B Chất Lượng"
    if text.startswith("R22C"):
        return "Bot R22C Paper"
    if text.startswith("R15C"):
        return "Bot R15C Paper"
    if text.startswith("R14H"):
        return "Bot R14H Paper"
    return "Bot Tín Hiệu Paper"


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
        return "🔻🔴 <b>TÍN HIỆU PAPER SHORT (BÁN) — {symbol}</b> 🔴🔻".format(symbol=esc(symbol))
    if text == "LONG":
        return "🟢🔺 <b>TÍN HIỆU PAPER LONG (MUA) — {symbol}</b> 🔺🟢".format(symbol=esc(symbol))
    return "📡 <b>TÍN HIỆU PAPER — {symbol}</b>".format(symbol=esc(symbol))


def data_age_label(latest_closed_bar_utc: Any, scanned_utc: Any) -> str:
    latest = parse_utc(latest_closed_bar_utc)
    scanned = parse_utc(scanned_utc) or datetime.now(timezone.utc)
    if latest is None:
        return "n/a"
    minutes = max(int((scanned - latest).total_seconds() // 60), 0)
    if minutes < 120:
        return f"{minutes} phút"
    hours = minutes // 60
    remainder = minutes % 60
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def format_startup_message(
    *,
    strategy_id: str,
    scan_interval_seconds: int,
    heartbeat_interval_seconds: int,
    scan_summary: dict[str, Any] | None = None,
    macro_events: list[dict[str, Any]] | None = None,
    historical_metrics: dict[str, Any] | None = None,
) -> str:
    summary = scan_summary or {}
    groups = summary.get("groups", [])
    pulse_groups = summary.get("pulse_groups", [])
    liquidity_groups = summary.get("liquidity_groups", [])
    readiness = summary.get("trade_readiness") or {}
    metrics = readiness.get("metrics") or {}
    history = historical_metrics or summary.get("report_metrics") or {}
    history_passed = int(history.get("historical_checks_passed") or 0)
    history_total = int(history.get("historical_checks_total") or 0)
    history_label = "ĐẠT" if history.get("historical_gate_pass") else "CHƯA ĐẠT"
    trade_a_status = "MỞ" if readiness.get("trade_a_plus_eligible") else "KHÓA"
    history_line = (
        "🧪 Trade A+: <b>{status}</b> | Backtest R31A: <b>{history} ({passed}/{total})</b>".format(
            status=trade_a_status,
            history=history_label,
            passed=esc(history_passed),
            total=esc(history_total),
        )
        if history_total
        else f"🧪 Trade A+: <b>{trade_a_status}</b>"
    )
    scan_ok = bool(groups) and all(
        group.get("status") not in {"ERROR", "INVALID_DATA", "DATA_GAP", "STALE_DATA", "INSUFFICIENT_HISTORY"}
        for group in groups
    )
    pulse_ready = sum(
        group.get("data_state") == "FRESH"
        and group.get("status") not in {"ERROR", "INVALID_DATA", "DATA_GAP", "INSUFFICIENT_HISTORY"}
        for group in pulse_groups
    )
    liquidity_ready = sum(
        group.get("data_state") == "FRESH" and group.get("status") not in {"DATA_NOT_READY", "ERROR"}
        for group in liquidity_groups
    )
    status = "ONLINE / DỮ LIỆU SẴN SÀNG" if scan_ok else "ONLINE / ĐANG KIỂM TRA DỮ LIỆU"
    lines = [
        f"📡 <b>{bot_label(strategy_id).upper()} ĐÃ KHỞI ĐỘNG</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✅ Trạng thái: <b>{status}</b>",
        "📌 Chế độ: <b>PAPER / WATCH ONLY</b>",
        "⏱️ Quét: <b>{scan}</b> | Báo sống: <b>{heartbeat}</b>".format(
            scan=esc(interval_label(scan_interval_seconds)),
            heartbeat=esc(interval_label(heartbeat_interval_seconds)),
        ),
        "📊 Tín hiệu: <b>BTC, ETH 1h/4h</b> | <b>SOL, BNB 4h</b>",
        "🔎 Theo dõi: xung lực <b>{pulse}/{pulse_total}</b> | thanh khoản <b>{liq}/{liq_total}</b> feed".format(
            pulse=esc(pulse_ready),
            pulse_total=esc(len(pulse_groups) or 12),
            liq=esc(liquidity_ready),
            liq_total=esc(len(liquidity_groups) or 4),
        ),
        history_line,
        "📋 Forward: <code>{days} ngày</code> | <code>{trades} lệnh đóng</code> (đang giám sát)".format(
            days=fmt_float(metrics.get("forward_days"), 1),
            trades=esc(metrics.get("closed_trades", 0)),
        ),
    ]
    events = sorted(
        macro_events or [],
        key=lambda item: (int(item.get("event_time_ms") or 0), -int(item.get("impact_score") or 0)),
    )
    if events:
        lines.extend(["", "🗓️ <b>VĨ MÔ CẦN LƯU Ý ({count})</b>".format(count=len(events))])
        for event in events:
            priority = str(event.get("priority", "MEDIUM")).upper()
            icon = "🔴" if priority == "CRITICAL" else "🟠" if priority == "HIGH" else "🟡"
            lines.append(
                "• {icon} <b>{title}</b> | {time} | {phase}".format(
                    icon=icon,
                    title=esc(macro_title_label(event)),
                    time=esc(compact_vn_label(event.get("event_time_utc"))),
                    phase=esc(event.get("phase", "n/a")),
                )
            )
    else:
        lines.extend(["", "🗓️ Vĩ mô: <b>không có cảnh báo mới</b>"])
    lines.extend(
        [
            "",
            "🔒 <b>KHÔNG TỰ ĐẶT LỆNH / TIỀN THẬT ĐANG KHÓA</b>",
            clock_line(summary.get("time_utc")),
        ]
    )
    return "\n".join(lines)


def format_heartbeat_message(scan_summary: dict[str, Any]) -> str:
    groups = scan_summary.get("groups", [])
    strategy_id = scan_summary.get("strategy_id", "")
    scanned_utc = scan_summary.get("time_utc")
    latest_candle = ""
    latest_dt = None
    for group in groups:
        candle_value = group.get("latest_candle_close_utc") or group.get("latest_closed_bar_utc")
        parsed = parse_utc(candle_value)
        if parsed is not None and (latest_dt is None or parsed > latest_dt):
            latest_dt = parsed
            latest_candle = compact_utc(candle_value)
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
    rules_scanned = sum(int(float(group.get("candidate_count") or 0)) for group in groups)
    readiness = scan_summary.get("trade_readiness") or {}
    readiness_metrics = readiness.get("metrics") or {}
    history = scan_summary.get("report_metrics") or {}
    history_passed = int(history.get("historical_checks_passed") or 0)
    history_total = int(history.get("historical_checks_total") or 0)
    history_label = "ĐẠT" if history.get("historical_gate_pass") else "CHƯA ĐẠT"
    readiness_status = "ĐỦ ĐIỀU KIỆN TRADE A+" if readiness.get("trade_a_plus_eligible") else "KHÓA / WATCH ONLY"
    history_suffix = (
        " | R31A: <b>{history} {passed}/{total}</b>".format(
            history=history_label,
            passed=esc(history_passed),
            total=esc(history_total),
        )
        if history_total
        else ""
    )
    healthy_markets = sum(
        group.get("data_state") == "FRESH"
        and group.get("status") not in {"ERROR", "INVALID_DATA", "DATA_GAP", "STALE_DATA", "INSUFFICIENT_HISTORY"}
        for group in groups
    )
    lines = [
        f"💓 <b>{bot_label(strategy_id).upper()} — BÁO SỐNG</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "✅ Hệ thống: <b>{state}</b> | Dữ liệu: <b>{data}</b>".format(
            state=esc(status_label(state)),
            data=esc(data_state_label(data_state)),
        ),
        "📊 Thị trường: <b>{ready}/{total}</b> sẵn sàng | Luật: <b>{rules}</b>".format(
            ready=esc(healthy_markets),
            total=esc(len(groups)),
            rules=esc(rules_scanned),
        ),
        "⏱️ Nến mới nhất: <b>{candle}</b> | Quét: <b>{duration}</b>".format(
            candle=esc(latest_candle or "n/a"),
            duration=esc(duration_label(scan_summary.get("scan_duration_seconds"))),
        ),
        "📨 Mới: tín hiệu <b>{signals}</b> | bị chặn <b>{blocked}</b> | vị thế mở <b>{active}</b>".format(
            signals=esc(scan_summary.get("new_signal_count", 0)),
            blocked=esc(scan_summary.get("suppressed_signal_count", 0)),
            active=esc(scan_summary.get("active_position_count", 0)),
        ),
        "👁️ Watch <b>{watch}</b> | Trade A+ <b>{aplus}</b> | vừa đóng <b>{closed}</b>".format(
            watch=esc(scan_summary.get("new_watch_signal_count", 0)),
            aplus=esc(scan_summary.get("new_trade_a_plus_count", 0)),
            closed=esc(scan_summary.get("closed_signal_count", 0)),
        ),
        "",
        "🧪 <b>TRADE A+: {status}</b>{history_suffix}".format(
            status=esc(readiness_status),
            history_suffix=history_suffix,
        ),
        "• Forward <code>{days} ngày</code> | Đóng <code>{trades}</code> | Win <code>{win}</code> | PF12 <code>{pf12}</code>".format(
            days=fmt_float(readiness_metrics.get("forward_days"), 1),
            trades=esc(readiness_metrics.get("closed_trades", 0)),
            win=esc(percent(readiness_metrics.get("win_rate"))),
            pf12=fmt_float(readiness_metrics.get("profit_factor_12bps"), 2),
        ),
    ]
    if pulse_groups:
        fresh = sum(g.get("data_state") == "FRESH" and g.get("status") not in {"INSUFFICIENT_HISTORY", "INVALID_DATA", "DATA_GAP"} for g in pulse_groups)
        lines.append(f"📡 Xung lực: <b>{fresh}/{len(pulse_groups)}</b> feed sẵn sàng")
    if liquidity_groups:
        ready = sum(g.get("data_state") == "FRESH" and g.get("status") not in {"DATA_NOT_READY", "ERROR"} for g in liquidity_groups)
        strongest = max(
            liquidity_groups,
            key=lambda group: float((group.get("features") or {}).get("binance_wall_intensity") or 0.0),
        )
        strong_features = strongest.get("features") or {}
        lines.append(
            "🧲 Thanh khoản: <b>{ready}/{total}</b> feed | Tường mạnh: <b>{symbol} {side}</b> @ <code>{price}</code>".format(
                ready=esc(ready),
                total=esc(len(liquidity_groups)),
                symbol=esc(strongest.get("symbol", "n/a")),
                side=esc(strong_features.get("binance_wall_side", "n/a")),
                price=fmt_float(strong_features.get("binance_wall_price"), 4),
            )
        )
    if macro_groups:
        macro_group = macro_groups[0]
        next_event = macro_group.get("next_event") or {}
        next_line = "n/a"
        if next_event:
            next_line = "{priority} {title} | {time}".format(
                priority=priority_label(next_event.get("priority", "n/a")),
                title=macro_title_label(next_event),
                time=utc_vn_label(next_event.get("event_time_utc")),
            )
        lines.append(
            "🗓️ Vĩ mô: <b>{alerts}</b> cảnh báo | Sắp tới: <b>{next}</b>".format(
                alerts=esc(macro_group.get("alert_count", 0)),
                next=esc(next_line),
            )
        )
    problem_groups = [
        group
        for group in groups
        if group.get("status") in {"ERROR", "INVALID_DATA", "DATA_GAP", "STALE_DATA", "INSUFFICIENT_HISTORY"}
    ]
    if problem_groups:
        lines.extend(["", "⚠️ <b>CẦN KIỂM TRA</b>"])
        for group in problem_groups:
            lines.append(
                "• {symbol} {tf}: <code>{status}</code>".format(
                    symbol=esc(group.get("symbol", "")),
                    tf=esc(group.get("timeframe", "")),
                    status=esc(status_label(group.get("status", ""))),
                )
            )
    lines.extend(
        [
            "",
            "🔒 <b>PAPER ONLY / KHÔNG TỰ ĐẶT LỆNH</b>",
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
    signal_tier = str(signal.get("signal_tier", "WATCH")).upper()
    tier_title = "ỨNG VIÊN TRADE A+ (VẪN PAPER)" if signal_tier == "TRADE_A_PLUS" else "WATCH PAPER / CHƯA ĐỦ CHUẨN TIỀN THẬT"
    tier_icon = "🏅" if signal_tier == "TRADE_A_PLUS" else "👁️"
    side_icon = "📈" if side == "LONG" else "📉" if side == "SHORT" else "📡"
    quality_lines = [
        "• Ứng viên: <code>{candidate_id}</code>".format(
            candidate_id=esc(candidate.get("candidate_id", "")),
        ),
        "• Điểm: <code>{score}</code>".format(
            score=fmt_float(candidate.get("selection_score"), 4),
        ),
        "• Điều kiện kích hoạt: <code>{family}</code>".format(
            family=esc(candidate.get("family", features.get("family", ""))),
        ),
    ]
    if features.get("market_breadth_count") is not None:
        quality_lines.extend(
            [
                "• Độ rộng thị trường: <code>{count}/{assets}</code> >= <code>{need}</code>".format(
                    count=fmt_float(features.get("market_breadth_count"), 0),
                    assets=fmt_float(features.get("market_breadth_assets"), 0),
                    need=fmt_float(features.get("breadth_n"), 0),
                ),
                "• Ngưỡng độ rộng: <code>{minv}</code> | Trung bình thị trường: <code>{mean}</code>".format(
                    minv=fmt_float(features.get("breadth_min"), 4),
                    mean=fmt_float(features.get("market_directional_mean"), 4),
                ),
            ]
        )
    if features.get("flow_thr") is not None:
        quality_lines.append(
            "• Dòng taker: <code>{flow}</code> >= <code>{thr}</code>".format(
                flow=fmt_float(features.get("flow_directional"), 4),
                thr=fmt_float(features.get("flow_thr"), 4),
            )
        )
    if features.get("pullback_min") is not None:
        quality_lines.append(
            "• Nhịp kéo ngược/reclaim: <code>{pullback}</code> >= <code>{need}</code> | Reclaim: <code>{reclaim}</code>".format(
                pullback=fmt_float(features.get("pullback"), 4),
                need=fmt_float(features.get("pullback_min"), 4),
                reclaim=esc(features.get("reclaim")),
            )
        )
    if features.get("volz_min") is not None:
        quality_lines.append(
            "• Z khối lượng 20: <code>{volume_z}</code> >= <code>{min_z}</code>".format(
                volume_z=fmt_float(features.get("quote_volume_prior_z_20"), 2),
                min_z=fmt_float(features.get("volz_min"), 2),
            )
        )
    if features.get("quality_realized_vol_24_min") is not None:
        quality_lines.append(
            "• Biến động thực 24 nến: <code>{rv}</code> >= <code>{need}</code>".format(
                rv=fmt_float(features.get("realized_vol_24"), 4),
                need=fmt_float(features.get("quality_realized_vol_24_min"), 4),
            )
        )
    quality_lines.append(
        "• Mở nến tín hiệu: <b>{signal_time}</b>".format(
            signal_time=esc(utc_vn_label(signal.get("signal_time_utc", ""))),
        )
    )
    return "\n".join(
        [
            side_banner(signal.get("symbol", ""), side),
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "{icon} <b>{title}</b>".format(icon=tier_icon, title=esc(tier_title)),
            "{icon} Hướng: <b>{side}</b>".format(icon=side_icon, side=esc(side_label(side))),
            "⏱️ Khung thời gian: <b>{tf}</b>".format(tf=esc(signal.get("timeframe", ""))),
            "🧠 Mô hình: <code>{engine}</code>".format(engine=esc(engine_label(strategy_id))),
            "🧩 Sleeve: <code>{sleeve}</code>".format(sleeve=esc(signal.get("sleeve_id", features.get("sleeve_id", "")))),
            "⚖️ Đơn vị rủi ro: <code>{risk}</code>".format(risk=fmt_float(signal.get("risk_fraction", features.get("risk_fraction")), 2)),
            "",
            "📍 <b>KẾ HOẠCH GIÁ</b>",
            "• Giá hiện tại: <code>{current}</code>".format(
                current=fmt_float(signal.get("market_price_at_scan"), 4),
            ),
            "• Giá đóng nến tín hiệu: <code>{price}</code>".format(price=fmt_float(features.get("close"), 4)),
            "• Giá vào giả định: <code>{entry_price}</code>".format(
                entry_price=esc(signal.get("entry_price", "pending_next_open")),
            ),
            "• Mô hình vào lệnh: <code>NEXT_OPEN</code>",
            "• Thời điểm vào giả định: <b>{entry_time}</b>".format(
                entry_time=esc(utc_vn_label(signal.get("entry_time_utc", ""))),
            ),
            "• Thời điểm gửi tín hiệu: <b>{notify_time}</b>".format(
                notify_time=esc(utc_vn_label(notify_time)),
            ),
            "• Độ trễ sau điểm vào: <code>{age}</code> | Trễ tối đa: <code>{max_lag}</code>".format(
                age=esc(duration_label(signal.get("entry_lag_seconds"))),
                max_lag=esc(duration_label(signal.get("max_entry_lag_seconds"))),
            ),
            "• Giá thị trường lúc gửi: <code>{market}</code>".format(
                market=fmt_float(signal.get("market_price_at_scan"), 4),
            ),
            "• Biến động sau điểm vào: <code>{move}</code> | Mức chase tối đa: <code>{max_chase}</code>".format(
                move=esc(fmt_signed_bps(signal.get("entry_price_move_bps"))),
                max_chase=esc(fmt_signed_bps(signal.get("max_chase_bps"))),
            ),
            "• Mô hình thoát: <code>HOLD_{hold}_BARS</code>".format(hold=esc(candidate.get("hold_bars", ""))),
            "• Thoát dự kiến: <b>{exit_time}</b>".format(
                exit_time=esc(utc_vn_label(signal.get("planned_exit_time_utc", ""))),
            ),
            "",
            "📊 <b>CHẤT LƯỢNG TÍN HIỆU</b>",
            *quality_lines,
            "",
            "🧪 Cửa chất lượng: <code>{gate}</code> | Trạng thái: <code>{status}</code>".format(
                gate=esc(signal.get("trade_readiness_gate_id", "R30A_FORWARD_TRADE_A_PLUS_GATE")),
                status=esc(signal.get("trade_readiness_status", "WATCH_ONLY")),
            ),
            "• Cho phép tiền thật: <b>KHÔNG</b>",
            "",
            "🔒 <b>CHỈ PAPER / KHÔNG TỰ ĐẶT LỆNH</b>",
            clock_line(notify_time),
        ]
    )


def format_market_pulse_message(event: dict[str, Any]) -> str:
    icon = "📈" if event.get("side") == "LONG" else "📉"
    return "\n".join([
        f"📡 <b>R25A XUNG LỰC THỊ TRƯỜNG | {esc(event.get('symbol'))}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{icon} Hướng: <b>{esc(side_label(event.get('side')))}</b> | Khung: <b>{esc(event.get('timeframe'))}</b>",
        "👀 <b>CHỈ THEO DÕI | Chưa phải lệnh vào trade</b>",
        "",
        f"• Biến động nến: <b>{float(event['return_pct']):+.2f}%</b>",
        f"• Giá đóng nến: <code>{fmt_float(event.get('candle_close_price'))}</code>",
        f"• Z khối lượng 20: <code>{fmt_float(event.get('volume_z20'), 2)}</code>",
        f"• Độ rộng thị trường: <code>{event.get('market_breadth_count')}/{event.get('market_breadth_assets')}</code>",
        "",
        f"• Nến đóng lúc: <b>{esc(utc_vn_label(event.get('candle_close_time_utc')))}</b>",
        f"• Gửi tín hiệu lúc: <b>{esc(utc_vn_label(event.get('notify_time_utc')))}</b>",
        f"• Độ trễ sau đóng nến: <code>{duration_label(event.get('freshness_lag_seconds'))}</code>",
        "",
        "🔒 <b>THEO DÕI PAPER / KHÔNG TỰ ĐẶT LỆNH</b>",
        clock_line(event.get("notify_time_utc")),
    ])


def format_liquidity_event_message(event: dict[str, Any]) -> str:
    features = event.get("features") or {}
    side = str(event.get("side", "")).upper()
    icon = "🟢📈" if side == "LONG" else "🔴📉" if side == "SHORT" else "🧲"
    return "\n".join(
        [
            f"🧲🔥 <b>R27A BẢN ĐỒ THANH KHOẢN — {esc(event.get('symbol'))}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"{icon} Nghiêng về: <b>{esc(side_label(side))}</b> | Khung: <b>{esc(event.get('timeframe'))}</b>",
            "👀 <b>CHỈ THEO DÕI | Chưa phải lệnh vào trade</b>",
            "",
            "📊 <b>DỮ LIỆU</b>",
            "• Lý do: <code>{reason}</code>".format(reason=esc(reason_label(event.get("reason", "")))),
            "• Điểm: <code>{score}</code> | Độ tin cậy: <b>{confidence}</b>".format(
                score=fmt_float(event.get("score"), 2),
                confidence=esc(confidence_label(event.get("confidence", "n/a"))),
            ),
            "• Giá: <code>{price}</code> | Biến động 15m: <code>{move}</code>".format(
                price=fmt_float(features.get("price"), 4),
                move=fmt_signed_bps(safe_bps(features.get("return_fraction"))),
            ),
            "• Z khối lượng 20: <code>{volz}</code> | Lệch taker: <code>{taker}</code>".format(
                volz=fmt_float(features.get("volume_z20"), 2),
                taker=fmt_float(features.get("taker_imbalance"), 3),
            ),
            "",
            "🧱 <b>HEATMAP THANH KHOẢN</b>",
            "• Tường thanh khoản: <code>{side}</code> @ <code>{price}</code>".format(
                side=esc(features.get("binance_wall_side", "n/a")),
                price=fmt_float(features.get("binance_wall_price"), 4),
            ),
            "• Khoảng cách: <code>{distance}</code> bps | Giá trị: <code>{notional}</code>".format(
                distance=fmt_float(features.get("binance_wall_distance_bps"), 1),
                notional=human_usd(features.get("binance_wall_quote")),
            ),
            "• Độ sâu trong 100bps: bid <code>{bid}</code> | ask <code>{ask}</code>".format(
                bid=human_usd(features.get("binance_bid_depth_100bps")),
                ask=human_usd(features.get("binance_ask_depth_100bps")),
            ),
            "• Lệch sổ lệnh: <code>{imbalance}</code>".format(
                imbalance=fmt_float(features.get("binance_imbalance_100bps"), 3),
            ),
            "",
            "💥 <b>ƯỚC LƯỢNG ÁP LỰC THANH LÝ</b>",
            "• Trạng thái: <code>{state}</code>".format(
                state=esc(status_label(features.get("liquidation_pressure_proxy", "UNKNOWN"))),
            ),
            "• Thay đổi OI 12x5m: <code>{oi12}</code>% | gần nhất: <code>{oi1}</code>%".format(
                oi12=fmt_float(features.get("open_interest_change_pct_12"), 2),
                oi1=fmt_float(features.get("open_interest_change_pct_1"), 2),
            ),
            "",
            "🌊 <b>BẢN ĐỒ HYPERLIQUID</b>",
            "• Trạng thái: <code>{state}</code> | Lệch mid: <code>{diff}</code> bps".format(
                state=esc(status_label(features.get("hyperliquid_state", "n/a"))),
                diff=fmt_float(features.get("hyperliquid_mid_diff_bps"), 1),
            ),
            "• Tường HL: <code>{side}</code> | Khoảng cách: <code>{distance}</code> bps".format(
                side=esc(features.get("hyperliquid_wall_side", "n/a")),
                distance=fmt_float(features.get("hyperliquid_wall_distance_bps"), 1),
            ),
            "",
            "• Nến đóng lúc: <b>{closed}</b>".format(
                closed=esc(utc_vn_label(event.get("candle_close_time_utc"))),
            ),
            "• Gửi tín hiệu lúc: <b>{notify}</b>".format(
                notify=esc(utc_vn_label(event.get("notify_time_utc"))),
            ),
            "",
            "🔒 <b>THEO DÕI PAPER / KHÔNG TỰ ĐẶT LỆNH</b>",
            clock_line(event.get("notify_time_utc")),
        ]
    )


def format_macro_digest_message(events: list[dict[str, Any]]) -> str:
    ordered = sorted(
        events,
        key=lambda item: (int(item.get("event_time_ms") or 0), -int(item.get("impact_score") or 0)),
    )
    lines = [
        "🗓️⚠️ <b>CẢNH BÁO VĨ MÔ — {count} SỰ KIỆN</b>".format(count=len(ordered)),
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for event in ordered:
        priority = str(event.get("priority", "MEDIUM")).upper()
        icon = "🔴" if priority == "CRITICAL" else "🟠" if priority == "HIGH" else "🟡"
        lines.append(
            "• {icon} <b>{title}</b> | {time} | {phase}".format(
                icon=icon,
                title=esc(macro_title_label(event)),
                time=esc(compact_vn_label(event.get("event_time_utc"))),
                phase=esc(event.get("phase", "n/a")),
            )
        )
    lines.extend(
        [
            "",
            "⚠️ Có thể tăng biến động, fakeout và quét thanh khoản quanh giờ công bố.",
            "👀 <b>CHỈ THEO DÕI / KHÔNG PHẢI LỆNH TRADE</b>",
            clock_line(ordered[0].get("notify_time_utc") if ordered else None),
        ]
    )
    return "\n".join(lines)


def format_macro_event_message(event: dict[str, Any]) -> str:
    priority = str(event.get("priority", "MEDIUM")).upper()
    icon = "🚨" if priority == "CRITICAL" else "🟠" if priority == "HIGH" else "🟡"
    minutes = event.get("minutes_until")
    if isinstance(minutes, (int, float)) and minutes < 0:
        timing = "Đã qua/đang live {age}".format(age=duration_label(abs(float(minutes)) * 60))
    elif isinstance(minutes, (int, float)):
        timing = "Còn {age}".format(age=duration_label(float(minutes) * 60))
    else:
        timing = "n/a"
    forecast = forecast_label(event.get("forecast_summary"))
    forecast_sources = event.get("forecast_sources") or []
    source_line = ", ".join(str(item) for item in forecast_sources) if forecast_sources else event.get("source", "lịch chính thức")
    return "\n".join(
        [
            f"{icon}🗓️ <b>R28A THEO DÕI RỦI RO VĨ MÔ — {esc(macro_title_label(event))}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📌 Mức ưu tiên: <b>{priority}</b> | Điểm tác động: <code>{score}</code> | Pha: <b>{phase}</b>".format(
                priority=esc(priority_label(priority)),
                score=esc(event.get("impact_score", "n/a")),
                phase=esc(event.get("phase", "n/a")),
            ),
            "👀 <b>CHỈ THEO DÕI | Chưa phải lệnh vào trade</b>",
            "",
            "⏱️ <b>THỜI ĐIỂM SỰ KIỆN</b>",
            "• Thời gian: <b>{time}</b>".format(time=esc(utc_vn_label(event.get("event_time_utc")))),
            "• Trạng thái: <code>{timing}</code>".format(timing=esc(timing)),
            "• Nhóm: <code>{category}</code>".format(category=esc(MACRO_CATEGORY_LABELS.get(str(event.get("category", "")).upper(), event.get("category", "n/a")))),
            "• Kỳ dữ liệu: <code>{reference}</code>".format(reference=esc(event.get("reference") or "n/a")),
            "",
            "🔮 <b>DỰ BÁO / ĐỒNG THUẬN</b>",
            "• {forecast}".format(forecast=esc(forecast)),
            "• Độ tin cậy: <code>{confidence}</code>".format(confidence=esc(confidence_label(event.get("forecast_confidence", "SCHEDULE_ONLY")))),
            "• Nguồn: <code>{sources}</code>".format(sources=esc(source_line)),
            "",
            "₿ <b>KỊCH BẢN CRYPTO</b>",
            "• Dự kiến biến động mở rộng, dễ có fakeout và quét thanh khoản quanh thời điểm công bố.",
            "• Ưu tiên chờ nến đóng / xác nhận thanh khoản trước khi coi nhịp chạy là xu hướng thật.",
            "• Lớp vĩ mô này chỉ tăng mức cảnh giác, không tự tạo điểm vào lệnh.",
            "",
            "🧾 <b>VÌ SAO QUAN TRỌNG</b>",
            "• {rationale}".format(rationale=esc(macro_rationale_label(event))),
            "",
            "🔒 <b>THEO DÕI VĨ MÔ / KHÔNG TỰ ĐẶT LỆNH</b>",
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
