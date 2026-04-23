from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def utcnow_iso(value: datetime | None = None) -> str:
    current = value or utcnow()
    return current.isoformat().replace("+00:00", "Z")


def timestamp_slug(value: datetime | None = None) -> str:
    current = value or utcnow()
    return current.strftime("%Y%m%dT%H%M%SZ")

