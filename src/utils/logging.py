from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp_utc": datetime.fromtimestamp(record.created, UTC).isoformat().replace(
                "+00:00", "Z"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        if extras:
            payload["context"] = _json_safe(extras)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, sort_keys=True)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S")
        level = record.levelname
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        event = str(extras.get("event", ""))

        if event == "probe_completed":
            return (
                f"{timestamp} | {level:<7} | probe | target={extras.get('site_target')} "
                f"status={extras.get('status_code')} duration_ms={extras.get('duration_ms')}"
            )
        if event == "probe_attempt_failed":
            return (
                f"{timestamp} | {level:<7} | probe | target={extras.get('site_target')} "
                f"attempt={extras.get('attempt')}/{extras.get('max_attempts')} error={extras.get('error')}"
            )
        if event == "detection_cycle_complete":
            return (
                f"{timestamp} | {level:<7} | detect | site={extras.get('site_id')} "
                f"state={extras.get('state')} conf={extras.get('confidence')} "
                f"transition={extras.get('transition')} reason={extras.get('state_reason')}"
            )
        if event == "heartbeat":
            return (
                f"{timestamp} | {level:<7} | heartbeat | active_openings={extras.get('active_opening_count')} "
                f"sites={extras.get('site_count')} failures={extras.get('failure_counts')}"
            )
        if event in {"notification_console", "notification_webhook", "notification_email"}:
            return (
                f"{timestamp} | {level:<7} | notify | site={extras.get('site_id')} "
                f"type={extras.get('notification_type')}"
            )
        if event in {"notification_email_retry", "notifier_failed", "site_inspection_exception"}:
            return (
                f"{timestamp} | {level:<7} | {record.name} | "
                f"{record.getMessage()} | context={_json_safe(extras)}"
            )

        return f"{timestamp} | {level:<7} | {record.name} | {record.getMessage()}"


def configure_logging(level: str, log_file: Path) -> None:
    json_formatter = JsonFormatter()
    human_formatter = HumanFormatter()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(human_formatter)
    root.addHandler(stream_handler)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    root.addHandler(file_handler)

    # Keep third-party wire-level request logs out of the console by default.
    logging.getLogger("httpx").setLevel(logging.WARNING)
