from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SubmissionMode(str, Enum):
    DISABLED = "disabled"
    DRY_RUN = "dry_run"
    LIVE = "live"


@dataclass(frozen=True)
class NotificationSettings:
    enable_console: bool
    webhook_url: str | None
    webhook_timeout_seconds: int


@dataclass(frozen=True)
class BrowserSettings:
    headless: bool
    slow_mo_ms: int


@dataclass(frozen=True)
class SiteMonitorConfig:
    site_id: str
    enabled: bool
    poll_interval_seconds: int
    jitter_seconds: float
    timeout_seconds: int
    max_retries: int
    submission_mode: SubmissionMode


@dataclass(frozen=True)
class StudentVillageApplicant:
    firstname: str
    lastname: str
    email: str
    second_email: str
    address: str
    zipcode: str
    city: str
    country: str
    phonenumber: str
    dob: str
    roomnumberonregister: str
    parents: str
    nationality: str
    studentfaculty: str
    spokenlanguage: str
    gender: str
    username: str
    password: str
    request_rentaldate: str
    comments: str

    def form_values(self) -> dict[str, str]:
        second_email = self.second_email or self.email
        request_rentaldate = self.request_rentaldate or "--"
        return {
            "firstname": self.firstname,
            "lastname": self.lastname,
            "email": self.email,
            "second_email": second_email,
            "address": self.address,
            "zipcode": self.zipcode,
            "city": self.city,
            "country": self.country,
            "phonenumber": self.phonenumber,
            "dob": self.dob,
            "roomnumberonregister": self.roomnumberonregister,
            "parents": self.parents,
            "nationality": self.nationality,
            "studentfaculty": self.studentfaculty,
            "spokenlanguage": self.spokenlanguage,
            "gender": self.gender,
            "username": self.username,
            "password": self.password,
            "request_rentaldate": request_rentaldate,
            "comments": self.comments,
        }

    def missing_required_fields(self) -> tuple[str, ...]:
        values = self.form_values()
        required = (
            "firstname",
            "lastname",
            "dob",
            "nationality",
            "spokenlanguage",
            "gender",
            "email",
            "second_email",
            "address",
            "zipcode",
            "city",
            "country",
            "phonenumber",
            "studentfaculty",
            "username",
            "password",
            "request_rentaldate",
        )
        missing = [name for name in required if not values.get(name)]
        return tuple(missing)

    def redacted_summary(self) -> dict[str, str]:
        summary = self.form_values()
        if summary.get("password"):
            summary["password"] = "***redacted***"
        return summary


@dataclass(frozen=True)
class AppConfig:
    database_path: Path
    artifacts_dir: Path
    log_dir: Path
    log_level: str
    user_agent: str
    detector_only: bool
    notification: NotificationSettings
    browser: BrowserSettings
    failure_alert_threshold: int
    sites: dict[str, SiteMonitorConfig]
    studentvillage_applicant: StudentVillageApplicant | None
    studentvillage_success_phrases: tuple[str, ...]
    studentvillage_failure_phrases: tuple[str, ...]

