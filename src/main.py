from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from typing import Iterable

from src.app.runner import ContinuousRunner
from src.app.simulation import run_studentvillage_opening_simulation
from src.config.models import SubmissionMode
from src.config.settings import load_settings
from src.detector.engine import PageStateDetector
from src.detector.http_client import HttpProbeClient
from src.detector.profile import build_site_profiles
from src.diagnostics.artifacts import ArtifactManager
from src.notifier.base import NotificationEvent, NotificationSeverity
from src.notifier.registry import build_notifier
from src.orchestrator.service import DormAlertService
from src.persistence.sqlite_store import SQLiteStateStore
from src.utils.env import get_int
from src.utils.logging import configure_logging
from src.utils.serialization import to_jsonable
from src.utils.time import parse_utc_iso, utcnow_iso
from src.verifier.rules import RuleBasedVerifier


def _selected_sites(all_site_ids: Iterable[str], requested: list[str] | None) -> list[str]:
    if not requested:
        return list(all_site_ids)
    return requested


def _sites_crossing_failure_threshold(records: Iterable, threshold: int) -> list[str]:
    """Sites whose consecutive failures reached the alert threshold on this cycle.

    Matching exactly the threshold keeps this a one-shot signal per failure
    episode, so a CI run exits non-zero once instead of on every later cycle.
    """
    return [
        record.site_id
        for record in records
        if record.consecutive_failures == threshold
    ]


def _health_problems(
    records: Iterable,
    *,
    expected_site_ids: list[str],
    threshold: int,
    max_age_minutes: int,
    now: datetime,
) -> list[str]:
    """Problems that mean the monitor might miss a real opening.

    Empty list = healthy = no email. This backs the silent daily health check
    that replaced the always-on heartbeat email.
    """
    records_by_site = {record.site_id: record for record in records}
    problems: list[str] = []
    for site_id in expected_site_ids:
        record = records_by_site.get(site_id)
        if record is None:
            problems.append(
                f"{site_id}: no detection state recorded (detect runs may never have executed "
                "or the state cache was lost)"
            )
            continue
        if record.last_page_state == "failed" or record.consecutive_failures >= threshold:
            problems.append(
                f"{site_id}: detector is failing ({record.consecutive_failures} consecutive failures, "
                f"last state {record.last_page_state})"
            )
        age_minutes = (now - parse_utc_iso(record.last_checked_at)).total_seconds() / 60
        if age_minutes > max_age_minutes:
            problems.append(
                f"{site_id}: last checked {int(age_minutes)} minutes ago "
                f"(expected within {max_age_minutes}); the detect pipeline may have stopped"
            )
    return problems


def _heartbeat_title(livingscience: dict | None, threshold: int) -> str:
    if livingscience and (
        livingscience["last_page_state"] == "failed"
        or livingscience["consecutive_failures"] >= threshold
    ):
        return "DormAlert heartbeat: monitor alive but livingscience detection is FAILING"
    return "DormAlert heartbeat: monitor is alive"


def _build_service(detector_only_override: bool | None = None) -> DormAlertService:
    config = load_settings()
    if detector_only_override is True:
        config = replace(config, detector_only=True)

    configure_logging(config.log_level, config.log_dir / "app.log")
    profiles = build_site_profiles()
    client = HttpProbeClient(config.user_agent)
    detector = PageStateDetector(client)
    artifacts = ArtifactManager(config.artifacts_dir)
    store = SQLiteStateStore(config.database_path)
    notifier = build_notifier(config)
    verifier = RuleBasedVerifier(config)
    return DormAlertService(
        config=config,
        profiles=profiles,
        detector=detector,
        store=store,
        artifacts=artifacts,
        notifier=notifier,
        verifier=verifier,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DormAlert monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the continuous monitor")
    run_parser.add_argument("--site", action="append", dest="sites")
    run_parser.add_argument("--detector-only", action="store_true")

    detect_parser = subparsers.add_parser("detect-once", help="Run one detection cycle")
    detect_parser.add_argument("--site", action="append", dest="sites")
    detect_parser.add_argument("--detector-only", action="store_true")

    submit_parser = subparsers.add_parser("submit-once", help="Run a single submission workflow")
    submit_parser.add_argument("--site", required=True)
    mode_group = submit_parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true")
    mode_group.add_argument("--live", action="store_true")

    openings_parser = subparsers.add_parser("list-openings", help="List opening events")
    openings_parser.add_argument("--active-only", action="store_true")

    ack_parser = subparsers.add_parser("ack-opening", help="Acknowledge an opening event")
    ack_parser.add_argument("--event-id", required=True, type=int)

    simulate_parser = subparsers.add_parser(
        "simulate-opening",
        help="Simulate a confirmed waitlist opening through the normal notification path",
    )
    simulate_parser.add_argument("--site", required=True, choices=("studentvillage",))
    simulate_parser.add_argument("--send-email", action="store_true")

    subparsers.add_parser("test-email", help="Send a test email through the configured SMTP notifier")

    subparsers.add_parser("test-whatsapp", help="Send a test WhatsApp message through the CallMeBot notifier")

    subparsers.add_parser("test-telegram", help="Send a test message through the Telegram bot notifier")

    subparsers.add_parser(
        "send-heartbeat",
        help="Send a heartbeat email proving the monitor and email channel are alive (manual use)",
    )

    subparsers.add_parser(
        "check-health",
        help="Silent health check: email only if the monitor looks broken, otherwise send nothing",
    )

    subparsers.add_parser("status", help="Show runtime status")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "simulate-opening":
        if not args.send_email:
            raise SystemExit("Pass --send-email to send a real simulated opening email.")
        config = load_settings()
        if not config.notification.email_enabled:
            raise SystemExit(
                "DORMALERT_EMAIL_ENABLED must be true before running simulate-opening --send-email."
            )
        configure_logging(config.log_level, config.log_dir / "app.log")
        try:
            notifier = build_notifier(config)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        result = run_studentvillage_opening_simulation(
            config=config,
            notifier=notifier,
            send_email=args.send_email,
        )
        print(json.dumps(to_jsonable(result), indent=2, ensure_ascii=True))
        if not result.opening_email_succeeded:
            raise SystemExit("Simulated opening ran, but no email delivery succeeded.")
        return

    service = _build_service(detector_only_override=getattr(args, "detector_only", False))
    site_ids = [
        site_id
        for site_id, site_config in service.config.sites.items()
        if site_config.enabled
    ]

    if args.command == "run":
        runner = ContinuousRunner(service)
        try:
            runner.run(_selected_sites(site_ids, args.sites))
        except KeyboardInterrupt:  # pragma: no cover - interactive runtime behavior
            service.logger.info("Monitor stopped by user")
        return

    if args.command == "detect-once":
        for site_id in _selected_sites(site_ids, args.sites):
            result = service.inspect_site(site_id)
            print(json.dumps(to_jsonable(result), indent=2, ensure_ascii=True))
        crossing = _sites_crossing_failure_threshold(
            service.store.list_runtime_records(),
            service.config.failure_alert_threshold,
        )
        if crossing:
            raise SystemExit(
                f"Detection reached the failure threshold for: {', '.join(sorted(crossing))}. "
                "Exiting non-zero so the CI run is marked failed as a backstop alert."
            )
        return

    if args.command == "submit-once":
        if args.dry_run:
            mode = SubmissionMode.DRY_RUN
        elif args.live:
            mode = SubmissionMode.LIVE
        else:
            mode = service.config.sites[args.site].submission_mode
        result = service.submit_site_once(args.site, mode=mode)
        print(json.dumps(to_jsonable(result), indent=2, ensure_ascii=True))
        return

    if args.command == "list-openings":
        result = service.list_openings(active_only=args.active_only)
        print(json.dumps(to_jsonable(result), indent=2, ensure_ascii=True))
        return

    if args.command == "ack-opening":
        result = {"event_id": args.event_id, "acknowledged": service.acknowledge_opening(args.event_id)}
        print(json.dumps(to_jsonable(result), indent=2, ensure_ascii=True))
        return

    if args.command == "test-email":
        if not service.config.notification.email_enabled:
            raise SystemExit("DORMALERT_EMAIL_ENABLED must be true before running test-email.")
        deliveries = service.notifier.send(
            NotificationEvent(
                event_type="email_test",
                site_id="system",
                title="DormAlert email test",
                message=(
                    "This is a DormAlert SMTP test message. Receiving it outside spam confirms "
                    "the local SMTP route is usable before a real waitlist opening."
                ),
                severity=NotificationSeverity.INFO,
                payload={
                    "facts": (
                        "This message was triggered manually by python3 -m src.main test-email.",
                        "Real opening emails are sent only for confirmed open events and reminders.",
                    ),
                },
            )
        )
        print(json.dumps(to_jsonable(deliveries), indent=2, ensure_ascii=True))
        return

    if args.command == "test-whatsapp":
        whatsapp_configured = (
            service.config.notification.whatsapp_enabled
            or service.config.notification.wa_cloud_enabled
        )
        if not whatsapp_configured:
            raise SystemExit(
                "Enable a WhatsApp channel before running test-whatsapp: either "
                "DORMALERT_WA_CLOUD_ENABLED=true (with DORMALERT_WA_CLOUD_TOKEN, "
                "DORMALERT_WA_CLOUD_PHONE_NUMBER_ID, DORMALERT_WA_CLOUD_TO) or "
                "DORMALERT_WHATSAPP_ENABLED=true (CallMeBot, with DORMALERT_WHATSAPP_PHONE "
                "and DORMALERT_WHATSAPP_APIKEY)."
            )
        deliveries = service.notifier.send(
            NotificationEvent(
                event_type="whatsapp_test",
                site_id="system",
                title="DormAlert WhatsApp test",
                message=(
                    "This is a DormAlert WhatsApp test message. Receiving it confirms the "
                    "WhatsApp channel is working before a real waitlist opening."
                ),
                severity=NotificationSeverity.INFO,
            )
        )
        print(json.dumps(to_jsonable(deliveries), indent=2, ensure_ascii=True))
        if not any(
            delivery.delivery_kind == "whatsapp" and delivery.succeeded
            for delivery in deliveries
        ):
            raise SystemExit("WhatsApp test ran, but no WhatsApp delivery succeeded.")
        return

    if args.command == "test-telegram":
        if not service.config.notification.telegram_enabled:
            raise SystemExit(
                "DORMALERT_TELEGRAM_ENABLED must be true (with DORMALERT_TELEGRAM_BOT_TOKEN and "
                "DORMALERT_TELEGRAM_CHAT_ID) before running test-telegram."
            )
        deliveries = service.notifier.send(
            NotificationEvent(
                event_type="telegram_test",
                site_id="system",
                title="DormAlert Telegram test",
                message=(
                    "This is a DormAlert Telegram test message. Receiving it confirms the "
                    "Telegram bot is working before a real waitlist opening."
                ),
                severity=NotificationSeverity.INFO,
            )
        )
        print(json.dumps(to_jsonable(deliveries), indent=2, ensure_ascii=True))
        if not any(
            delivery.delivery_kind == "telegram" and delivery.succeeded
            for delivery in deliveries
        ):
            raise SystemExit("Telegram test ran, but no Telegram delivery succeeded.")
        return

    if args.command == "send-heartbeat":
        snapshot = service.status_snapshot()
        sites_by_id = {site["site_id"]: site for site in snapshot["sites"]}
        livingscience = sites_by_id.get("livingscience")
        if livingscience:
            detail = (
                f"livingscience last state: {livingscience['last_page_state']} "
                f"(workflow {livingscience['last_workflow_state']}, "
                f"checked {livingscience['last_checked_at']}, "
                f"consecutive failures {livingscience['consecutive_failures']})."
            )
        else:
            detail = "No detection recorded yet (first cycle pending or the state cache is empty)."
        deliveries = service.notifier.send(
            NotificationEvent(
                event_type="heartbeat",
                site_id="system",
                title=_heartbeat_title(livingscience, service.config.failure_alert_threshold),
                message=(
                    "DormAlert ran its scheduled heartbeat. " + detail + " If you stop receiving "
                    "this heartbeat email on schedule, assume the monitor is broken and investigate."
                ),
                severity=NotificationSeverity.INFO,
                payload={"active_openings": snapshot["active_openings"]},
            )
        )
        print(json.dumps(to_jsonable(deliveries), indent=2, ensure_ascii=True))
        if service.config.notification.email_enabled and not any(
            delivery.delivery_kind == "email" and delivery.succeeded
            for delivery in deliveries
        ):
            raise SystemExit("Heartbeat ran but email delivery failed.")
        return

    if args.command == "check-health":
        problems = _health_problems(
            service.store.list_runtime_records(),
            expected_site_ids=site_ids,
            threshold=service.config.failure_alert_threshold,
            max_age_minutes=get_int("DORMALERT_HEALTH_MAX_AGE_MINUTES", 60),
            now=parse_utc_iso(utcnow_iso()),
        )
        if not problems:
            print(json.dumps({"healthy": True, "sites": site_ids}, indent=2))
            return
        deliveries = service.notifier.send(
            NotificationEvent(
                event_type="health_alert",
                site_id="system",
                title="DormAlert health check FAILED - the monitor may be blind",
                message=(
                    "The daily silent health check found problems. Until they are fixed, "
                    "a real waitlist opening could go unnoticed. Problems: "
                    + "; ".join(problems)
                ),
                severity=NotificationSeverity.ERROR,
                payload={"facts": tuple(problems)},
            )
        )
        print(json.dumps(to_jsonable({"healthy": False, "problems": problems, "deliveries": deliveries}), indent=2))
        raise SystemExit("DormAlert health check failed: " + "; ".join(problems))

    if args.command == "status":
        print(json.dumps(to_jsonable(service.status_snapshot()), indent=2, ensure_ascii=True))
        return


if __name__ == "__main__":
    main()
