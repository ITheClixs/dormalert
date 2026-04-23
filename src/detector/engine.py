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


MIN_PLAUSIBLE_BODY_CHARS = 500


def _is_plausible_html(probe: ProbeResult) -> tuple[bool, str | None]:
    if probe.status_code < 200 or probe.status_code >= 300:
        return False, f"status_{probe.status_code}"
    content_type = (probe.headers.get("content-type") or probe.headers.get("Content-Type") or "").lower()
    if "html" not in content_type:
        return False, "non_html_content_type"
    if len(probe.text) < MIN_PLAUSIBLE_BODY_CHARS:
        return False, "body_too_short"
    return True, None


class PageStateDetector:
    def __init__(self, client: HttpProbeClient) -> None:
        self.client = client

    def detect(self, profile: SiteProfile, config: SiteMonitorConfig) -> DetectionExecution:
        probes: list[ProbeResult] = []
        errors: list[str] = []
        observations: list[AntiBotObservation] = []
        implausible_reasons: list[str] = []

        for target in profile.targets:
            try:
                probe = self.client.fetch(
                    target,
                    timeout_seconds=config.timeout_seconds,
                    max_retries=config.max_retries,
                )
                probes.append(probe)
                observations.append(inspect_probe(probe))
                plausible, reason = _is_plausible_html(probe)
                if not plausible:
                    implausible_reasons.append(f"{target.name}:{reason}")
            except ProbeError as exc:
                errors.append(str(exc))

        merged_antibot = merge_observations(observations)
        if errors:
            result = self._failed_result(profile, probes, merged_antibot, errors)
            return DetectionExecution(result=result, probes=tuple(probes))

        if implausible_reasons:
            result = self._implausible_result(profile, probes, merged_antibot, implausible_reasons)
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
            state_reason="probe_failure",
            signal_scores={
                "closed_marker_strength": 0.0,
                "open_marker_strength": 0.0,
                "drift_risk": 1.0,
            },
            state_version=profile.state_version,
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

    def _implausible_result(
        self,
        profile: SiteProfile,
        probes: Iterable[ProbeResult],
        anti_bot: AntiBotObservation,
        reasons: list[str],
    ) -> DetectionResult:
        probe_list = list(probes)
        fingerprint = hashlib.sha256("|".join(reasons).encode("utf-8")).hexdigest()
        return DetectionResult(
            site_id=profile.site_id,
            display_name=profile.display_name,
            state=DetectorState.FAILED,
            confidence=0.0,
            state_reason="implausible_response",
            signal_scores={
                "closed_marker_strength": 0.0,
                "open_marker_strength": 0.0,
                "drift_risk": 1.0,
            },
            state_version=profile.state_version,
            signals=("implausible_response",),
            facts=tuple(f"Implausible response: {reason}" for reason in reasons),
            inferences=(
                "At least one probe returned a response that cannot be trusted for state classification.",
            ),
            uncertainties=(
                "The site may be serving an error, maintenance, or non-HTML payload.",
            ),
            anti_bot=anti_bot,
            page_urls=tuple(probe.final_url for probe in probe_list) or tuple(
                target.url for target in profile.targets
            ),
            timestamp_utc=utcnow_iso(),
            fingerprint=fingerprint,
            metadata={"implausible_reasons": reasons},
        )
