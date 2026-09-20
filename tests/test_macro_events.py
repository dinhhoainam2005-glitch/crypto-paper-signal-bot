from __future__ import annotations

import unittest
from datetime import datetime, timezone

from paper_signal_bot.macro_events import (
    MACRO_ID,
    MacroScheduledEvent,
    add_gdp_nowcast,
    add_inflation_nowcast,
    alert_phase,
    enrich_bls_actuals,
    enrich_census_actuals,
    enrich_fed_actuals,
    evaluate_macro_calendar,
    market_impact_for_event,
    link_fomc_outcomes,
    parse_atlanta_gdpnow,
    parse_bea_release_actual,
    parse_bls_ics,
    parse_census_widget,
    parse_fed_target_results,
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
    def test_free_official_sources_enrich_forecast_and_actuals(self) -> None:
        cpi = next(event for event in parse_bls_ics(BLS_SAMPLE) if event.category == "CPI_INFLATION")
        cpi = add_inflation_nowcast(
            [cpi],
            {
                "monthly": [
                    {
                        "month": "August 2026",
                        "cpi_mom": "0.40",
                        "core_cpi_mom": "0.20",
                        "pce_mom": "0.30",
                        "core_pce_mom": "0.20",
                        "updated": "09/08",
                    }
                ]
            },
        )[0]
        payload = {
            "Results": {
                "series": [
                    {
                        "seriesID": "CUSR0000SA0",
                        "data": [
                            {"year": "2026", "period": "M08", "value": "301.2"},
                            {"year": "2026", "period": "M07", "value": "300.0"},
                        ],
                    },
                    {
                        "seriesID": "CUSR0000SA0L1E",
                        "data": [
                            {"year": "2026", "period": "M08", "value": "310.62"},
                            {"year": "2026", "period": "M07", "value": "310.0"},
                        ],
                    },
                ]
            }
        }

        enriched = enrich_bls_actuals([cpi], payload)[0]

        self.assertIn("CPI MoM thực tế +0.40%", enriched.actual_summary)
        self.assertEqual(enriched.actual_sources, ("BLS Public Data API",))
        self.assertIn("+0", enriched.surprise_summary)

        gdpnow = parse_atlanta_gdpnow(
            '{"ResearchData":[{"Indicator":"5.1%","UpdatedDate":"2026-09-17T11:00:00",'
            '"Name":"Third-Quarter GDPNow Estimate for 2026:Q3","IconAlias":"GDPNow"}]}'
        )
        gdp = MacroScheduledEvent(
            title="GDP (Advance Estimate), 3rd Quarter 2026",
            category="GDP_GROWTH",
            priority="HIGH",
            impact_score=78,
            event_time_utc="2026-10-29T12:30:00+00:00",
            source="BEA",
            source_url="https://example.test",
            rationale="Growth",
            reference="3rd Quarter 2026",
        )
        gdp = add_gdp_nowcast([gdp], gdpnow)[0]
        self.assertIn("5.1%", gdp.forecast_summary)
        self.assertEqual(gdp.forecast_sources, ("Atlanta Fed GDPNow",))

    def test_fed_census_and_bea_official_result_parsers(self) -> None:
        fed_html = """
        <a id="2026" name="2026"></a><h4>2026</h4><table>
        <tr><th>Date</th><th>Increase</th><th>Decrease</th><th>Level (%)</th></tr>
        <tr><td>September 17</td><td>25</td><td>0</td><td>3.75-4.00</td></tr>
        </table>
        """
        fed_results = parse_fed_target_results(fed_html)
        decision = MacroScheduledEvent(
            title="FOMC Rate Decision",
            category="FOMC_RATE_DECISION",
            priority="CRITICAL",
            impact_score=100,
            event_time_utc="2026-09-17T18:00:00+00:00",
            source="Federal Reserve",
            source_url="https://example.test",
            rationale="Fed",
        )
        decision = enrich_fed_actuals([decision], fed_results)[0]
        self.assertIn("3.75-4.00%", decision.actual_summary)
        self.assertIn("tăng 25 bps", decision.actual_summary)

        census_html = """
        <article class="row"><div class="info_row"><span class="tooltip bottom"
        aria-label="Advance Monthly Sales for Retail and Food Services">Retail</span>
        <span class="date">August 2026 Report</span></div><div class="value_row">
        <span class="change tooltip top">$773.9 B</span><span class="sub_change"></span>
        <div class="increase sig"></div><span class="">1.2%</span></div></article>
        """
        rows = parse_census_widget(census_html)
        retail = MacroScheduledEvent(
            title="Advance Monthly Sales for Retail and Food Services",
            category="RETAIL_SALES",
            priority="HIGH",
            impact_score=74,
            event_time_utc="2026-09-16T12:30:00+00:00",
            source="Census",
            source_url="https://example.test",
            rationale="Demand",
            reference="August 2026",
        )
        retail = enrich_census_actuals([retail], rows)[0]
        self.assertIn("1.2%", retail.actual_summary)
        self.assertEqual(retail.actual_sources, ("U.S. Census Economic Indicators",))

        bea = parse_bea_release_actual(
            "From the preceding month, the PCE price index for August increased 0.3 percent. "
            "Excluding food and energy, the PCE price index also increased 0.2 percent.",
            "PCE_INFLATION",
        )
        self.assertEqual(bea["primary_value"], 0.3)
        self.assertIn("Core PCE MoM +0.20%", bea["summary"])

    def test_fomc_press_conference_receives_linked_rate_decision_result(self) -> None:
        decision = MacroScheduledEvent(
            title="FOMC Rate Decision",
            category="FOMC_RATE_DECISION",
            priority="CRITICAL",
            impact_score=100,
            event_time_utc="2026-09-16T18:00:00+00:00",
            source="Trading Economics",
            source_url="https://example.test",
            rationale="Fed decision",
            actual_summary="Actual 3.75% | Consensus 4.00%",
            actual_sources=("Trading Economics",),
            surprise_summary="Chênh lệch actual - consensus: -0.25",
        )
        press = MacroScheduledEvent(
            title="FOMC Press Conference",
            category="FOMC_PRESS_CONFERENCE",
            priority="CRITICAL",
            impact_score=96,
            event_time_utc="2026-09-16T18:30:00+00:00",
            source="Federal Reserve",
            source_url="https://example.test",
            rationale="Fed guidance",
        )

        linked = link_fomc_outcomes([decision, press])

        self.assertIn("Rate decision context", linked[1].actual_summary)
        self.assertEqual(linked[1].actual_sources, ("Trading Economics",))

    def test_past_event_is_result_pending_instead_of_disappearing(self) -> None:
        event_time = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)
        phase, minutes = alert_phase(event_time, datetime(2026, 9, 10, 13, 0, tzinfo=timezone.utc))

        self.assertEqual(phase, "RESULT_PENDING")
        self.assertEqual(minutes, -30.0)

    def test_result_event_contains_actual_and_observed_crypto_reaction(self) -> None:
        event_time = "2026-09-10T12:30:00+00:00"
        event_ms = ms(event_time)
        rows_by_key = {}
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
            rows_by_key[(symbol, "1h")] = [
                [event_ms - 3_600_000, 100, 100, 100, 100, 100, event_ms - 1_800_000],
                [event_ms + 1_800_000, 100, 101, 100, 101, 100, event_ms + 5_400_000],
            ]
        payload = {
            "calendar": [
                {
                    "title": "Consumer Price Index",
                    "category": "CPI_INFLATION",
                    "priority": "CRITICAL",
                    "impact_score": 95,
                    "event_time_utc": event_time,
                    "source": "Trading Economics",
                    "source_url": "https://example.test",
                    "rationale": "Inflation risk",
                    "reference": "August 2026",
                    "forecast_summary": "Consensus 0.30%",
                    "forecast_sources": ["Trading Economics"],
                    "forecast_confidence": "CONSENSUS",
                    "actual_summary": "Actual 0.40% | Consensus 0.30% | Previous 0.20%",
                    "actual_sources": ["Trading Economics"],
                    "surprise_summary": "Chênh lệch actual - consensus: +0.1",
                }
            ],
            "source_states": [{"source": "Trading Economics", "status": "OK"}],
        }
        result = evaluate_macro_calendar(payload, ms("2026-09-10T13:35:00+00:00"), rows_by_key)

        alert = result["events"][0]
        self.assertEqual(alert["phase"], "RESULT")
        self.assertEqual(alert["result_status"], "AVAILABLE")
        self.assertEqual(alert["market_impact"]["direction"], "TĂNG")
        self.assertEqual(alert["market_impact"]["up_count"], 4)
        text = format_macro_event_message(alert)
        self.assertIn("KẾT QUẢ THỰC TẾ", text)
        self.assertIn("PHẢN ỨNG CRYPTO SAU SỰ KIỆN", text)
        self.assertIn("Thực tế 0.40%", text)
        digest = format_macro_digest_message([alert])
        self.assertIn("Dự báo:", digest)
        self.assertIn("Kết quả:", digest)
        self.assertIn("Crypto 1h:", digest)

    def test_post_event_reaction_generates_a_distinct_delivery_update(self) -> None:
        event_time = "2026-09-10T12:30:00+00:00"
        event_ms = ms(event_time)
        payload = {
            "calendar": [
                {
                    "title": "Consumer Price Index",
                    "category": "CPI_INFLATION",
                    "priority": "CRITICAL",
                    "impact_score": 95,
                    "event_time_utc": event_time,
                    "source": "BLS",
                    "source_url": "https://example.test",
                    "rationale": "Inflation risk",
                }
            ],
            "source_states": [{"source": "BLS", "status": "OK"}],
        }
        first = evaluate_macro_calendar(payload, ms("2026-09-10T12:35:00+00:00"))["events"][0]
        rows = {
            (symbol, "1h"): [
                [event_ms - 3_600_000, 100, 100, 100, 100, 100, event_ms - 1_800_000],
                [event_ms + 1_800_000, 100, 101, 100, 101, 100, event_ms + 5_400_000],
            ]
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
        }
        reaction = evaluate_macro_calendar(payload, ms("2026-09-10T13:35:00+00:00"), rows)["events"][0]

        self.assertNotEqual(first["event_id"], reaction["event_id"])
        self.assertEqual(reaction["delivery_phase"], "RESULT_PENDING_REACTION_1H")
        self.assertEqual(reaction["reaction_status"], "AVAILABLE")

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
