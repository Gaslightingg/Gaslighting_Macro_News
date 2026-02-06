from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..utils.settings import get_settings


def add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class NewsRange:
    start_local: datetime
    end_local: datetime
    tz: ZoneInfo
    start_utc: datetime
    end_utc: datetime


def get_news_timezone() -> ZoneInfo:
    settings = get_settings()
    return ZoneInfo(settings.news_timezone)


def parse_local_date(date_str: str, tz: ZoneInfo) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def build_news_range(start_date: str, end_date: str, tz: ZoneInfo) -> NewsRange:
    start_day = parse_local_date(start_date, tz)
    end_day = parse_local_date(end_date, tz)
    start_local = datetime.combine(start_day, datetime.min.time(), tzinfo=tz)
    end_local = datetime.combine(end_day, datetime.min.time(), tzinfo=tz) + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return NewsRange(
        start_local=start_local,
        end_local=end_local,
        tz=tz,
        start_utc=start_utc,
        end_utc=end_utc,
    )


def parse_event_time(event_time: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(event_time)
    except ValueError:
        try:
            parsed = datetime.strptime(event_time, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_event_time(event: dict, tz: ZoneInfo) -> datetime | None:
    for field in ("datetime_local", "datetime_utc"):
        raw = event.get(field)
        if not raw:
            continue
        parsed = parse_event_time(str(raw))
        if parsed is None:
            continue
        if field == "datetime_local" and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    return None


def filter_events_by_range(
    events: list[dict],
    start_date: str,
    end_date: str,
    tz: ZoneInfo,
) -> tuple[list[dict], NewsRange, list[datetime]]:
    news_range = build_news_range(start_date, end_date, tz)
    kept: list[dict] = []
    normalized_times: list[datetime] = []
    for event in events:
        event_time = normalize_event_time(event, tz)
        if event_time is None:
            continue
        normalized_times.append(event_time)
        if news_range.start_local <= event_time < news_range.end_local:
            kept.append(event)
    return kept, news_range, normalized_times


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - datetime(year, month, 1)).days
