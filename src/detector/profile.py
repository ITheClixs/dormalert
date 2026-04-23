from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from bs4 import BeautifulSoup

from src.detector.models import (
    AntiBotObservation,
    DetectionResult,
    DetectorState,
    ProbeResult,
    ProbeTarget,
)
from src.utils.time import utcnow_iso


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _html_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


class SiteProfile:
    site_id: str
    display_name: str
    targets: tuple[ProbeTarget, ...]
    state_version = "2026-04-24.v2"

    def classify(
        self,
        probes: Mapping[str, ProbeResult],
        anti_bot: AntiBotObservation,
    ) -> DetectionResult:
        raise NotImplementedError

    def _fingerprint(self, probes: Mapping[str, ProbeResult]) -> str:
        digest = hashlib.sha256()
        for name in sorted(probes):
            probe = probes[name]
            normalized = _normalize_text(_html_text(probe.text))[:12000]
            digest.update(name.encode("utf-8"))
            digest.update(str(probe.status_code).encode("utf-8"))
            digest.update(normalized.encode("utf-8"))
        return digest.hexdigest()

    def _result(
        self,
        *,
        state: DetectorState,
        confidence: float,
        state_reason: str,
        signal_scores: dict[str, float],
        signals: tuple[str, ...],
        facts: tuple[str, ...],
        inferences: tuple[str, ...],
        uncertainties: tuple[str, ...],
        anti_bot: AntiBotObservation,
        probes: Mapping[str, ProbeResult],
    ) -> DetectionResult:
        return DetectionResult(
            site_id=self.site_id,
            display_name=self.display_name,
            state=state,
            confidence=confidence,
            state_reason=state_reason,
            signal_scores=signal_scores,
            state_version=self.state_version,
            signals=signals,
            facts=facts,
            inferences=inferences,
            uncertainties=uncertainties,
            anti_bot=anti_bot,
            page_urls=tuple(probe.final_url for probe in probes.values()),
            timestamp_utc=utcnow_iso(),
            fingerprint=self._fingerprint(probes),
        )


class LivingScienceProfile(SiteProfile):
    site_id = "livingscience"
    display_name = "Living Science"
    targets = (
        ProbeTarget(
            name="home",
            url="https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        ),
    )

    _closed_phrase = _normalize_text(
        "Unsere Wartelisten sind derzeit voll. Vorübergehend können wir keine neuen Anmeldungen annehmen. "
        "Sobald die Warteliste wieder geöffnet ist, wird das Anmeldeformular wieder zur Verfügung stehen."
    )

    def classify(
        self,
        probes: Mapping[str, ProbeResult],
        anti_bot: AntiBotObservation,
    ) -> DetectionResult:
        probe = probes["home"]
        html_lower = probe.text.lower()
        text_lower = _normalize_text(_html_text(probe.text))

        closed_visible = self._closed_phrase in text_lower
        form_visible = bool(re.search(r"<form\b", html_lower)) or "tx_powermail" in html_lower

        facts = [
            "Observed April 23, 2026 closed phrase location on the public page is directly monitorable in HTML.",
        ]
        signals = []
        inferences: list[str] = []
        uncertainties: list[str] = []

        if closed_visible:
            signals.extend(["closed_phrase_present", "html_monitorable"])
            facts.append("Exact livingscience closed-state phrase is present.")
            return self._result(
                state=DetectorState.CLOSED,
                confidence=0.99,
                state_reason="known_closed_phrase_present",
                signal_scores={
                    "closed_marker_strength": 1.0,
                    "open_marker_strength": 0.0,
                    "drift_risk": 0.0,
                },
                signals=tuple(signals),
                facts=tuple(facts),
                inferences=tuple(inferences),
                uncertainties=tuple(uncertainties),
                anti_bot=anti_bot,
                probes=probes,
            )

        if form_visible:
            signals.extend(["closed_phrase_absent", "form_marker_present"])
            facts.append("A form marker is visible on the monitored page.")
            inferences.append(
                "Because the prior closed banner disappeared and a form marker is now visible, the application flow may be open."
            )
            uncertainties.append("The reopened form has not yet been validated against a real successful submission.")
            return self._result(
                state=DetectorState.OPEN,
                confidence=0.9,
                state_reason="closed_phrase_absent_and_form_marker_present",
                signal_scores={
                    "closed_marker_strength": 0.0,
                    "open_marker_strength": 0.95,
                    "drift_risk": 0.1,
                },
                signals=tuple(signals),
                facts=tuple(facts),
                inferences=tuple(inferences),
                uncertainties=tuple(uncertainties),
                anti_bot=anti_bot,
                probes=probes,
            )

        signals.append("closed_phrase_absent")
        inferences.append(
            "The closed phrase is gone, but no strong public form marker is visible yet."
        )
        uncertainties.append(
            "The page may have changed for reasons other than the waitlist reopening."
        )
        return self._result(
            state=DetectorState.OPENING_CANDIDATE,
            confidence=0.6,
            state_reason="closed_phrase_absent_without_strong_open_marker",
            signal_scores={
                "closed_marker_strength": 0.0,
                "open_marker_strength": 0.35,
                "drift_risk": 0.65,
            },
            signals=tuple(signals),
            facts=tuple(facts),
            inferences=tuple(inferences),
            uncertainties=tuple(uncertainties),
            anti_bot=anti_bot,
            probes=probes,
        )


class StudentVillageProfile(SiteProfile):
    site_id = "studentvillage"
    display_name = "Student Village"
    targets = (
        ProbeTarget(name="home", url="https://studentvillage.ch/en/"),
        ProbeTarget(name="apply", url="https://studentvillage.ch/en/apply/"),
        ProbeTarget(name="contact", url="https://studentvillage.ch/en/contact/"),
    )

    _home_closed = "all rooms are currently occupied"
    _apply_closed = "currently all rooms are rented. we do not have a waiting list."
    _contact_closed = "there are currently no rooms available and we do not have a waiting list."

    def classify(
        self,
        probes: Mapping[str, ProbeResult],
        anti_bot: AntiBotObservation,
    ) -> DetectionResult:
        home = probes["home"]
        apply = probes["apply"]
        contact = probes["contact"]

        home_text = _normalize_text(_html_text(home.text))
        apply_text = _normalize_text(_html_text(apply.text))
        contact_text = _normalize_text(_html_text(contact.text))
        apply_html = apply.text.lower()

        home_closed = self._home_closed in home_text
        apply_closed = self._apply_closed in apply_text
        contact_closed = self._contact_closed in contact_text
        secondary_closed_visible = (
            self._contact_closed in apply_text
            or self._contact_closed in apply_html
            or contact_closed
        )
        register_form_present = 'id="register_form"' in apply_html
        form_token_present = 'name="form_token"' in apply_html
        hashing_present = "regformhash(" in apply_html

        facts = [
            "Observed April 23, 2026: the apply page already exposes a registration form while the closed banner is still present.",
        ]
        signals: list[str] = []
        inferences: list[str] = []
        uncertainties: list[str] = []

        if home_closed:
            signals.append("home_closed_banner_present")
        if apply_closed:
            signals.append("apply_closed_banner_present")
        if contact_closed:
            signals.append("contact_closed_banner_present")
        if secondary_closed_visible and not contact_closed:
            signals.append("secondary_closed_phrase_present_on_apply_page")
        if register_form_present:
            signals.append("register_form_present")
            facts.append("Student Village register form is present on the apply page.")
        if form_token_present:
            signals.append("form_token_present")
            facts.append("Student Village apply page includes a hidden form token.")
        if hashing_present:
            signals.append("password_hashing_present")
            facts.append("Student Village apply submit path uses client-side password hashing.")
        if secondary_closed_visible and not contact_closed:
            facts.append(
                "The secondary 'no rooms available' phrase is currently present on the apply page rather than only on the contact page."
            )

        if home_closed and apply_closed:
            facts.append("The strongest Student Village closed-state banners are both present on the home and apply pages.")
            return self._result(
                state=DetectorState.CLOSED,
                confidence=0.98 if secondary_closed_visible else 0.95,
                state_reason="strong_home_and_apply_closed_banners_present",
                signal_scores={
                    "closed_marker_strength": 0.98 if secondary_closed_visible else 0.92,
                    "open_marker_strength": 0.15,
                    "drift_risk": 0.05,
                },
                signals=tuple(signals),
                facts=tuple(facts),
                inferences=tuple(inferences),
                uncertainties=tuple(uncertainties),
                anti_bot=anti_bot,
                probes=probes,
            )

        if not apply_closed and not home_closed and not contact_closed:
            inferences.append(
                "The monitored closed-state language disappeared across the public pages while the apply path remains present."
            )
            uncertainties.append(
                "The final successful post-submit confirmation text still needs to be validated in live operation."
            )
            return self._result(
                state=DetectorState.OPEN,
                confidence=0.92,
                state_reason="closed_banners_removed_across_monitored_pages",
                signal_scores={
                    "closed_marker_strength": 0.0,
                    "open_marker_strength": 0.9,
                    "drift_risk": 0.15,
                },
                signals=tuple(signals + ["closed_banners_removed"]),
                facts=tuple(facts),
                inferences=tuple(inferences),
                uncertainties=tuple(uncertainties),
                anti_bot=anti_bot,
                probes=probes,
            )

        inferences.append(
            "One or more Student Village closed-state banners changed, but the monitored pages are not yet consistent enough for a high-confidence open declaration."
        )
        uncertainties.append(
            "The apply page can be present during the closed state, so partial banner changes are treated conservatively."
        )
        return self._result(
            state=DetectorState.OPENING_CANDIDATE,
            confidence=0.66,
            state_reason="public_pages_changed_but_open_not_confirmed",
            signal_scores={
                "closed_marker_strength": 0.45,
                "open_marker_strength": 0.5,
                "drift_risk": 0.7,
            },
            signals=tuple(signals + ["inconsistent_public_state"]),
            facts=tuple(facts),
            inferences=tuple(inferences),
            uncertainties=tuple(uncertainties),
            anti_bot=anti_bot,
            probes=probes,
        )


def build_site_profiles() -> dict[str, SiteProfile]:
    return {
        "livingscience": LivingScienceProfile(),
        "studentvillage": StudentVillageProfile(),
    }
