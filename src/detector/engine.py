from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Iterable

from src.config.models import SiteMonitorConfig
from src.detector.antibot import inspect_probe, merge_observations
from src.detector.http_client import HttpProbeClient, ProbeError
from src.detector.models import (
    AntiBotObservation,
    AntiBotSeverity,
    DetectionExecution,
    DetectionResult,
    DetectorState,
    ProbeResult,
)
from src.detector.profile import SiteProfile
from src.utils.time import utcnow_iso


class PageStateDetector:
    def __init__(self, client: HttpProbeClient) -> None:
        self.client = client

    def detect(self, profile: SiteProfile, config: SiteMonitorConfig) -> DetectionExecution:
        probes: list[ProbeResult] = []
        errors: list[str] = []
        observations: list[AntiBotObservation] = []

        for target in profile.targets:
            try:
                probe = self.client.fetch(
                    target,
                    timeout_seconds=config.timeout_seconds,
                    max_retries=config.max_retries,
                )
                probes.append(probe)
                observations.append(inspect_probe(probe))
            except ProbeError as exc:
                errors.append(str(exc))

        merged_antibot = merge_observations(observations)
        if errors:
            result = self._failed_result(profile, probes, merged_antibot, errors)
            return DetectionExecution(result=result, probes=tuple(probes))

        probe_map = {probe.target_name: probe for probe in probes}
        result = profile.classify(probe_map, merged_antibot)

        if merged_antibot.severity is AntiBotSeverity.BLOCKING:
            result = replace(
                result,
                state=DetectorState.FAILED,
                confidence=min(result.confidence, 0.35),
                signals=tuple(dict.fromkeys(result.signals + ("blocking_antibot_observed",))),
                inferences=result.inferences
                + (
                    "A blocking anti-bot marker is present, so the normal application state cannot be trusted.",
                ),
                uncertainties=result.uncertainties
                + ("The site may be serving a challenge page instead of the normal flow.",),
            )

        return DetectionExecution(result=result, probes=tuple(probes))

    def _failed_result(
        self,
        profile: SiteProfile,
        probes: Iterable[ProbeResult],
        anti_bot: AntiBotObservation,
        errors: list[str],
    ) -> DetectionResult:
        fingerprint = hashlib.sha256("|".join(errors).encode("utf-8")).hexdigest()
        return DetectionResult(
            site_id=profile.site_id,
            display_name=profile.display_name,
            state=DetectorState.FAILED,
            confidence=0.0,
            signals=("probe_failure",),
            facts=tuple(f"Probe failure observed: {error}" for error in errors),
            inferences=("The detector could not classify the site with confidence in this cycle.",),
            uncertainties=("One or more target pages were unreachable or invalid.",),
            anti_bot=anti_bot,
            page_urls=tuple(probe.final_url for probe in probes) or tuple(
                target.url for target in profile.targets
            ),
            timestamp_utc=utcnow_iso(),
            fingerprint=fingerprint,
            metadata={"probe_errors": errors},
        )
