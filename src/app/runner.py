from __future__ import annotations

import random
import time
from datetime import timedelta

from src.orchestrator.service import DormAlertService
from src.utils.time import utcnow


class ContinuousRunner:
    def __init__(self, service: DormAlertService) -> None:
        self.service = service

    def run(self, site_ids: list[str]) -> None:  # pragma: no cover - exercised in live operation
        next_run = {site_id: utcnow() for site_id in site_ids}

        while True:
            now = utcnow()
            due_sites = [site_id for site_id, due_at in next_run.items() if due_at <= now]
            if not due_sites:
                time.sleep(1)
                continue

            for site_id in due_sites:
                self.service.inspect_site(site_id)
                site_config = self.service.config.sites[site_id]
                delay = site_config.poll_interval_seconds + random.uniform(0, site_config.jitter_seconds)
                next_run[site_id] = utcnow() + timedelta(seconds=delay)

