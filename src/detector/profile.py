from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from bs4 import BeautifulSoup, Comment

from src.detector.models import (
    AntiBotObservation,
    DetectionResult,
    DetectorState,
    ProbeResult,
    ProbeTarget,
)
from src.utils.time import utcnow_iso


WATCHED_CLOSED_TEXT_PRESENT_SIGNAL = "watched_closed_text_present"
WATCHED_CLOSED_TEXT_MISSING_SIGNAL = "watched_closed_text_missing"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _html_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _fingerprint_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    return soup.get_text(" ", strip=True)


class SiteProfile:
    site_id: str
    display_name: str
    targets: tuple[ProbeTarget, ...]
    state_version = "2026-04-23.reliability-v1"
    # Sites whose open-state markup has never been observed can opt in to full
    # opening-event treatment (initial alert + reminders + ack) when the
    # detector reaches OPENING_CANDIDATE with the watched closed text missing.
    candidate_open_alerts = False

    def classify(
        self,
        probes: Mapping[str, ProbeResult],
        anti_bot: AntiBotObservation,
    ) -> DetectionResult:
        raise NotImplementedError

    def _fingerprint(self, probes: Mapping[str, ProbeResult]) -> str:
        digest = hashlib.sha256()
        digest.update(self.state_version.encode("utf-8"))
        for name in sorted(probes):
            probe = probes[name]
            normalized = _normalize_text(_fingerprint_text(probe.text))[:12000]
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
        metadata: dict[str, object] | None = None,
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
            metadata=metadata or {},
        )


class LivingScienceProfile(SiteProfile):
    site_id = "livingscience"
    display_name = "Living Science"
    candidate_open_alerts = True
    targets = (
        ProbeTarget(
            name="living_en",
            url="https://livingscience.ch/wohnen-studieren-zuerich/?L=1",
        ),
        ProbeTarget(
            name="offer_en",
            url="https://livingscience.ch/angebot-studentenzimmer-zuerich/?id=&L=1",
        ),
    )

    _closed_phrase = _normalize_text(
        "Unsere Wartelisten sind derzeit voll. Vorübergehend können wir keine neuen Anmeldungen annehmen. "
        "Sobald die Warteliste wieder geöffnet ist, wird das Anmeldeformular wieder zur Verfügung stehen."
    )
    _watched_closed_text = (
        "Our waiting lists for rooms and studios are currently full. "
        "We are temporarily unable to accept new registrations. "
        "As soon as the waiting list is open again, the registration form will be available again. "
        "Thank you for your understanding."
    )
    _watched_closed_text_normalized = _normalize_text(_watched_closed_text.rstrip("."))
    _summary_closed_text = (
        "Our waiting lists are currently full. "
        "We are temporarily unable to accept new registrations. "
        "As soon as the waiting list is open again, the registration form will be available again. "
        "Thank you for your understanding"
    )
    _summary_closed_text_normalized = _normalize_text(_summary_closed_text)
    _closed_marker_full = re.compile(r"wartelisten?\b[^.]{0,25}\bvoll\b", re.IGNORECASE)
    _closed_marker_reopen = re.compile(r"warteliste\b[^.]{0,60}\bgeöffnet\b", re.IGNORECASE)

    def classify(
        self,
        probes: Mapping[str, ProbeResult],
        anti_bot: AntiBotObservation,
    ) -> DetectionResult:
        text_by_target = {
            name: _normalize_text(_html_text(probe.text))
            for name, probe in probes.items()
        }
        combined_text = " ".join(text_by_target.values())

        watched_text_present = any(
            self._watched_closed_text_normalized in text
            for text in text_by_target.values()
        )
        watched_text_targets = tuple(
            name
            for name, text in text_by_target.items()
            if self._watched_closed_text_normalized in text
        )
        summary_closed_visible = any(
            self._summary_closed_text_normalized in text
            for text in text_by_target.values()
        )
        closed_visible_literal = self._closed_phrase in combined_text
        closed_visible_markers = bool(
            self._closed_marker_full.search(combined_text)
            and self._closed_marker_reopen.search(combined_text)
        )
        closed_visible = (
            watched_text_present
            or summary_closed_visible
            or closed_visible_literal
            or closed_visible_markers
        )

        facts: list[str] = [
            "Observed April 23, 2026 closed phrase location on the public page is directly monitorable in HTML.",
        ]
        signals: list[str] = []
        inferences: list[str] = []
        uncertainties: list[str] = []
        watched_metadata: dict[str, object] = {
            "watched_closed_text": self._watched_closed_text,
            "watched_closed_text_status": "present" if watched_text_present else "missing",
            "watched_closed_text_targets": watched_text_targets,
            "watched_closed_text_note": (
                "This exact English LivingScience rooms-and-studios waitlist text is monitored "
                "as an immediate disappearance/change signal."
            ),
        }

        if watched_text_present:
            signals.append(WATCHED_CLOSED_TEXT_PRESENT_SIGNAL)
            facts.append("The monitored LivingScience English waitlist text is present.")
        else:
            signals.append(WATCHED_CLOSED_TEXT_MISSING_SIGNAL)
            facts.append(
                "The monitored LivingScience English waitlist text is absent or changed."
            )
            inferences.append(
                "This does not prove applications are open; it proves the watched closed-state text changed or disappeared."
            )

        if closed_visible:
            if watched_text_present:
                signals.extend(["closed_phrase_present", "html_monitorable"])
                facts.append("Exact monitored LivingScience English closed-state phrase is present.")
            elif summary_closed_visible:
                signals.extend(["closed_phrase_present", "html_monitorable"])
                facts.append("LivingScience summary closed-state phrase is present.")
            elif closed_visible_literal:
                signals.extend(["closed_phrase_present", "html_monitorable"])
                facts.append("Exact livingscience closed-state phrase is present.")
            else:
                signals.extend(["closed_phrase_markers_present", "html_monitorable"])
                facts.append(
                    "LivingScience closed-state markers (wartelisten…voll and warteliste…geöffnet) "
                    "are present in the same sentence; exact phrase may have been edited."
                )
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
                metadata=watched_metadata,
            )

        signals.append("closed_phrase_absent")
        inferences.append(
            "The closed phrase is gone, but the reopened application form has not yet been characterized in production. "
            "Holding at opening_candidate until an operator verifies the live open-state markup."
        )
        uncertainties.append(
            "The page may have changed for reasons other than the waitlist reopening "
            "(CMS edit, site redesign, A/B test, incidental form on the page)."
        )
        return self._result(
            state=DetectorState.OPENING_CANDIDATE,
            confidence=0.6,
            state_reason="closed_phrase_absent_pending_operator_verification",
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
            metadata=watched_metadata,
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
    _apply_closed_full = (
        "currently all rooms are rented. we do not have a waiting list. "
        "if you have any questions, please contact service@livit.ch."
    )
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
        watched_text_present = self._apply_closed_full in apply_text
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
        watched_metadata: dict[str, object] = {
            "watched_closed_text": (
                "Currently all rooms are rented. We do not have a waiting list. "
                "If you have any questions, please contact service@livit.ch."
            ),
            "watched_closed_text_status": "present" if watched_text_present else "missing",
            "watched_closed_text_targets": ("apply",) if watched_text_present else (),
            "watched_closed_text_note": (
                "This exact Student Village apply-page banner is monitored as an immediate disappearance/change signal."
            ),
        }

        if watched_text_present:
            signals.append(WATCHED_CLOSED_TEXT_PRESENT_SIGNAL)
            facts.append("The monitored Student Village apply-page closed banner is present.")
        else:
            signals.append(WATCHED_CLOSED_TEXT_MISSING_SIGNAL)
            facts.append(
                "The monitored Student Village apply-page closed banner is absent or changed."
            )
            inferences.append(
                "This does not prove rooms are available; it proves the watched closed-state banner changed or disappeared."
            )

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
                metadata=watched_metadata,
            )

        if not apply_closed and not home_closed and not contact_closed:
            if register_form_present and form_token_present:
                inferences.append(
                    "The monitored closed-state language disappeared across the public pages while the apply path remains present, "
                    "and the register form with its hidden form_token is still exposed."
                )
                uncertainties.append(
                    "The final successful post-submit confirmation text still needs to be validated in live operation."
                )
                return self._result(
                    state=DetectorState.OPEN,
                    confidence=0.92,
                    state_reason="closed_banners_removed_and_register_form_present",
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
                    metadata=watched_metadata,
                )

            signals.append("register_form_missing")
            inferences.append(
                "Closed banners are absent across the monitored pages, but the expected register form "
                "structure on the apply page is not present. This could be a redesign or a partial outage, "
                "not a genuine opening."
            )
            uncertainties.append(
                "Without the known register form markers, the apply flow cannot be treated as open."
            )
            return self._result(
                state=DetectorState.OPENING_CANDIDATE,
                confidence=0.55,
                state_reason="banners_removed_but_register_form_missing",
                signal_scores={
                    "closed_marker_strength": 0.0,
                    "open_marker_strength": 0.4,
                    "drift_risk": 0.8,
                },
                signals=tuple(signals),
                facts=tuple(facts),
                inferences=tuple(inferences),
                uncertainties=tuple(uncertainties),
                anti_bot=anti_bot,
                probes=probes,
                metadata=watched_metadata,
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
            metadata=watched_metadata,
        )


def build_site_profiles() -> dict[str, SiteProfile]:
    return {
        "livingscience": LivingScienceProfile(),
        "studentvillage": StudentVillageProfile(),
    }
