from __future__ import annotations

import unittest
from datetime import datetime, timezone

from paper_signal_bot.macro_events import (
    MACRO_ID,
    add_inflation_nowcast,
    evaluate_macro_calendar,
    parse_bls_ics,
    parse_fomc_schedule,
)
from paper_signal_bot.telegram import format_macro_digest_message, format_macro_event_message


def ms(iso_value: str) -> int:
    return int(datetime.fromisoformat(iso_value.replace("Z", "+00:00")).timestamp() * 1000)


BLS_SAMPLE = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20260910T083000
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20260904T083000
SUMMARY:Employment Situation
END:VEVENT
END:VCALENDAR
"""


FOMC_SAMPLE = """
<div class="panel panel-default"><div class="panel-heading"><h4><a>2026 FOMC Meetings</a></h4></div>
<div class="row fomc-meeting">
<div class="fomc-meeting__month col-xs-5"><strong>September</strong></div>
<div class="fomc-meeting__date col-xs-4">15-16*</div>
</div>
</div>
"""


class MacroEventTests(unittest.TestCase):
    def test_bls_cpi_event_enriches_with_inflation_nowcast_and_alerts_t24h(self) -> None:
        events = parse_bls_ics(BLS_SAMPLE)
        cpi_events = [event for event in events if event.category == "CPI_INFLATION"]
        enriched = add_inflation_nowcast(
            cpi_events,
            {
                "monthly": [
                    {
                        "month": "September 2026",
                        "cpi_mom": "0.40",
                        "core_cpi_mom": "0.19",
                        "pce_mom": "0.38",
                        "core_pce_mom": "0.28",
                        "updated": "09/08",
                    },
                    {
                        "month": "August 2026",
                        "cpi_mom": "0.36",
                        "core_cpi_mom": "0.20",
                        "pce_mom": "0.35",
                        "core_pce_mom": "0.27",
                        "updated": "09/08",
                    }
                ]
            },
        )

        result = evaluate_macro_calendar(
            {"calendar": [event.__dict__ for event in enriched], "source_states": [{"source": "BLS", "status": "OK"}]},
            ms("2026-09-09T12:30:00+00:00"),
        )

        self.assertEqual(result["engine_id"], MACRO_ID)
        self.assertEqual(len(result["events"]), 1)
        alert = result["events"][0]
        self.assertEqual(alert["phase"], "T-24H")
        self.assertEqual(alert["priority"], "CRITICAL")
        self.assertEqual(alert["reference"], "August 2026")
        self.assertIn("Core CPI MoM 0.20%", alert["forecast_summary"])
        self.assertTrue(alert["watch_only"])
        self.assertFalse(alert["auto_trade"])

    def test_fomc_parser_adds_decision_and_press_conference(self) -> None:
        now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        events = parse_fomc_schedule(FOMC_SAMPLE, now)

        titles = {event.title for event in events}
        self.assertIn("FOMC Rate Decision + SEP/Dot Plot", titles)
        self.assertIn("FOMC Press Conference", titles)
        decision = next(event for event in events if event.title.startswith("FOMC Rate"))
        self.assertEqual(decision.event_time_utc, "2026-09-16T18:00:00+00:00")
        self.assertEqual(decision.priority, "CRITICAL")

    def test_far_future_event_does_not_alert_before_t7d_window(self) -> None:
        events = [event for event in parse_bls_ics(BLS_SAMPLE) if event.category == "CPI_INFLATION"]
        result = evaluate_macro_calendar(
            {"calendar": [event.__dict__ for event in events], "source_states": [{"source": "BLS", "status": "OK"}]},
            ms("2026-09-01T12:30:00+00:00"),
        )

        self.assertEqual(result["events"], [])

    def test_macro_format_is_not_trade_entry(self) -> None:
        events = parse_bls_ics(BLS_SAMPLE)
        alert = evaluate_macro_calendar(
            {"calendar": [event.__dict__ for event in events], "source_states": [{"source": "BLS", "status": "OK"}]},
            ms("2026-09-10T12:20:00+00:00"),
        )["events"][0]

        text = format_macro_event_message(alert)
        self.assertIn("R28A THEO DÕI RỦI RO VĨ MÔ", text)
        self.assertIn("CHỈ THEO DÕI", text)
        self.assertIn("DỰ BÁO / ĐỒNG THUẬN", text)
        self.assertIn("KỊCH BẢN CRYPTO", text)
        self.assertNotIn("Entry:", text)
        self.assertNotIn("TP1", text)

    def test_macro_digest_is_compact_and_keeps_every_event(self) -> None:
        events = evaluate_macro_calendar(
            {
                "calendar": [event.__dict__ for event in parse_bls_ics(BLS_SAMPLE)],
                "source_states": [{"source": "BLS", "status": "OK"}],
            },
            ms("2026-09-03T12:30:00+00:00"),
        )["events"]

        text = format_macro_digest_message(events)

        self.assertIn("CẢNH BÁO VĨ MÔ", text)
        self.assertIn("Chỉ số giá tiêu dùng CPI", text)
        self.assertIn("Báo cáo việc làm / NFP", text)
        self.assertIn("KHÔNG PHẢI LỆNH TRADE", text)
        self.assertLess(len(text), 1200)


if __name__ == "__main__":
    unittest.main()
