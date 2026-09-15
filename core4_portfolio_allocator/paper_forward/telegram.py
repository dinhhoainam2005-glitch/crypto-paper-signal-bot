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


VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")


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


def format_startup(scan: dict[str, Any]) -> str:
    ready = sum(group.get("status") == "READY" for group in scan.get("groups", []))
    trusted, fallback = source_summary(scan)
    lines = [
            "💎📡 <b>CORE4 V7 PAPER-FORWARD ĐÃ KHỞI ĐỘNG</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"✅ Hệ thống: <b>{'ỔN' if scan.get('status') == 'OK' else 'SUY GIẢM'}</b> | Dữ liệu: <b>{ready}/4</b>",
            "📊 Thị trường: <b>BTC / ETH / SOL / BNB</b> | Khung: <b>1D</b>",
            "↕️ Hướng: <b>LONG + SHORT</b> | Quét: <b>mỗi 1 phút</b>",
            "🎯 Kế hoạch: <b>Entry + SL + TP1/TP2/TP3 + giữ tối đa 90 ngày</b>",
            "💎 Phân tầng: <b>V7 STANDARD</b> | <b>A+ khi ≥2 coin cùng kỳ xác nhận</b>",
            "🧭 MTF 1h/4h: <b>CHỈ BỐI CẢNH / KHÔNG TỰ SINH LỆNH</b>",
            f"🧾 Nguồn Binance Futures xác minh: <b>{trusted}/4</b>",
            "🧪 Trạng thái: <b>PAPER-FORWARD / KHÔNG ĐẶT LỆNH</b>",
            "🔐 Cấu hình V7 đã khóa SHA-256; bot R26A cũ hoạt động độc lập.",
            f"🕒 {esc(display_time(scan['time_utc']))}",
    ]
    if fallback:
        lines.insert(
            -3,
            f"⚠️ Spot fallback: <b>{fallback}/4</b> | Tín hiệu Trade bị khóa để bảo toàn chất lượng",
        )
    return "\n".join(lines)


def format_signal(signal: dict[str, Any]) -> str:
    plan = signal["plan"]
    is_long = plan["side"] == "LONG"
    header = "🟢🔺" if is_long else "🔴🔻"
    action = "LONG (MUA)" if is_long else "SHORT (BÁN)"
    quality_tier = signal.get("quality_tier", "STANDARD")
    is_cross_market = quality_tier == "A_PLUS_CROSS_MARKET"
    quality_label = "A+ CROSS-MARKET" if is_cross_market else "V7 STANDARD"
    quality_icon = "💎" if is_cross_market else "📘"
    confirmations = ", ".join(
        symbol.replace("USDT", "")
        for symbol in signal.get("cross_market_symbols", [plan["symbol"]])
    )
    return "\n".join(
        [
            f"{header} <b>CORE4 V7 PAPER {action} — {esc(plan['symbol'])}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "✅ <b>TÍN HIỆU PAPER-FORWARD HỢP LỆ</b>",
            f"⏱️ Khung: <b>1D</b> | Hướng: <b>{action}</b>",
            f"{quality_icon} Chất lượng: <b>{quality_label}</b>",
            f"• Xác nhận chéo: <b>{signal.get('cross_market_count', 1)}/4</b> | {esc(confirmations)}",
            "• MTF 1h/4h: <b>chỉ bối cảnh, không tạo lệnh</b>",
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
            "• Rủi ro mô hình: <code>0.35% vốn</code>",
            f"• Quy mô danh nghĩa: <code>{100.0 * signal.get('notional_fraction_allocated', 0.0):.2f}% vốn</code>",
            "• Sau TP1: dời SL về hòa vốn | Sau TP2: dời SL lên TP1",
            "• Thoát sớm: đóng ngày phá kênh Donchian 20 ngày ngược hướng",
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
        "CHANNEL_EXIT": "THOÁT THEO KÊNH 20 NGÀY",
        "MAX_HOLD": "HẾT 90 NGÀY",
    }
    reason = labels.get(event["reason"], event["reason"])
    return "\n".join(
        [
            f"📌 <b>CORE4 V7 — {esc(reason)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• {esc(event['symbol'])} | <b>{esc(event['side'])}</b>",
            f"• Tầng: <b>{esc(event.get('quality_label', 'V7 STANDARD'))}</b>",
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
    return "\n".join(
        [
            f"{icon} <b>CORE4 V7 — ĐÃ ĐÓNG {esc(trade['symbol'])} {esc(trade['side'])}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• Kết quả: <b>{result}</b> | <code>{trade['realized_r']:+.3f}R</code>",
            f"• Tầng: <b>{esc(trade.get('quality_label', 'V7 STANDARD'))}</b>",
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
    aplus_count, aplus_win, aplus_pf, _ = performance(
        state, "A_PLUS_CROSS_MARKET"
    )
    pf_label = f"{pf:.2f}" if math.isfinite(pf) else "∞"
    standard_pf_label = f"{standard_pf:.2f}" if math.isfinite(standard_pf) else "∞"
    aplus_pf_label = f"{aplus_pf:.2f}" if math.isfinite(aplus_pf) else "∞"
    active = state.get("active_positions", [])
    standard_open = sum(
        position.get("quality_tier", "STANDARD") == "STANDARD"
        for position in active
    )
    aplus_open = sum(
        position.get("quality_tier") == "A_PLUS_CROSS_MARKET"
        for position in active
    )
    lines = [
            "💓💎 <b>CORE4 V7 PAPER-FORWARD — BÁO SỐNG</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"✅ Hệ thống: <b>{'ỔN' if scan.get('status') == 'OK' else 'SUY GIẢM'}</b> | Dữ liệu: <b>{ready}/4</b>",
            f"📂 Vị thế mở: <b>{len(state.get('active_positions', []))}</b> | Đã đóng: <b>{count}</b>",
            f"📈 Forward: Win <code>{win_rate:.1%}</code> | PF <code>{pf_label}</code> | Mean <code>{mean_r:+.3f}R</code>",
            f"💎 A+ chéo: mở <b>{aplus_open}</b> | đóng <b>{aplus_count}</b> | Win <code>{aplus_win:.1%}</code> | PF <code>{aplus_pf_label}</code>",
            f"📘 Standard: mở <b>{standard_open}</b> | đóng <b>{standard_count}</b> | Win <code>{standard_win:.1%}</code> | PF <code>{standard_pf_label}</code>",
            f"💰 Equity paper: <code>{float(state.get('equity', 1.0)):.4f}</code>",
            f"🧾 Nguồn Futures xác minh: <b>{trusted}/4</b> | Spot fallback: <b>{fallback}/4</b>",
            "🎯 1D LONG + SHORT | BTC / ETH / SOL / BNB",
            "🧭 MTF 1h/4h: BỐI CẢNH | KHÔNG TỰ SINH LỆNH",
            "🔒 <b>PAPER ONLY / KHÔNG TỰ ĐẶT LỆNH</b>",
            f"🕒 {esc(display_time(scan['time_utc']))}",
    ]
    if fallback:
        lines.insert(-3, "⚠️ <b>TRADE BỊ KHÓA KHI NGUỒN FUTURES CHƯA ĐẠT</b>")
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
