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


def test_fingerprint_is_stable_across_rotating_tokens_and_scripts() -> None:
    profile = StudentVillageProfile()
    html_template = """
    <html><head><script>var token="{token}";</script></head>
    <body>
      <p>All rooms are currently occupied</p>
      <!-- generated at {timestamp} -->
      <script>tracking("{timestamp}")</script>
    </body></html>
    """
    probes_a = {
        "home": make_probe("home", "https://studentvillage.ch/en/", html_template.format(token="AAA", timestamp="2026-04-23T18:00:00Z")),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            '<p>Currently all rooms are rented. We do not have a waiting list.</p>'
            '<form id="register_form"><input type="hidden" name="form_token" value="AAA"></form>',
        ),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", '<p>There are currently no rooms available and we do not have a waiting list.</p>'),
    }
    probes_b = {
        "home": make_probe("home", "https://studentvillage.ch/en/", html_template.format(token="BBB", timestamp="2026-04-23T18:05:00Z")),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            '<p>Currently all rooms are rented. We do not have a waiting list.</p>'
            '<form id="register_form"><input type="hidden" name="form_token" value="BBB"></form>',
        ),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", '<p>There are currently no rooms available and we do not have a waiting list.</p>'),
    }

    result_a = profile.classify(probes_a, AntiBotObservation(AntiBotSeverity.INFO))
    result_b = profile.classify(probes_b, AntiBotObservation(AntiBotSeverity.INFO))

    assert len(result_a.fingerprint) == 64
    assert result_a.fingerprint != "0" * 64
    assert result_a.fingerprint == result_b.fingerprint


def test_fingerprint_text_strips_scripts_styles_comments_and_hidden_values() -> None:
    from src.detector.profile import _fingerprint_text

    html = (
        '<html><head>'
        '<style>.rotating-color { color: red; }</style>'
        '<script>var token = "AAA";</script>'
        '<noscript>Please enable JavaScript.</noscript>'
        '</head><body>'
        '<!-- build timestamp 2026-04-23T18:00:00Z -->'
        '<p>Visible text.</p>'
        '<form><input type="hidden" name="csrf" value="AAA"></form>'
        '</body></html>'
    )

    output = _fingerprint_text(html)

    assert "Visible text." in output
    assert "AAA" not in output
    assert "rotating-color" not in output
    assert "enable JavaScript" not in output
    assert "build timestamp" not in output


def test_livingscience_does_not_fire_open_on_incidental_form() -> None:
    profile = LivingScienceProfile()
    probe = make_probe(
        "home",
        "https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        """
        <html><body>
        <h1>Online bewerben</h1>
        <p>Bitte pruefen Sie spaeter erneut.</p>
        <form id="newsletter-signup" class="tx_powermail">
          <input type="email" name="email">
        </form>
        </body></html>
        """,
    )

    result = profile.classify({"home": probe}, AntiBotObservation(AntiBotSeverity.NONE))

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert result.signal_scores["open_marker_strength"] < 0.9


def test_livingscience_closed_state_tolerant_to_minor_phrase_edits() -> None:
    profile = LivingScienceProfile()
    probe = make_probe(
        "home",
        "https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        """
        <html><body>
        <p>Leider sind unsere Wartelisten derzeit voll.</p>
        <p>Sobald die Warteliste wieder geöffnet ist, wird das Anmeldeformular wieder verfügbar sein.</p>
        </body></html>
        """,
    )

    result = profile.classify({"home": probe}, AntiBotObservation(AntiBotSeverity.NONE))

    assert result.state is DetectorState.CLOSED
    assert "closed_phrase_markers_present" in result.signals


def test_studentvillage_open_requires_register_form_present_on_apply_page() -> None:
    profile = StudentVillageProfile()
    probes = {
        "home": make_probe("home", "https://studentvillage.ch/en/", "<p>Welcome to Student Village</p>" + "x" * 600),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            "<p>We are updating the application experience. Please check back shortly.</p>" + "x" * 600,
        ),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", "<p>Application support</p>" + "x" * 600),
    }

    result = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO))

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert "register_form_missing" in result.signals


def test_fingerprint_changes_when_state_version_changes(monkeypatch) -> None:
    profile = StudentVillageProfile()
    probes = {
        "home": make_probe("home", "https://studentvillage.ch/en/", "<p>All rooms are currently occupied</p>"),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            '<p>Currently all rooms are rented. We do not have a waiting list.</p>'
            '<form id="register_form"><input type="hidden" name="form_token" value="AAA"></form>',
        ),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", '<p>There are currently no rooms available and we do not have a waiting list.</p>'),
    }

    baseline = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO)).fingerprint

    monkeypatch.setattr(type(profile), "state_version", "test-bumped-version")
    bumped = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO)).fingerprint

    assert baseline != bumped


def test_studentvillage_does_not_fire_open_on_cookie_banner_only_page() -> None:
    """A page whose visible text is only a cookie consent overlay must not fire OPEN."""
    profile = StudentVillageProfile()
    apply_html = (
        "<div id='cookie-consent'><p>This site uses cookies.</p><button>Accept</button></div>"
        + "<p>placeholder</p>" * 40
    )
    probes = {
        "home": make_probe("home", "https://studentvillage.ch/en/", apply_html),
        "apply": make_probe("apply", "https://studentvillage.ch/en/apply/", apply_html),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", apply_html),
    }

    result = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO))

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert "register_form_missing" in result.signals


def test_livingscience_does_not_fire_open_on_cookie_banner_only_page() -> None:
    profile = LivingScienceProfile()
    probe = make_probe(
        "home",
        "https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        "<div id='cookie'><p>We use cookies.</p></div>" + "<p>x</p>" * 80,
    )

    result = profile.classify({"home": probe}, AntiBotObservation(AntiBotSeverity.NONE))

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert result.signal_scores["open_marker_strength"] < 0.9


def test_studentvillage_does_not_fire_open_on_registration_form_without_matching_token() -> None:
    """If the site ships a visually similar form but without the expected hidden form_token, do not fire OPEN."""
    profile = StudentVillageProfile()
    probes = {
        "home": make_probe("home", "https://studentvillage.ch/en/", "<p>Welcome</p>" + "x" * 600),
        "apply": make_probe(
            "apply",
            "https://studentvillage.ch/en/apply/",
            '<form id="register_form"><input name="email"></form>' + "x" * 600,
        ),
        "contact": make_probe("contact", "https://studentvillage.ch/en/contact/", "<p>support</p>" + "x" * 600),
    }

    result = profile.classify(probes, AntiBotObservation(AntiBotSeverity.INFO))

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert "register_form_missing" in result.signals


def test_livingscience_markers_do_not_match_across_a_period() -> None:
    """An open-state page that uses 'voll' in a different sentence near 'warteliste' must not classify as CLOSED."""
    profile = LivingScienceProfile()
    probe = make_probe(
        "home",
        "https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        """
        <html><body>
        <p>Die Warteliste ist jetzt wieder geöffnet. Voll von neuen Möglichkeiten für Sie.</p>
        </body></html>
        """,
    )

    result = profile.classify({"home": probe}, AntiBotObservation(AntiBotSeverity.NONE))

    assert result.state is DetectorState.OPENING_CANDIDATE

