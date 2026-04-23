from __future__ import annotations

import re
from collections import defaultdict

from src.detector.models import AntiBotObservation, AntiBotSeverity, ProbeResult


_SEVERITY_ORDER = {
    AntiBotSeverity.NONE: 0,
    AntiBotSeverity.INFO: 1,
    AntiBotSeverity.WARNING: 2,
    AntiBotSeverity.BLOCKING: 3,
}


def _upgrade(current: AntiBotSeverity, candidate: AntiBotSeverity) -> AntiBotSeverity:
    if _SEVERITY_ORDER[candidate] > _SEVERITY_ORDER[current]:
        return candidate
    return current


def inspect_probe(probe: ProbeResult) -> AntiBotObservation:
    severity = AntiBotSeverity.NONE
    signals: list[str] = []
    details: dict[str, object] = {}
    text_lower = probe.text.lower()
    header_blob = "\n".join(f"{key.lower()}: {value.lower()}" for key, value in probe.headers.items())

    if any(token in text_lower for token in ("g-recaptcha", "grecaptcha", "recaptcha/api.js")):
        severity = _upgrade(severity, AntiBotSeverity.BLOCKING)
        signals.append("visible_recaptcha")
    if "hcaptcha" in text_lower:
        severity = _upgrade(severity, AntiBotSeverity.BLOCKING)
        signals.append("visible_hcaptcha")
    if "turnstile" in text_lower:
        severity = _upgrade(severity, AntiBotSeverity.BLOCKING)
        signals.append("visible_turnstile")
    if any(token in text_lower for token in ("attention required", "challenge page", "cf-chl", "checking your browser")):
        severity = _upgrade(severity, AntiBotSeverity.BLOCKING)
        signals.append("challenge_page_marker")
    if any(token in header_blob for token in ("cf-ray", "__cf_bm", "cloudflare")):
        severity = _upgrade(severity, AntiBotSeverity.WARNING)
        signals.append("cloudflare_header_marker")
    if "captcha" in text_lower and not any(signal.startswith("visible_") for signal in signals):
        severity = _upgrade(severity, AntiBotSeverity.WARNING)
        signals.append("generic_captcha_text")
    if any(token in text_lower for token in ('name="form_token"', "name=\"csrf", "name=\"_token")):
        severity = _upgrade(severity, AntiBotSeverity.INFO)
        signals.append("tokenized_form_present")
    if any(token in header_blob for token in ("phpsessid", "fe_typo_user", "set-cookie")):
        severity = _upgrade(severity, AntiBotSeverity.INFO)
        signals.append("session_cookie_present")
    if any(token in text_lower for token in ("regformhash(", "formhash(", "hex_sha512(")):
        severity = _upgrade(severity, AntiBotSeverity.INFO)
        signals.append("javascript_password_hashing_present")

    hidden_inputs = len(re.findall(r'<input[^>]+type=["\']hidden["\']', probe.text, re.IGNORECASE))
    if hidden_inputs:
        severity = _upgrade(severity, AntiBotSeverity.INFO)
        signals.append("hidden_inputs_present")
        details["hidden_input_count"] = hidden_inputs

    if "javascript is disabled" in text_lower:
        severity = _upgrade(severity, AntiBotSeverity.INFO)
        signals.append("javascript_expected")

    return AntiBotObservation(
        severity=severity,
        signals=tuple(dict.fromkeys(signals)),
        details=details,
    )


def merge_observations(observations: list[AntiBotObservation]) -> AntiBotObservation:
    severity = AntiBotSeverity.NONE
    signals: list[str] = []
    merged_details: dict[str, object] = defaultdict(list)

    for observation in observations:
        severity = _upgrade(severity, observation.severity)
        signals.extend(observation.signals)
        for key, value in observation.details.items():
            merged_details[key].append(value)

    normalized_details = {key: value if len(value) > 1 else value[0] for key, value in merged_details.items()}
    return AntiBotObservation(
        severity=severity,
        signals=tuple(dict.fromkeys(signals)),
        details=normalized_details,
    )

