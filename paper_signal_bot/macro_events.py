from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo


MACRO_ID = "R28A_MACRO_EVENT_WATCH_V1"
EASTERN = ZoneInfo("America/New_York")
LOOKAHEAD_DAYS = int(os.getenv("MACRO_LOOKAHEAD_DAYS", "45"))
LOOKBACK_MINUTES = int(os.getenv("MACRO_LOOKBACK_MINUTES", "90"))
ALERT_RETRY_MINUTES = int(os.getenv("MACRO_ALERT_RETRY_MINUTES", "90"))
TRADING_ECONOMICS_CACHE_SECONDS = int(os.getenv("TRADING_ECONOMICS_CACHE_SECONDS", "86400"))
TRADING_ECONOMICS_EVENT_REFRESH_SECONDS = int(os.getenv("TRADING_ECONOMICS_EVENT_REFRESH_SECONDS", "3600"))
ALERT_PHASES = (
    (7 * 24 * 60, "T-7D"),
    (24 * 60, "T-24H"),
    (6 * 60, "T-6H"),
    (60, "T-1H"),
    (15, "T-15M"),
)


_trading_economics_cache: tuple[datetime, list[MacroScheduledEvent], dict[str, Any]] | None = None


SOURCE_URLS = {
    "fed_fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "bls_ics": "https://www.bls.gov/schedule/news_release/bls.ics",
    "bea_schedule": "https://www.bea.gov/news/schedule",
    "census_schedule": "https://www.census.gov/economic-indicators/calendar-listview.html",
    "cleveland_nowcast": "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting",
    "trading_economics": "https://api.tradingeconomics.com/calendar",
}


PROFILE_RULES = (
    ("FOMC_RATE_DECISION", ("fomc", "federal funds", "interest rate decision", "rate decision"), "CRITICAL", 100, "Fed rate/SEP can reprice USD liquidity, yields and crypto beta."),
    ("FOMC_PRESS_CONFERENCE", ("press conference", "fomc"), "CRITICAL", 96, "Powell guidance can reverse the first move after the statement."),
    ("CPI_INFLATION", ("consumer price index", "cpi"), "CRITICAL", 95, "Inflation surprise can move yields, DXY and crypto risk appetite."),
    ("NFP_LABOR", ("employment situation", "nonfarm", "payroll"), "CRITICAL", 92, "Labor surprise can shift Fed expectations and liquidity conditions."),
    ("PCE_INFLATION", ("personal income and outlays", "pce"), "HIGH", 88, "Core PCE is the Fed's preferred inflation gauge."),
    ("PPI_INFLATION", ("producer price index", "ppi"), "HIGH", 82, "PPI can foreshadow pipeline inflation and PCE pressure."),
    ("GDP_GROWTH", ("gdp", "gross domestic product"), "HIGH", 78, "Growth surprise can change soft-landing versus recession pricing."),
    ("RETAIL_SALES", ("retail", "advance monthly sales"), "HIGH", 74, "Consumer-demand data can move yields and risk assets."),
    ("JOLTS_LABOR", ("job openings", "jolts"), "MEDIUM", 68, "Labor-demand data can shift Fed path expectations."),
    ("DURABLE_GOODS", ("durable goods", "manufacturers' shipments"), "MEDIUM", 62, "Capex-sensitive data can affect growth expectations."),
    ("HOUSING", ("housing starts", "building permits", "new residential"), "MEDIUM", 56, "Housing activity is a secondary growth and rates signal."),
    ("TRADE_BALANCE", ("international trade", "trade in goods"), "MEDIUM", 52, "Trade data can affect GDP tracking and USD macro context."),
)


@dataclass(frozen=True)
class MacroScheduledEvent:
    title: str
    category: str
    priority: str
    impact_score: int
    event_time_utc: str
    source: str
    source_url: str
    rationale: str
    reference: str | None = None
    forecast_summary: str | None = None
    forecast_sources: tuple[str, ...] = ()
    forecast_confidence: str = "SCHEDULE_ONLY"
    actual_summary: str | None = None
    actual_sources: tuple[str, ...] = ()
    surprise_summary: str | None = None


class TableCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict[str, Any]] = []
        self._in_table = False
        self._in_caption = False
        self._in_row = False
        self._in_cell = False
        self._caption_parts: list[str] = []
        self._cell_parts: list[str] = []
        self._row: list[str] = []
        self._rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._caption_parts = []
            self._rows = []
        elif self._in_table and tag == "caption":
            self._in_caption = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "caption":
            self._in_caption = False
        elif tag in {"td", "th"} and self._in_cell:
            self._row.append(clean_text(" ".join(self._cell_parts)))
            self._cell_parts = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(cell for cell in self._row):
                self._rows.append(self._row)
            self._row = []
            self._in_row = False
        elif tag == "table" and self._in_table:
            self.tables.append({"caption": clean_text(" ".join(self._caption_parts)), "rows": self._rows})
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_caption:
            self._caption_parts.append(data)
        if self._in_cell:
            self._cell_parts.append(data)


def clean_text(value: Any) -> str:
    text = html.unescape("" if value is None else str(value))
    return " ".join(text.replace("\xa0", " ").split())


def numeric_value(value: Any) -> float | None:
    """Extract a simple numeric value for a neutral actual-vs-consensus comparison."""
    text = clean_text(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def surprise_summary(actual: Any, forecast: Any) -> str | None:
    actual_number = numeric_value(actual)
    forecast_number = numeric_value(forecast)
    if actual_number is None or forecast_number is None:
        return None
    delta = actual_number - forecast_number
    return f"Chênh lệch actual - consensus: {delta:+.4g}"


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_text(url: str, timeout: float = 12.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crypto-paper-signal-bot/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def collect_tables(markup: str) -> list[dict[str, Any]]:
    parser = TableCollector()
    parser.feed(markup)
    return parser.tables


MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def parse_us_datetime(date_text: str, time_text: str, default_year: int) -> datetime | None:
    date_clean = clean_text(date_text).replace(".", "")
    time_clean = clean_text(time_text).upper().replace(".", "")
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", date_clean)
    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", time_clean)
    if not match or not time_match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if not month:
        return None
    year = int(match.group(3) or default_year)
    day = int(match.group(2))
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or "0")
    if time_match.group(3) == "PM" and hour != 12:
        hour += 12
    if time_match.group(3) == "AM" and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, minute, tzinfo=EASTERN).astimezone(timezone.utc)


def profile_for_title(title: str) -> dict[str, Any] | None:
    lower = title.lower()
    for category, keys, priority, score, rationale in PROFILE_RULES:
        if category == "FOMC_PRESS_CONFERENCE":
            matched = "fomc" in lower and "press conference" in lower
        else:
            matched = any(key in lower for key in keys)
        if matched:
            return {
                "category": category,
                "priority": priority,
                "impact_score": score,
                "rationale": rationale,
            }
    return None


def make_event(
    *,
    title: str,
    when_utc: datetime,
    source: str,
    source_url: str,
    reference: str | None = None,
    profile_override: dict[str, Any] | None = None,
) -> MacroScheduledEvent | None:
    profile = profile_override or profile_for_title(title)
    if profile is None:
        return None
    return MacroScheduledEvent(
        title=clean_text(title),
        category=profile["category"],
        priority=profile["priority"],
        impact_score=int(profile["impact_score"]),
        event_time_utc=when_utc.astimezone(timezone.utc).isoformat(),
        source=source,
        source_url=source_url,
        rationale=profile["rationale"],
        reference=clean_text(reference) or None,
    )


def previous_month_label(value: datetime) -> str:
    year = value.year
    month = value.month - 1
    if month == 0:
        month = 12
        year -= 1
    month_name = value.replace(year=year, month=month, day=1).strftime("%B")
    return f"{month_name} {year}"


def parse_ics_datetime(line: str) -> datetime | None:
    _, _, value = line.partition(":")
    if not value:
        return None
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if "TZID=US-Eastern" in line or "TZID=America/New_York" in line:
            return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=EASTERN).astimezone(timezone.utc)
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=EASTERN).astimezone(timezone.utc)
    except ValueError:
        return None


def unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw.strip())
    return lines


def parse_bls_ics(text: str) -> list[MacroScheduledEvent]:
    events: list[MacroScheduledEvent] = []
    current: dict[str, str] | None = None
    for line in unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current:
                title = clean_text(current.get("SUMMARY", ""))
                when = parse_ics_datetime(current.get("DTSTART", ""))
                if title and when:
                    event = make_event(title=title, when_utc=when, source="BLS", source_url=SOURCE_URLS["bls_ics"])
                    if event:
                        if event.category in {"CPI_INFLATION", "PPI_INFLATION", "NFP_LABOR", "JOLTS_LABOR"} and not event.reference:
                            event = MacroScheduledEvent(**{**event.__dict__, "reference": previous_month_label(when)})
                        events.append(event)
            current = None
        elif current is not None:
            name, _, value = line.partition(":")
            key = name.split(";", 1)[0]
            if key in {"DTSTART", "SUMMARY"}:
                current[key] = line if key == "DTSTART" else value
    return events


def parse_bea_schedule(text: str, default_year: int) -> list[MacroScheduledEvent]:
    events: list[MacroScheduledEvent] = []
    year_match = re.search(r"Year\s+(20\d{2})", text)
    year = int(year_match.group(1)) if year_match else default_year
    for table in collect_tables(text):
        for row in table["rows"]:
            if len(row) < 3:
                continue
            date_time = row[0]
            title = row[-1]
            match = re.search(r"([A-Za-z]+\s+\d{1,2})\s+(\d{1,2}:\d{2}\s*[AP]M)", date_time, flags=re.I)
            if not match:
                continue
            when = parse_us_datetime(match.group(1), match.group(2), year)
            if when is None:
                continue
            event = make_event(
                title=title,
                when_utc=when,
                source="BEA",
                source_url=SOURCE_URLS["bea_schedule"],
                reference=extract_reference(title),
            )
            if event:
                events.append(event)
    return events


def parse_census_schedule(text: str, default_year: int) -> list[MacroScheduledEvent]:
    events: list[MacroScheduledEvent] = []
    for table in collect_tables(text):
        for row in table["rows"]:
            if len(row) < 4:
                continue
            title, date_text, time_text, reference = row[0], row[1], row[2], row[3]
            when = parse_us_datetime(date_text, time_text, default_year)
            if when is None:
                continue
            event = make_event(
                title=title,
                when_utc=when,
                source="Census",
                source_url=SOURCE_URLS["census_schedule"],
                reference=reference,
            )
            if event:
                events.append(event)
    return events


def parse_fomc_schedule(text: str, now: datetime) -> list[MacroScheduledEvent]:
    events: list[MacroScheduledEvent] = []
    headers = list(re.finditer(r">(20\d{2})\s+FOMC Meetings<", text))
    for index, header in enumerate(headers):
        year = int(header.group(1))
        if year < now.year - 1 or year > now.year + 1:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        segment = text[header.end():end]
        row_pattern = re.compile(
            r'fomc-meeting.*?fomc-meeting__month[^>]*>.*?<strong>([^<]+)</strong>.*?'
            r'fomc-meeting__date[^>]*>([^<]+)</div>',
            flags=re.S,
        )
        for row in row_pattern.finditer(segment):
            month_text = clean_text(row.group(1))
            date_text = clean_text(row.group(2)).replace("*", "")
            month = MONTHS.get(month_text.lower())
            if not month:
                continue
            day_match = re.search(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?", date_text)
            if not day_match:
                continue
            decision_day = int(day_match.group(2) or day_match.group(1))
            sep = "*" in row.group(2)
            decision = datetime(year, month, decision_day, 14, 0, tzinfo=EASTERN).astimezone(timezone.utc)
            profile = {
                "category": "FOMC_RATE_DECISION",
                "priority": "CRITICAL",
                "impact_score": 100 if sep else 96,
                "rationale": "Fed rate decision can reprice USD liquidity, yields and crypto beta. SEP/dot plot raises event risk." if sep else "Fed rate decision can reprice USD liquidity, yields and crypto beta.",
            }
            title = "FOMC Rate Decision" + (" + SEP/Dot Plot" if sep else "")
            events.append(
                make_event(
                    title=title,
                    when_utc=decision,
                    source="Federal Reserve",
                    source_url=SOURCE_URLS["fed_fomc"],
                    reference=f"{month_text} {date_text}, {year}",
                    profile_override=profile,
                )
            )
            press_profile = {
                "category": "FOMC_PRESS_CONFERENCE",
                "priority": "CRITICAL",
                "impact_score": 96 if sep else 92,
                "rationale": "Fed chair guidance can reverse the first move after the statement.",
            }
            events.append(
                make_event(
                    title="FOMC Press Conference",
                    when_utc=decision + timedelta(minutes=30),
                    source="Federal Reserve",
                    source_url=SOURCE_URLS["fed_fomc"],
                    reference=f"{month_text} {date_text}, {year}",
                    profile_override=press_profile,
                )
            )
    return [event for event in events if event is not None]


def parse_cleveland_nowcast(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"source": "Cleveland Fed Inflation Nowcasting", "monthly": []}
    for table in collect_tables(text):
        if table["caption"].lower() != "inflation, month-over-month percent change":
            continue
        for row in table["rows"]:
            if len(row) < 6 or row[0].lower() == "month":
                continue
            result["monthly"].append(
                {
                    "month": row[0],
                    "cpi_mom": row[1],
                    "core_cpi_mom": row[2],
                    "pce_mom": row[3],
                    "core_pce_mom": row[4],
                    "updated": row[5],
                }
            )
    return result


def fetch_trading_economics(now: datetime, lookahead_days: int) -> tuple[list[MacroScheduledEvent], dict[str, Any]]:
    key = os.getenv("TRADING_ECONOMICS_API_KEY", "").strip()
    if not key:
        return [], {"source": "Trading Economics", "status": "DISABLED", "events": 0, "url": SOURCE_URLS["trading_economics"]}
    start = (now - timedelta(minutes=LOOKBACK_MINUTES)).date().isoformat()
    end = (now + timedelta(days=lookahead_days)).date().isoformat()
    encoded_country = urllib.parse.quote("united states")
    url = f"https://api.tradingeconomics.com/calendar/country/{encoded_country}/{start}/{end}?c={urllib.parse.quote(key)}&importance=3&values=true&f=json"
    text = fetch_text(url, timeout=15.0)
    rows = json.loads(text)
    events: list[MacroScheduledEvent] = []
    for row in rows if isinstance(rows, list) else []:
        title = clean_text(row.get("Event") or row.get("Category") or "")
        date_text = row.get("Date") or ""
        try:
            when = datetime.fromisoformat(str(date_text).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        event = make_event(
            title=title,
            when_utc=when,
            source="Trading Economics",
            source_url=clean_text(row.get("SourceURL") or SOURCE_URLS["trading_economics"]),
            reference=clean_text(row.get("Reference") or ""),
        )
        if event is None:
            continue
        forecast_bits = []
        if row.get("Forecast"):
            forecast_bits.append(f"Consensus {row.get('Forecast')}")
        if row.get("TEForecast"):
            forecast_bits.append(f"TE forecast {row.get('TEForecast')}")
        if row.get("Previous"):
            forecast_bits.append(f"Previous {row.get('Previous')}")
        actual = row.get("Actual")
        actual_bits = []
        if actual not in (None, ""):
            actual_bits.append(f"Actual {actual}")
        if row.get("Forecast") not in (None, ""):
            actual_bits.append(f"Consensus {row.get('Forecast')}")
        if row.get("Previous") not in (None, ""):
            actual_bits.append(f"Previous {row.get('Previous')}")
        events.append(
            MacroScheduledEvent(
                **{
                    **event.__dict__,
                    "forecast_summary": " | ".join(forecast_bits) if forecast_bits else None,
                    "forecast_sources": ("Trading Economics",) if forecast_bits else (),
                    "forecast_confidence": "CONSENSUS" if row.get("Forecast") else "MODEL_OR_PREVIOUS",
                    "actual_summary": " | ".join(actual_bits) if actual_bits else None,
                    "actual_sources": ("Trading Economics",) if actual_bits else (),
                    "surprise_summary": surprise_summary(actual, row.get("Forecast")),
                }
            )
        )
    return events, {"source": "Trading Economics", "status": "OK", "events": len(events), "url": SOURCE_URLS["trading_economics"]}


def trading_economics_refresh_due(now: datetime, scheduled_events: list[MacroScheduledEvent]) -> bool:
    if _trading_economics_cache is None:
        return True
    cached_at, _, _ = _trading_economics_cache
    event_window = any(
        -LOOKBACK_MINUTES <= (parse_iso(event.event_time_utc) - now).total_seconds() / 60.0 <= 120
        for event in scheduled_events
    )
    ttl = TRADING_ECONOMICS_EVENT_REFRESH_SECONDS if event_window else TRADING_ECONOMICS_CACHE_SECONDS
    return (now - cached_at).total_seconds() >= ttl


def extract_reference(title: str) -> str | None:
    match = re.search(r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}|[1-4](?:st|nd|rd|th)\s+Quarter\s+20\d{2})", title)
    return clean_text(match.group(1)) if match else None


def add_inflation_nowcast(events: list[MacroScheduledEvent], forecast: dict[str, Any]) -> list[MacroScheduledEvent]:
    monthly = forecast.get("monthly") or []
    if not monthly:
        return events
    enriched = []
    for event in events:
        latest = next(
            (row for row in monthly if clean_text(row.get("month", "")).lower() == clean_text(event.reference or "").lower()),
            monthly[0],
        )
        lower = event.category.lower()
        summary = None
        if "cpi" in lower:
            summary = "Cleveland Fed nowcast: CPI MoM {cpi}%, Core CPI MoM {core}% ({month}, updated {updated})".format(
                cpi=latest.get("cpi_mom", "n/a"),
                core=latest.get("core_cpi_mom", "n/a"),
                month=latest.get("month", "n/a"),
                updated=latest.get("updated", "n/a"),
            )
        elif "pce" in lower:
            summary = "Cleveland Fed nowcast: PCE MoM {pce}%, Core PCE MoM {core}% ({month}, updated {updated})".format(
                pce=latest.get("pce_mom", "n/a"),
                core=latest.get("core_pce_mom", "n/a"),
                month=latest.get("month", "n/a"),
                updated=latest.get("updated", "n/a"),
            )
        if summary:
            enriched.append(
                MacroScheduledEvent(
                    **{
                        **event.__dict__,
                        "forecast_summary": summary,
                        "forecast_sources": tuple(dict.fromkeys((*event.forecast_sources, "Cleveland Fed Inflation Nowcasting"))),
                        "forecast_confidence": "NOWCAST",
                    }
                )
            )
        else:
            enriched.append(event)
    return enriched


def dedupe_calendar(events: list[MacroScheduledEvent]) -> list[MacroScheduledEvent]:
    merged: dict[tuple[str, str], MacroScheduledEvent] = {}
    for event in events:
        key = (event.category, event.event_time_utc)
        existing = merged.get(key)
        if not existing or event.forecast_confidence != "SCHEDULE_ONLY":
            if existing:
                merged_fields = dict(event.__dict__)
                if not merged_fields.get("forecast_summary") and existing.forecast_summary:
                    merged_fields.update(
                        forecast_summary=existing.forecast_summary,
                        forecast_sources=existing.forecast_sources,
                        forecast_confidence=existing.forecast_confidence,
                    )
                if not merged_fields.get("actual_summary") and existing.actual_summary:
                    merged_fields.update(
                        actual_summary=existing.actual_summary,
                        actual_sources=existing.actual_sources,
                        surprise_summary=existing.surprise_summary,
                    )
                event = MacroScheduledEvent(**merged_fields)
            merged[key] = event
    return sorted(merged.values(), key=lambda item: (item.event_time_utc, -item.impact_score, item.title))


def link_fomc_outcomes(events: list[MacroScheduledEvent]) -> list[MacroScheduledEvent]:
    """Carry the numeric rate-decision outcome into the related press-conference alert."""
    decisions = {
        event.event_time_utc: event
        for event in events
        if event.category == "FOMC_RATE_DECISION"
    }
    linked: list[MacroScheduledEvent] = []
    for event in events:
        if event.category != "FOMC_PRESS_CONFERENCE" or event.actual_summary:
            linked.append(event)
            continue
        decision_time = (parse_iso(event.event_time_utc) - timedelta(minutes=30)).isoformat()
        decision = decisions.get(decision_time)
        if decision is None or not decision.actual_summary:
            linked.append(event)
            continue
        linked.append(
            MacroScheduledEvent(
                **{
                    **event.__dict__,
                    "forecast_summary": event.forecast_summary or decision.forecast_summary,
                    "forecast_sources": event.forecast_sources or decision.forecast_sources,
                    "forecast_confidence": event.forecast_confidence if event.forecast_summary else decision.forecast_confidence,
                    "actual_summary": f"Rate decision context: {decision.actual_summary}",
                    "actual_sources": decision.actual_sources,
                    "surprise_summary": decision.surprise_summary,
                }
            )
        )
    return linked


def fetch_macro_calendar(now_ms_value: int | None = None) -> dict[str, Any]:
    current = datetime.fromtimestamp((now_ms_value or now_ms()) / 1000, tz=timezone.utc)
    events: list[MacroScheduledEvent] = []
    source_states: list[dict[str, Any]] = []
    for source_name, url, parser in (
        ("Federal Reserve FOMC", SOURCE_URLS["fed_fomc"], lambda text: parse_fomc_schedule(text, current)),
        ("BLS", SOURCE_URLS["bls_ics"], parse_bls_ics),
        ("BEA", SOURCE_URLS["bea_schedule"], lambda text: parse_bea_schedule(text, current.year)),
        ("Census", SOURCE_URLS["census_schedule"], lambda text: parse_census_schedule(text, current.year)),
    ):
        try:
            parsed = parser(fetch_text(url))
            events.extend(parsed)
            source_states.append({"source": source_name, "status": "OK", "events": len(parsed), "url": url})
        except Exception as exc:
            source_states.append({"source": source_name, "status": "ERROR", "events": 0, "url": url, "error": type(exc).__name__})
    forecast_state: dict[str, Any] = {}
    try:
        forecast_state = parse_cleveland_nowcast(fetch_text(SOURCE_URLS["cleveland_nowcast"]))
        events = add_inflation_nowcast(events, forecast_state)
        source_states.append({"source": "Cleveland Fed Inflation Nowcasting", "status": "OK", "events": len(forecast_state.get("monthly", [])), "url": SOURCE_URLS["cleveland_nowcast"]})
    except Exception as exc:
        source_states.append({"source": "Cleveland Fed Inflation Nowcasting", "status": "ERROR", "events": 0, "url": SOURCE_URLS["cleveland_nowcast"], "error": type(exc).__name__})
    global _trading_economics_cache
    try:
        if trading_economics_refresh_due(current, events):
            te_events, te_state = fetch_trading_economics(current, LOOKAHEAD_DAYS)
            _trading_economics_cache = (current, te_events, te_state)
        else:
            cached_at, te_events, cached_state = _trading_economics_cache
            te_state = {
                **cached_state,
                "status": "CACHED",
                "cache_age_seconds": round((current - cached_at).total_seconds()),
            }
        events.extend(te_events)
        source_states.append(te_state)
    except Exception as exc:
        source_states.append({"source": "Trading Economics", "status": "ERROR", "events": 0, "url": SOURCE_URLS["trading_economics"], "error": type(exc).__name__})
    start = current - timedelta(minutes=LOOKBACK_MINUTES)
    end = current + timedelta(days=LOOKAHEAD_DAYS)
    filtered = [event for event in link_fomc_outcomes(dedupe_calendar(events)) if start <= parse_iso(event.event_time_utc) <= end]
    return {
        "engine_id": MACRO_ID,
        "calendar": [event.__dict__ for event in filtered],
        "source_states": source_states,
        "forecast_state": forecast_state,
        "fetched_utc": current.isoformat(),
    }


def alert_phase(
    event_time: datetime,
    current: datetime,
    *,
    actual_available: bool = False,
) -> tuple[str, float] | None:
    minutes_until = (event_time - current).total_seconds() / 60.0
    if minutes_until < 0:
        if minutes_until >= -LOOKBACK_MINUTES:
            return ("RESULT" if actual_available else "RESULT_PENDING"), minutes_until
        return None
    if minutes_until <= 2:
        return "LIVE", minutes_until
    for index, (threshold, phase) in enumerate(ALERT_PHASES):
        next_threshold = ALERT_PHASES[index + 1][0] if index + 1 < len(ALERT_PHASES) else 0
        if next_threshold < minutes_until <= threshold:
            return phase, minutes_until
    return None


def event_key(item: dict[str, Any]) -> str:
    raw = "|".join(
        [
            clean_text(item.get("source")),
            clean_text(item.get("category")),
            clean_text(item.get("event_time_utc")),
            clean_text(item.get("title")),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def market_impact_for_event(
    event_time_ms: int,
    market_klines_by_key: dict[tuple[str, str], list[list[Any]]] | None,
    *,
    horizon_minutes: int = 60,
) -> dict[str, Any] | None:
    """Measure the observed 1h crypto reaction; it is descriptive, not causal."""
    if not market_klines_by_key:
        return None
    assets: dict[str, dict[str, Any]] = {}
    post_target_ms = event_time_ms + horizon_minutes * 60 * 1000
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
        rows = market_klines_by_key.get((symbol, "1h"), [])
        eligible_before = []
        eligible_after = []
        for row in rows:
            if len(row) < 7:
                continue
            try:
                close_time_ms = int(row[6])
                close_price = float(row[4])
            except (TypeError, ValueError):
                continue
            if close_price <= 0:
                continue
            if close_time_ms <= event_time_ms:
                eligible_before.append((close_time_ms, close_price))
            if close_time_ms >= post_target_ms:
                eligible_after.append((close_time_ms, close_price))
        if not eligible_before or not eligible_after:
            continue
        before_time, before_price = max(eligible_before)
        after_time, after_price = min(eligible_after)
        return_pct = (after_price / before_price - 1.0) * 100.0
        assets[symbol] = {
            "return_pct": round(return_pct, 4),
            "before_price": round(before_price, 8),
            "after_price": round(after_price, 8),
            "before_time_utc": ms_to_iso(before_time),
            "after_time_utc": ms_to_iso(after_time),
        }
    if not assets:
        return None
    returns = [float(item["return_pct"]) for item in assets.values()]
    mean_return = sum(returns) / len(returns)
    if mean_return >= 0.20:
        direction = "TĂNG"
    elif mean_return <= -0.20:
        direction = "GIẢM"
    else:
        direction = "TRUNG TÍNH"
    return {
        "source": "Binance Futures nến 1h",
        "horizon": "1h sau sự kiện",
        "direction": direction,
        "basket_mean_pct": round(mean_return, 4),
        "up_count": sum(value >= 0 for value in returns),
        "down_count": sum(value < 0 for value in returns),
        "assets": assets,
        "note": "Phản ứng quan sát được sau sự kiện; không khẳng định quan hệ nhân quả.",
    }


def evaluate_macro_calendar(
    calendar_payload: dict[str, Any],
    now_ms_value: int | None = None,
    market_klines_by_key: dict[tuple[str, str], list[list[Any]]] | None = None,
) -> dict[str, Any]:
    current_ms = now_ms_value or now_ms()
    current = datetime.fromtimestamp(current_ms / 1000, tz=timezone.utc)
    calendar = calendar_payload.get("calendar", [])
    alerts: list[dict[str, Any]] = []
    next_event = None
    upcoming_count = 0
    result_available_count = 0
    result_pending_count = 0
    for item in calendar:
        event_time = parse_iso(item["event_time_utc"])
        minutes_until = (event_time - current).total_seconds() / 60.0
        if minutes_until >= 0:
            upcoming_count += 1
            if next_event is None or event_time < parse_iso(next_event["event_time_utc"]):
                next_event = item
        actual_summary = item.get("actual_summary")
        phase = alert_phase(event_time, current, actual_available=bool(actual_summary))
        if phase is None:
            continue
        phase_name, phase_minutes = phase
        if phase_name == "RESULT":
            result_available_count += 1
        elif phase_name == "RESULT_PENDING":
            result_pending_count += 1
        market_impact = None
        if phase_name in {"RESULT", "RESULT_PENDING"}:
            market_impact = market_impact_for_event(
                int(event_time.timestamp() * 1000),
                market_klines_by_key,
            )
        alert = {
            "event_id": f"{MACRO_ID}:{event_key(item)}:{phase_name}",
            "event_type": "MACRO_EVENT",
            "engine_id": MACRO_ID,
            "phase": phase_name,
            "title": item["title"],
            "category": item["category"],
            "priority": item["priority"],
            "impact_score": item["impact_score"],
            "source": item["source"],
            "source_url": item["source_url"],
            "reference": item.get("reference"),
            "rationale": item["rationale"],
            "forecast_summary": item.get("forecast_summary"),
            "forecast_sources": item.get("forecast_sources") or [],
            "forecast_confidence": item.get("forecast_confidence") or "SCHEDULE_ONLY",
            "actual_summary": actual_summary,
            "actual_sources": item.get("actual_sources") or [],
            "surprise_summary": item.get("surprise_summary"),
            "result_status": "AVAILABLE" if actual_summary else "PENDING_SOURCE",
            "market_impact": market_impact,
            "event_time_utc": item["event_time_utc"],
            "event_time_ms": int(event_time.timestamp() * 1000),
            "minutes_until": round(phase_minutes, 1),
            "detected_utc": ms_to_iso(current_ms),
            "notify_time_utc": ms_to_iso(current_ms),
            "expires_at_ms": current_ms + ALERT_RETRY_MINUTES * 60 * 1000,
            "watch_only": True,
            "paper_only": True,
            "auto_trade": False,
            "delivery_status": "PENDING",
        }
        alerts.append(alert)
    alerts.sort(key=lambda item: (-int(item["impact_score"]), abs(float(item["minutes_until"])), item["event_id"]))
    group = {
        "status": "UPCOMING" if upcoming_count else "NO_UPCOMING",
        "data_state": "FRESH" if any(s.get("status") == "OK" for s in calendar_payload.get("source_states", [])) else "DEGRADED",
        "candidate_count": len(calendar),
        "upcoming_count": upcoming_count,
        "result_available_count": result_available_count,
        "result_pending_count": result_pending_count,
        "alert_count": len(alerts),
        "next_event": next_event,
        "source_states": calendar_payload.get("source_states", []),
        "fetched_utc": calendar_payload.get("fetched_utc"),
    }
    return {"engine_id": MACRO_ID, "events": alerts, "groups": [group], "calendar": calendar, "source_states": calendar_payload.get("source_states", [])}
