from __future__ import annotations

import argparse
import json
from dataclasses import replace
from typing import Iterable

from src.app.runner import ContinuousRunner
from src.config.models import SubmissionMode
from src.config.settings import load_settings
from src.detector.engine import PageStateDetector
from src.detector.http_client import HttpProbeClient
from src.detector.profile import build_site_profiles
from src.diagnostics.artifacts import ArtifactManager
from src.notifier.registry import build_notifier
from src.orchestrator.service import DormAlertService
from src.persistence.sqlite_store import SQLiteStateStore
from src.utils.logging import configure_logging
from src.utils.serialization import to_jsonable
from src.verifier.rules import RuleBasedVerifier


def _selected_sites(all_site_ids: Iterable[str], requested: list[str] | None) -> list[str]:
    if not requested:
        return list(all_site_ids)
    return requested


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

    subparsers.add_parser("status", help="Show runtime status")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    service = _build_service(detector_only_override=getattr(args, "detector_only", False))
    site_ids = [
        site_id
        for site_id, site_config in service.config.sites.items()
        if site_config.enabled
    ]

    if args.command == "run":
        runner = ContinuousRunner(service)
        runner.run(_selected_sites(site_ids, args.sites))
        return

    if args.command == "detect-once":
        for site_id in _selected_sites(site_ids, args.sites):
            result = service.inspect_site(site_id)
            print(json.dumps(to_jsonable(result), indent=2, ensure_ascii=True))
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

    if args.command == "status":
        print(json.dumps(to_jsonable(service.status_snapshot()), indent=2, ensure_ascii=True))
        return


if __name__ == "__main__":
    main()
