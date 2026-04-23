from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def utcnow_iso(value: datetime | None = None) -> str:
    current = value or utcnow()
    return current.isoformat().replace("+00:00", "Z")


def timestamp_slug(value: datetime | None = None) -> str:
    current = value or utcnow()
    return current.strftime("%Y%m%dT%H%M%SZ")


def parse_utc_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def add_minutes(value: str, minutes: int) -> str:
    return utcnow_iso(parse_utc_iso(value) + timedelta(minutes=minutes))


def age_in_days(created_at: str, *, reference: datetime | None = None) -> float:
    now = reference or utcnow()
    delta = now - parse_utc_iso(created_at)
    return delta.total_seconds() / 86400.0
