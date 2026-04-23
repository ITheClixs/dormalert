from __future__ import annotations

import random
import time
from datetime import timedelta

from src.orchestrator.service import DormAlertService
from src.utils.time import utcnow


class ContinuousRunner:
    WAIT_LOG_INTERVAL_SECONDS = 15

    def __init__(self, service: DormAlertService) -> None:
        self.service = service

    def run(self, site_ids: list[str]) -> None:  # pragma: no cover - exercised in live operation
        next_run = {site_id: utcnow() for site_id in site_ids}
        next_prune = utcnow()
        next_wait_log = utcnow()

        while True:
            now = utcnow()
            due_sites = [site_id for site_id, due_at in next_run.items() if due_at <= now]
            if not due_sites:
                if now >= next_wait_log:
                    self.service.log_scheduler_wait(next_run)
                    next_wait_log = utcnow() + timedelta(seconds=self.WAIT_LOG_INTERVAL_SECONDS)
                time.sleep(1)
                continue

            for site_id in due_sites:
                try:
                    self.service.inspect_site(site_id)
                except Exception as exc:
                    self.service.logger.exception(
                        "Unhandled site inspection error",
                        extra={
                            "event": "site_inspection_exception",
                            "site_id": site_id,
                            "error": str(exc),
                        },
                    )
                site_config = self.service.config.sites[site_id]
                delay = site_config.poll_interval_seconds + random.uniform(0, site_config.jitter_seconds)
                next_run[site_id] = utcnow() + timedelta(seconds=delay)

            self.service.log_heartbeat(due_sites)
            if utcnow() >= next_prune:
                self.service.prune_old_artifacts()
                next_prune = utcnow() + timedelta(hours=1)
