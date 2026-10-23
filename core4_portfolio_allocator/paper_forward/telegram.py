from __future__ import annotations

import html
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .decision_support import HISTORICAL_REFERENCE, forward_assessment


VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")
V17_HISTORICAL_REFERENCE = {
    "trades_per_week": 0.815,
    "win_rate": 0.6349,
    "profit_factor": 1.968,
    "sharpe": 1.103,
    "max_drawdown": -0.0262,
}
V20_HISTORICAL_REFERENCE = {
    "trades": 221,
    "trades_per_week": 0.737,
    "win_rate": 0.611,
    "profit_factor": 1.956,
    "sharpe": 1.131,
    "max_drawdown": -0.0358,
    "stress_profit_factor": 1.585,
    "stress_max_drawdown": -0.0392,
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def price(value: Any) -> str:
    number = float(value)
    if number >= 10_000:
        return f"{number:,.2f}"
    if number >= 100:
        return f"{number:,.3f}"
    return f"{number:,.4f}"


def display_time(iso_value: str) -> str:
    value = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    utc = value.astimezone(timezone.utc)
    vn = value.astimezone(VIETNAM)
    return f"UTC {utc:%Y-%m-%d %H:%M} | VN {vn:%Y-%m-%d %H:%M}"


def source_summary(scan: dict[str, Any]) -> tuple[int, int]:
    groups = scan.get("groups", [])
    trusted = sum(group.get("source_trusted") is True for group in groups)
    fallback = sum(group.get("source_trusted") is False for group in groups)
    return trusted, fallback


def quality_display(value: Any) -> str:
    label = str(value or "V7 STANDARD")
    if label in {"A+ CROSS-MARKET", "A_PLUS_CROSS_MARKET"}:
        return "XÁC NHẬN CHÉO"
    return label


def bot_display(item: dict[str, Any]) -> str:
    if (
        item.get("shadow_version") == "V20"
        or item.get("strategy_id") == "CORE4_V20_TREND_BREADTH2_SHADOW"
    ):
        return "CORE4 V20 SHADOW"
    if item.get("shadow_only"):
        return "CORE4 V17 SHADOW"
    return "CORE4 V7"


def readiness_display(band: Any) -> tuple[str, str]:
    return {
        "TESTING": ("🔴", "ĐANG CHẠM NGƯỠNG"),
        "NEAR": ("🟠", "GẦN"),
        "WATCH": ("🟡", "THEO DÕI"),
        "WAIT": ("⚪", "CÒN XA"),
    }.get(str(band), ("⚪", "CHƯA RÕ"))


def format_startup(scan: dict[str, Any]) -> str:
    ready = sum(group.get("status") == "READY" for group in scan.get("groups", []))
    trusted, fallback = source_summary(scan)
    active = scan.get("state", {}).get("active_positions", [])
    v21 = scan.get("v21_readiness", {})
    lines = [
            "💎📡 <b>CORE4 V7 PAPER-FORWARD ĐÃ KHỞI ĐỘNG</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"✅ Hệ thống: <b>{'ỔN' if scan.get('status') == 'OK' else 'SUY GIẢM'}</b> | Dữ liệu: <b>{ready}/4</b>",
            "📊 Thị trường: <b>BTC / ETH / SOL / BNB</b> | Khung: <b>1D</b>",
            "↕️ Hướng: <b>LONG + SHORT</b> | Quét: <b>mỗi 1 phút</b>",
            "🎯 Kế hoạch: <b>Entry + SL + TP1/TP2/TP3 + giữ tối đa 90 ngày</b>",
            "🧭 Phân tầng: <b>STANDARD</b> | <b>XÁC NHẬN CHÉO khi ≥2 coin</b>",
            "⚠️ Tín hiệu cùng kỳ là một cụm tương quan, không phải các cược độc lập.",
            "🧪 V17 shadow: <b>BNB/SOL 1D nhanh + xác nhận ≥2/4</b> | rủi ro <b>0,105%/lệnh</b>",
            "🧭 V20 shadow: <b>CORE + FAST theo TREND_BREADTH2</b> | ledger riêng / watch only",
            "🛡️ V21 go-live: <b>KHÓA</b> | đạt <b>{passed}/{total}</b> cổng | Forward <code>{days:.1f}/90 ngày</code> | Cụm <code>{clusters}/20</code>".format(
                passed=int(v21.get("passed_checks", 0)),
                total=int(v21.get("total_checks", 11)),
                days=float(v21.get("forward", {}).get("observation_days", 0.0)),
                clusters=int(v21.get("forward", {}).get("clusters", 0)),
            ),
            "🧭 MTF 1h/4h: <b>CHỈ BỐI CẢNH / KHÔNG TỰ SINH LỆNH</b>",
            f"🧾 Nguồn Binance Futures xác minh: <b>{trusted}/4</b>",
            f"📂 Vị thế đang mở: <b>{len(active)}</b>",
    ]
    for position in active[:3]:
        lines.append(
            "• {symbol} <b>{side}</b> @ <code>{entry}</code> | SL <code>{stop}</code> | {status}".format(
                symbol=esc(position["symbol"]),
                side=esc(position["side"]),
                entry=price(position["entry"]),
                stop=price(position["current_stop"]),
                status=esc(position.get("status", "OPEN")),
            ),
        )
    if fallback:
        lines.append(
            f"⚠️ Spot fallback: <b>{fallback}/4</b> | Tín hiệu Trade bị khóa để bảo toàn chất lượng"
        )
    lines.extend(
        [
            "🧪 Trạng thái: <b>PAPER-FORWARD / KHÔNG ĐẶT LỆNH</b>",
            "🔐 Cấu hình V7 đã khóa SHA-256; bot R26A cũ hoạt động độc lập.",
            f"🕒 {esc(display_time(scan['time_utc']))}",
        ]
    )
    return "\n".join(lines)


def format_signal(signal: dict[str, Any]) -> str:
    plan = signal["plan"]
    is_shadow = bool(signal.get("shadow_only", False))
    is_long = plan["side"] == "LONG"
    header = "🟢🔺" if is_long else "🔴🔻"
    action = "LONG (MUA)" if is_long else "SHORT (BÁN)"
    quality_tier = signal.get("quality_tier", "STANDARD")
    is_cross_market = quality_tier == "A_PLUS_CROSS_MARKET"
    is_v20 = signal.get("shadow_version") == "V20"
    sleeve = str(signal.get("sleeve") or "")
    quality_label = (
        f"V20 {sleeve} PAPER-SHADOW"
        if is_v20
        else "V17 THỬ NGHIỆM SHADOW"
        if is_shadow
        else "XÁC NHẬN CHÉO" if is_cross_market else "V7 STANDARD"
    )
    quality_icon = "🧪" if is_shadow else "💎" if is_cross_market else "📘"
    bot_label = bot_display(signal)
    if not is_shadow:
        bot_label = f"{bot_label} PAPER"
    risk_pct = 100.0 * float(plan.get("capital_at_risk_fraction", 0.0035))
    exit_channel = int(signal.get("exit_channel", 20))
    confirmations = ", ".join(
        symbol.replace("USDT", "")
        for symbol in signal.get("cross_market_symbols", [plan["symbol"]])
    )
    return "\n".join(
        [
            f"{header} <b>{bot_label} {action} — {esc(plan['symbol'])}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "✅ <b>TÍN HIỆU PAPER-FORWARD HỢP LỆ</b>",
            f"⏱️ Khung: <b>1D</b> | Hướng: <b>{action}</b>",
            f"{quality_icon} Phân loại: <b>{quality_label}</b>",
            f"• Xác nhận chéo: <b>{signal.get('cross_market_count', 1)}/4</b> | {esc(confirmations)}",
            (
                "• V20 đã qua 7/7 kiểm định robustness; vẫn cần xác nhận bằng paper-forward."
                if is_v20
                else "• Hậu nghiệm cải thiện tổng thể nhưng chưa thắng mọi giai đoạn; đang kiểm chứng forward."
                if is_shadow
                else "• Nhãn xác nhận chéo dựa trên lịch sử; forward hiện chưa đủ mẫu."
            ),
            (
                f"• Bộ lọc: SMA200 dốc đúng hướng | Breadth <b>{signal.get('v20_regime_breadth', 0)}/4</b> ≥ <b>2/4</b>"
                if is_v20
                else "• MTF 1h/4h: <b>chỉ bối cảnh, không tạo lệnh</b>"
            ),
            "",
            "📍 <b>KẾ HOẠCH GIÁ</b>",
            f"• Giá hiện tại: <code>{price(signal['current_price'])}</code>",
            f"• Entry tham chiếu: <code>{price(plan['entry'])}</code>",
            f"• SL: <code>{price(plan['stop_loss'])}</code>",
            f"• TP1 (1R, chốt 20%): <code>{price(plan['take_profit_1'])}</code>",
            f"• TP2 (2R, chốt 30%): <code>{price(plan['take_profit_2'])}</code>",
            f"• TP3 (4R, chốt 50%): <code>{price(plan['take_profit_3'])}</code>",
            "",
            "🛡️ <b>QUẢN TRỊ</b>",
            f"• Rủi ro mô hình: <code>{risk_pct:.3f}% vốn</code>",
            f"• Quy mô danh nghĩa: <code>{100.0 * signal.get('notional_fraction_allocated', 0.0):.2f}% vốn</code>",
            f"• Rủi ro cả cụm nếu vào đủ: <code>{100.0 * float(signal.get('portfolio_risk_fraction', risk_pct / 100.0 * signal.get('portfolio_risk_count', signal.get('cross_market_count', 1)))):.3f}% vốn</code>",
            "• Các coin cùng cụm có tương quan; không cộng chúng như cơ hội độc lập.",
            "• Sau TP1: dời SL về hòa vốn | Sau TP2: dời SL lên TP1",
            f"• Thoát sớm: đóng ngày phá kênh Donchian {exit_channel} ngày ngược hướng",
            "• Giữ tối đa: <b>90 ngày</b> | Rà soát: <b>mỗi ngày</b>",
            "• Nguồn: <b>BINANCE USD-M FUTURES ĐÃ XÁC MINH</b>",
            f"• Cửa sổ Entry: <b>{signal.get('entry_window_seconds', 600) / 60.0:.0f} phút</b> | Không đuổi quá <b>{signal.get('max_chase_bps', 40.0):.0f} bps</b>",
            "",
            f"🕯️ Tín hiệu: {esc(display_time(signal['signal_time_utc']))}",
            f"🚪 Entry: {esc(display_time(signal['entry_time_utc']))}",
            f"⚡ Độ trễ: <code>{signal['entry_lag_seconds']:.0f}s</code> | Chase: <code>{signal['directional_chase_bps']:.1f} bps</code>",
            f"🆔 <code>{esc(signal['signal_id'])}</code>",
            "🔒 <b>PAPER ONLY / KHÔNG TỰ ĐẶT LỆNH</b>",
        ]
    )


def format_position_event(event: dict[str, Any]) -> str:
    labels = {
        "TP1": "ĐẠT TP1",
        "TP1_GAP": "ĐẠT TP1",
        "TP2": "ĐẠT TP2",
        "TP2_GAP": "ĐẠT TP2",
        "TP3": "ĐẠT TP3",
        "TP3_GAP": "ĐẠT TP3",
        "STOP": "CHẠM SL",
        "STOP_GAP": "THOÁT DO GAP QUA SL",
        "CHANNEL_EXIT": f"THOÁT THEO KÊNH {int(event.get('exit_channel', 20))} NGÀY",
        "MAX_HOLD": "HẾT 90 NGÀY",
    }
    reason = labels.get(event["reason"], event["reason"])
    bot_label = bot_display(event)
    return "\n".join(
        [
            f"📌 <b>{bot_label} — {esc(reason)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• {esc(event['symbol'])} | <b>{esc(event['side'])}</b>",
            f"• Tầng: <b>{esc(quality_display(event.get('quality_label')))}</b>",
            f"• Giá: <code>{price(event['price'])}</code>",
            f"• Vị thế còn lại: <code>{100.0 * event['remaining_fraction']:.1f}%</code>",
            f"• Thời gian: {esc(display_time(event['time_utc']))}",
            f"🆔 <code>{esc(event['signal_id'])}</code>",
            "🔒 <b>PAPER ONLY</b>",
        ]
    )


def format_trade_closed(trade: dict[str, Any]) -> str:
    result = "LÃI" if trade["net_pnl"] > 0 else "LỖ"
    icon = "✅" if trade["net_pnl"] > 0 else "⛔"
    bot_label = bot_display(trade)
    return "\n".join(
        [
            f"{icon} <b>{bot_label} — ĐÃ ĐÓNG {esc(trade['symbol'])} {esc(trade['side'])}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• Kết quả: <b>{result}</b> | <code>{trade['realized_r']:+.3f}R</code>",
            f"• Tầng: <b>{esc(quality_display(trade.get('quality_label')))}</b>",
            f"• Entry: <code>{price(trade['entry'])}</code> | Exit: <code>{price(trade['exit_price'])}</code>",
            f"• Lý do: <code>{esc(trade['exit_reason'])}</code>",
            f"• Thời gian giữ: <code>{trade['holding_hours'] / 24.0:.1f} ngày</code>",
            "• Phí + funding đã tính trong kết quả.",
            f"🆔 <code>{esc(trade['signal_id'])}</code>",
            "🔒 <b>PAPER ONLY / KHÔNG TỰ ĐẶT LỆNH</b>",
        ]
    )


def performance(
    state: dict[str, Any], quality_tier: str | None = None
) -> tuple[int, float, float, float]:
    trades = state.get("closed_trades", [])
    if quality_tier is not None:
        trades = [
            trade
            for trade in trades
            if trade.get("quality_tier", "STANDARD") == quality_tier
        ]
    if not trades:
        return 0, 0.0, 0.0, 0.0
    wins = sum(float(trade["net_pnl"]) > 0.0 for trade in trades)
    gross_profit = sum(max(float(trade["net_pnl"]), 0.0) for trade in trades)
    gross_loss = -sum(min(float(trade["net_pnl"]), 0.0) for trade in trades)
    pf = gross_profit / gross_loss if gross_loss > 0.0 else math.inf
    mean_r = sum(float(trade["realized_r"]) for trade in trades) / len(trades)
    return len(trades), wins / len(trades), pf, mean_r


def format_heartbeat(scan: dict[str, Any], state: dict[str, Any]) -> str:
    ready = sum(group.get("status") == "READY" for group in scan.get("groups", []))
    trusted, fallback = source_summary(scan)
    count, win_rate, pf, mean_r = performance(state)
    standard_count, standard_win, standard_pf, _ = performance(state, "STANDARD")
    confirmed_count, confirmed_win, confirmed_pf, _ = performance(
        state, "A_PLUS_CROSS_MARKET"
    )
    forward = forward_assessment(state)
    pf_label = f"{pf:.2f}" if math.isfinite(pf) else "∞"
    standard_pf_label = f"{standard_pf:.2f}" if math.isfinite(standard_pf) else "∞"
    confirmed_pf_label = f"{confirmed_pf:.2f}" if math.isfinite(confirmed_pf) else "∞"
    active = state.get("active_positions", [])
    shadow = state.get("shadow_v17", {})
    shadow_count, shadow_win, shadow_pf, shadow_mean = performance(shadow)
    shadow_pf_label = f"{shadow_pf:.2f}" if math.isfinite(shadow_pf) else "∞"
    shadow_active = shadow.get("active_positions", [])
    v20 = state.get("shadow_v20", {})
    v20_count, v20_win, v20_pf, v20_mean = performance(v20)
    v20_pf_label = f"{v20_pf:.2f}" if math.isfinite(v20_pf) else "∞"
    v20_active = v20.get("active_positions", [])
    v20_context = v20.get("last_regime_context", {})
    v21 = state.get("production_readiness_v21", {})
    v21_forward = v21.get("forward", {})
    v21_operations = v21.get("operations", {})
    v21_pf = float(v21_forward.get("profit_factor", 0.0))
    v21_pf_label = f"{v21_pf:.2f}" if math.isfinite(v21_pf) else "∞"
    standard_open = sum(
        position.get("quality_tier", "STANDARD") == "STANDARD"
        for position in active
    )
    confirmed_open = sum(
        position.get("quality_tier") == "A_PLUS_CROSS_MARKET"
        for position in active
    )
    forward_status = {
        "INSUFFICIENT_SAMPLE_CURRENTLY_WEAK": "CHƯA ĐỦ MẪU / ĐANG YẾU",
        "INSUFFICIENT_SAMPLE": "CHƯA ĐỦ MẪU",
        "PRELIMINARY_PASS": "SƠ BỘ ĐẠT",
        "BELOW_FORWARD_GATE": "DƯỚI CHUẨN FORWARD",
    }.get(forward["status"], forward["status"])
    cluster_pf = forward["profit_factor"]
    cluster_pf_label = f"{cluster_pf:.2f}" if math.isfinite(cluster_pf) else "∞"
    lines = [
            "💓💎 <b>CORE4 V7 PAPER-FORWARD — BÁO SỐNG</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"✅ Hệ thống: <b>{'ỔN' if scan.get('status') == 'OK' else 'SUY GIẢM'}</b> | Dữ liệu: <b>{ready}/4</b>",
            f"📂 Vị thế mở: <b>{len(state.get('active_positions', []))}</b> | Đã đóng: <b>{count}</b>",
            f"📈 Forward: Win <code>{win_rate:.1%}</code> | PF <code>{pf_label}</code> | Mean <code>{mean_r:+.3f}R</code>",
            f"💎 Xác nhận chéo: mở <b>{confirmed_open}</b> | đóng <b>{confirmed_count}</b> | Win <code>{confirmed_win:.1%}</code> | PF <code>{confirmed_pf_label}</code>",
            f"📘 Standard: mở <b>{standard_open}</b> | đóng <b>{standard_count}</b> | Win <code>{standard_win:.1%}</code> | PF <code>{standard_pf_label}</code>",
            f"💰 Equity paper: <code>{float(state.get('equity', 1.0)):.4f}</code>",
            f"🧪 Forward theo cụm: <b>{forward_status}</b> | n=<code>{forward['clusters']}</code> | Win <code>{forward['win_rate']:.1%}</code> | PF <code>{cluster_pf_label}</code>",
            f"🧪 V17 shadow: mở <b>{len(shadow_active)}</b> | đóng <b>{shadow_count}</b> | Win <code>{shadow_win:.1%}</code> | PF <code>{shadow_pf_label}</code> | Mean <code>{shadow_mean:+.3f}R</code>",
            f"🧭 V20 shadow: mở <b>{len(v20_active)}</b> | đóng <b>{v20_count}</b> | Win <code>{v20_win:.1%}</code> | PF <code>{v20_pf_label}</code> | Mean <code>{v20_mean:+.3f}R</code>",
            "🧭 V20 gate: <b>{status}</b> | hướng <b>{side}</b> | breadth <code>{breadth}/4</code> | FAST <b>{fast}</b>".format(
                status="ĐẠT" if v20_context.get("allowed") else "CHỜ",
                side=esc(v20_context.get("side") or "n/a"),
                breadth=int(v20_context.get("breadth", 0)),
                fast="BẬT" if v20_context.get("fast_active") else "TẮT",
            ),
            "📚 V20 hậu nghiệm: <code>{freq:.3f}/tuần</code> | <code>{trades} lệnh</code> | Win <code>{win:.1%}</code> | PF <code>{pf:.2f}</code> | Sharpe <code>{sharpe:.2f}</code> | DD <code>{dd:.2%}</code>".format(
                freq=V20_HISTORICAL_REFERENCE["trades_per_week"],
                trades=V20_HISTORICAL_REFERENCE["trades"],
                win=V20_HISTORICAL_REFERENCE["win_rate"],
                pf=V20_HISTORICAL_REFERENCE["profit_factor"],
                sharpe=V20_HISTORICAL_REFERENCE["sharpe"],
                dd=V20_HISTORICAL_REFERENCE["max_drawdown"],
            ),
            "🧯 V20 stress gộp: PF <code>{pf:.2f}</code> | DD <code>{dd:.2%}</code>".format(
                pf=V20_HISTORICAL_REFERENCE["stress_profit_factor"],
                dd=V20_HISTORICAL_REFERENCE["stress_max_drawdown"],
            ),
            "🛡️ V21 go-live: <b>KHÓA</b> | đạt <b>{passed}/{total}</b> cổng | <code>{days:.1f}/90 ngày</code> | <code>{clusters}/20 cụm</code>".format(
                passed=int(v21.get("passed_checks", 0)),
                total=int(v21.get("total_checks", 11)),
                days=float(v21_forward.get("observation_days", 0.0)),
                clusters=int(v21_forward.get("clusters", 0)),
            ),
            "📐 V21 forward: Win <code>{win:.1%}</code> | PF <code>{pf}</code> | Mean <code>{mean:+.3f}R</code> | DD <code>{dd:.2%}</code>".format(
                win=float(v21_forward.get("win_rate", 0.0)),
                pf=v21_pf_label,
                mean=float(v21_forward.get("mean_cluster_r", 0.0)),
                dd=float(v21_forward.get("maximum_drawdown", 0.0)),
            ),
            "⚙️ V21 vận hành: scan tốt <code>{healthy:.2%}</code> | Futures tin cậy <code>{trusted:.2%}</code> | data-gap <code>{gap:.3%}</code>".format(
                healthy=float(v21_operations.get("healthy_scan_rate", 0.0)),
                trusted=float(v21_operations.get("trusted_source_rate", 0.0)),
                gap=float(v21_operations.get("data_gap_scan_rate", 0.0)),
            ),
            "📚 V17 hậu nghiệm: <code>{freq:.3f}/tuần</code> | Win <code>{win:.1%}</code> | PF <code>{pf:.2f}</code> | Sharpe <code>{sharpe:.2f}</code> | DD <code>{dd:.2%}</code>".format(
                freq=V17_HISTORICAL_REFERENCE["trades_per_week"],
                win=V17_HISTORICAL_REFERENCE["win_rate"],
                pf=V17_HISTORICAL_REFERENCE["profit_factor"],
                sharpe=V17_HISTORICAL_REFERENCE["sharpe"],
                dd=V17_HISTORICAL_REFERENCE["max_drawdown"],
            ),
            "📚 OOS lịch sử: <code>{trades} lệnh</code> | Win <code>{win:.1%}</code> | PF <code>{pf:.2f}</code> | Sharpe <code>{sharpe:.2f}</code>".format(
                trades=HISTORICAL_REFERENCE["trades"],
                win=HISTORICAL_REFERENCE["win_rate"],
                pf=HISTORICAL_REFERENCE["profit_factor"],
                sharpe=HISTORICAL_REFERENCE["sharpe"],
            ),
    ]
    for position in active[:3]:
        lines.append(
            "📌 {symbol} <b>{side}</b> @ <code>{entry}</code> | SL <code>{stop}</code> | {status}".format(
                symbol=esc(position["symbol"]),
                side=esc(position["side"]),
                entry=price(position["entry"]),
                stop=price(position["current_stop"]),
                status=esc(position.get("status", "OPEN")),
            )
        )
    diagnostics = [
        group for group in scan.get("groups", []) if group.get("readiness_band")
    ]
    if diagnostics:
        regime = diagnostics[0].get("actionable_side", "n/a")
        lines.append(
            "🧭 Kịch bản 1D dự kiến: <b>{side}</b> | BTC <code>{close}</code> vs SMA200 <code>{sma}</code>".format(
                side=esc(regime),
                close=price(diagnostics[0]["projected_btc_close"]),
                sma=price(diagnostics[0]["projected_btc_sma200"]),
            )
        )
        for group in diagnostics:
            icon, label = readiness_display(group["readiness_band"])
            distance = float(group["actionable_distance_pct"])
            distance_label = (
                "đã xuyên intraday"
                if distance <= 0.0
                else f"còn {distance:.2f}%"
            )
            lines.append(
                "{icon} {symbol} <b>{label}</b> {side} | {distance} → <code>{trigger}</code>".format(
                    icon=icon,
                    symbol=esc(group["symbol"].replace("USDT", "")),
                    label=esc(label),
                    side=esc(group["actionable_side"]),
                    distance=esc(distance_label),
                    trigger=price(group["actionable_trigger"]),
                )
            )
    lines.extend(
        [
            f"🧾 Nguồn Futures xác minh: <b>{trusted}/4</b> | Spot fallback: <b>{fallback}/4</b>",
            "🎯 1D LONG + SHORT | BTC / ETH / SOL / BNB",
            "🧭 MTF 1h/4h: BỐI CẢNH | KHÔNG TỰ SINH LỆNH",
            "🔒 <b>PAPER ONLY / KHÔNG TỰ ĐẶT LỆNH</b>",
            f"🕒 {esc(display_time(scan['time_utc']))}",
        ]
    )
    if fallback:
        lines.insert(-3, "⚠️ <b>TRADE BỊ KHÓA KHI NGUỒN FUTURES CHƯA ĐẠT</b>")
    if scan.get("status") != "OK" and scan.get("errors"):
        lines.insert(-3, f"⚠️ Dữ liệu: <code>{esc(str(scan['errors'][0])[:140])}</code>")
    return "\n".join(lines)


def format_readiness_alert(event: dict[str, Any]) -> str:
    headline = (
        "ĐỔI CHẾ ĐỘ THỊ TRƯỜNG"
        if event.get("regime_changed")
        else "GIÁ ĐANG TIẾN GẦN NGƯỠNG V7"
    )
    lines = [
        f"🎯📡 <b>CORE4 V7 — {headline}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "👀 <b>CẢNH BÁO THEO DÕI / CHƯA PHẢI LỆNH TRADE</b>",
    ]
    for market in event.get("markets", []):
        icon, label = readiness_display(market.get("band"))
        distance = float(market.get("distance_pct", 0.0))
        distance_label = "đã xuyên intraday" if distance <= 0.0 else f"còn {distance:.2f}%"
        lines.append(
            "{icon} {symbol} <b>{label}</b> {side} | {distance} → <code>{trigger}</code>".format(
                icon=icon,
                symbol=esc(str(market.get("symbol", "")).replace("USDT", "")),
                label=esc(label),
                side=esc(market.get("side")),
                distance=esc(distance_label),
                trigger=price(market.get("trigger")),
            )
        )
    decision = next(
        (market.get("next_decision_utc") for market in event.get("markets", []) if market.get("next_decision_utc")),
        None,
    )
    if decision:
        lines.append(f"🕯️ Xác nhận chính thức khi nến 1D đóng: {esc(display_time(decision))}")
    lines.extend(
        [
            "⚠️ Không vào sớm chỉ vì cảnh báo này; V7 chỉ tạo lệnh sau đóng nến 1D.",
            f"🕒 {esc(display_time(event['time_utc']))}",
            "🔒 <b>PAPER ONLY / KHÔNG TỰ ĐẶT LỆNH</b>",
        ]
    )
    return "\n".join(lines)


class TelegramSender:
    def __init__(self, enabled: bool, timeout_seconds: float = 10.0) -> None:
        self.enabled = enabled
        self.token = os.getenv("CORE4_TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("CORE4_TELEGRAM_CHAT_ID", "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.token) and bool(self.chat_id)

    def send(self, text: str) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "skipped": True, "reason": "core4_telegram_not_configured"}
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "core4-v7-paper-forward/1.0"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
