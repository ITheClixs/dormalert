from __future__ import annotations

from src.detector.antibot import merge_observations
from src.detector.models import AntiBotObservation, AntiBotSeverity, DetectorState, ProbeResult
from src.detector.profile import LivingScienceProfile, StudentVillageProfile


def make_probe(name: str, url: str, html: str) -> ProbeResult:
    return ProbeResult(
        target_name=name,
        requested_url=url,
        final_url=url,
        status_code=200,
        headers={"content-type": "text/html"},
        text=html,
        duration_ms=100,
        fetched_at="2026-04-23T18:00:00Z",
    )


def test_livingscience_closed_state_detected() -> None:
    profile = LivingScienceProfile()
    probe = make_probe(
        "home",
        "https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        """
        <html><body>
        <h1>Online bewerben</h1>
        <p>Unsere Wartelisten sind derzeit voll. Vorübergehend können wir keine neuen Anmeldungen annehmen.
        Sobald die Warteliste wieder geöffnet ist, wird das Anmeldeformular wieder zur Verfügung stehen.</p>
        </body></html>
        """,
    )
    result = profile.classify({"home": probe}, AntiBotObservation(AntiBotSeverity.NONE))
    assert result.state is DetectorState.CLOSED
    assert "closed_phrase_present" in result.signals


def test_livingscience_open_candidate_without_form() -> None:
    profile = LivingScienceProfile()
    probe = make_probe(
        "home",
        "https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        "<html><body><h1>Online bewerben</h1><p>Bitte pruefen Sie spaeter erneut.</p></body></html>",
    )
    result = profile.classify({"home": probe}, AntiBotObservation(AntiBotSeverity.NONE))
    assert result.state is DetectorState.OPENING_CANDIDATE


def test_studentvillage_closed_state_uses_banners_not_form_presence() -> None:
    profile = StudentVillageProfile()
    probes = {
        "home": make_probe("home", "https://studentvillage.ch/en/", "<p>All rooms are currently occupied</p>"),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            """
            <p>Currently all rooms are rented. We do not have a waiting list.</p>
            <form id="register_form">
              <input type="hidden" name="form_token" value="abc123">
              <input type="submit" value="Register" onclick="return regformhash(...)">
            </form>
            """,
        ),
        "contact": make_probe(
            "contact",
            "https://studentvillage.ch/en/contact/",
            "<p>There are currently no rooms available and we do not have a waiting list.</p>",
        ),
    }
    result = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO))
    assert result.state is DetectorState.CLOSED
    assert "register_form_present" in result.signals
    assert "apply_closed_banner_present" in result.signals


def test_studentvillage_open_requires_banner_removal_across_pages() -> None:
    profile = StudentVillageProfile()
    probes = {
        "home": make_probe("home", "https://studentvillage.ch/en/", "<p>Rooms available now</p>"),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            """
            <form id="register_form">
              <input type="hidden" name="form_token" value="abc123">
              <input type="submit" value="Register" onclick="return regformhash(...)">
            </form>
            """,
        ),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", "<p>Application support</p>"),
    }
    result = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO))
    assert result.state is DetectorState.OPEN
    assert "closed_banners_removed" in result.signals


def test_antibot_merge_keeps_highest_severity() -> None:
    merged = merge_observations(
        [
            AntiBotObservation(AntiBotSeverity.INFO, ("form_token_present",)),
            AntiBotObservation(AntiBotSeverity.BLOCKING, ("visible_recaptcha",)),
        ]
    )
    assert merged.severity is AntiBotSeverity.BLOCKING
    assert "visible_recaptcha" in merged.signals

