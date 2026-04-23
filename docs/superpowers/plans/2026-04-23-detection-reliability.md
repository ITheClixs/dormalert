# Detector Reliability Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the concrete false-positive `OPEN` paths in the current detector and make the orchestrator's consecutive-confirmation safety net actually work, so `python3 -m src.main run --detector-only` can be trusted to flag an opening only when the opening is real.

**Architecture:** Keep the HTTP-first, profile-based detector in place. Harden it with (a) a response-plausibility gate before classification, (b) stable semantic fingerprints that survive page chrome churn, (c) positive OPEN signals required per site (not just absence of closed banners), (d) a time-windowed confirmation rule in the orchestrator, and (e) a dedicated false-positive test battery. Bump `state_version` once to invalidate cached fingerprints from prior runs.

**Tech Stack:** Python 3.11+, httpx, BeautifulSoup4, pytest, stdlib only (no new deps).

---

## Problems this plan targets

Each concrete risk has a task that closes it:

1. **LivingScience** — `bool(re.search(r"<form\b", html_lower))` matches *any* `<form>` on the page. A newsletter form, cookie-consent form, or search form will drive `DetectorState.OPEN` at `open_marker_strength=0.95`, which **bypasses the orchestrator's consecutive-confirmation gate** (that gate only engages when strength < 0.95). This is the single most dangerous false-positive path. → Task 3.
2. **StudentVillage** — declares `OPEN` purely on *absence* of closed banners. A maintenance page, a 5xx error, or a page where the banner text was simply moved to a different DOM node all satisfy "banner absent". No positive open signals are required. → Task 5.
3. **HTTP status codes are ignored.** A 4xx/5xx response that happens not to contain the closed phrase is treated as "banner gone" → `OPEN`. → Task 1.
4. **Tiny/JSON/maintenance-page responses are classified.** No body-size or content-type floor. → Task 1.
5. **Fingerprints include transient page chrome** (script contents, rotating form tokens in visible text, time/date stamps). When fingerprints flap between cycles, `_apply_confirmation_policy` *never* satisfies the `same_fingerprint and prior_positive` branch, so the "2 consecutive matching OPEN" rule silently never fires. → Task 2.
6. **LivingScience closed phrase is an exact literal match** on one German sentence. Any rewording turns `CLOSED` into `OPENING_CANDIDATE` even when the site is still obviously closed. → Task 4.
7. **Consecutive confirmation has no time floor.** Two detections in the same 1-second window (e.g. a CDN serving the same cached response twice) currently count as "consecutive". → Task 6.
8. **No test coverage for the error-page, truncated-body, or incidental-form false-positive cases** — only happy-path HTML. → Task 7.

Non-goals: no ML, no probabilistic rules engine, no new dependencies, no rewrite of the orchestrator's workflow-state model, no changes to submission or notifier code.

---

## File structure

Files touched by this plan:

- **Modify** `src/detector/engine.py` — add response plausibility gate before `profile.classify`.
- **Modify** `src/detector/profile.py` — strengthen per-site classification, stable fingerprint, bump `state_version`.
- **Modify** `src/orchestrator/service.py` — add time-window floor to consecutive-confirmation policy.
- **Modify** `src/config/models.py` — add two config fields for confirmation window.
- **Modify** `src/config/settings.py` — wire env vars for the new fields.
- **Modify** `.env.example` — document the new env vars.
- **Modify** `tests/test_detector_profiles.py` — new false-positive cases.
- **Create** `tests/test_detector_engine.py` — plausibility-gate tests (new file; detector engine currently has no dedicated test file).
- **Create** `tests/test_orchestrator_confirmation.py` — confirmation-window tests (new file; orchestrator test file exists but is already long).
- **Modify** `docs/detection_strategy.md` — document the new rules.

Each task below is self-contained: one concern, one or two test additions, minimal code changes, one commit.

---

## Task 1: Response plausibility gate in the detection engine

**Why:** Today a 502 error page that happens not to contain the closed phrase can satisfy StudentVillage's "banners absent" branch. Classification must refuse to run on non-plausible responses and return `FAILED`.

**Files:**
- Create: `tests/test_detector_engine.py`
- Modify: `src/detector/engine.py` (add helper + call site)
- Modify: `src/detector/profile.py:31` (no change here, but this task depends on `SiteProfile` being callable — confirm during implementation)

### Steps

- [ ] **Step 1: Write the failing tests**

Create `tests/test_detector_engine.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from src.config.models import SiteMonitorConfig, SubmissionMode
from src.detector.engine import PageStateDetector
from src.detector.models import DetectorState, ProbeResult, ProbeTarget
from src.detector.profile import LivingScienceProfile


def _make_probe(status_code: int, text: str, content_type: str = "text/html") -> ProbeResult:
    return ProbeResult(
        target_name="home",
        requested_url="https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        final_url="https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        status_code=status_code,
        headers={"content-type": content_type},
        text=text,
        duration_ms=10,
        fetched_at="2026-04-23T18:00:00Z",
    )


def _site_config() -> SiteMonitorConfig:
    return SiteMonitorConfig(
        site_id="livingscience",
        enabled=True,
        poll_interval_seconds=300,
        jitter_seconds=0.0,
        timeout_seconds=5,
        max_retries=0,
        submission_mode=SubmissionMode.DISABLED,
    )


def test_non_2xx_response_forces_failed_state() -> None:
    client = MagicMock()
    client.fetch.return_value = _make_probe(502, "<html>Bad gateway</html>")
    detector = PageStateDetector(client)

    execution = detector.detect(LivingScienceProfile(), _site_config())

    assert execution.result.state is DetectorState.FAILED
    assert "implausible_response" in execution.result.signals


def test_short_body_forces_failed_state() -> None:
    client = MagicMock()
    client.fetch.return_value = _make_probe(200, "ok")
    detector = PageStateDetector(client)

    execution = detector.detect(LivingScienceProfile(), _site_config())

    assert execution.result.state is DetectorState.FAILED
    assert "implausible_response" in execution.result.signals


def test_non_html_content_type_forces_failed_state() -> None:
    client = MagicMock()
    client.fetch.return_value = _make_probe(
        200,
        '{"status":"maintenance"}',
        content_type="application/json",
    )
    detector = PageStateDetector(client)

    execution = detector.detect(LivingScienceProfile(), _site_config())

    assert execution.result.state is DetectorState.FAILED
    assert "implausible_response" in execution.result.signals
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_detector_engine.py -v`

Expected: 3 FAIL. The detector currently calls `profile.classify` unconditionally and will return a profile-specific state (not FAILED).

- [ ] **Step 3: Add the plausibility helper and wire it into `PageStateDetector.detect`**

Edit `src/detector/engine.py`. Replace the existing file with:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_detector_engine.py tests/test_detector_profiles.py -v`

Expected: all PASS. Existing profile tests still pass because they use 200 + `text/html` + long-enough bodies.

- [ ] **Step 5: Commit**

```bash
git add src/detector/engine.py tests/test_detector_engine.py
git commit -m "feat(detector): reject implausible responses before classification"
```

---

## Task 2: Stable semantic fingerprint

**Why:** The orchestrator's consecutive-confirmation rule compares `runtime.last_fingerprint == result.fingerprint`. If fingerprints churn between cycles (from rotating form tokens, `<script>` contents, timestamp text, cache-bust query params rendered in links), the gate never fires. That silently turns the safety net off.

**Files:**
- Modify: `src/detector/profile.py` (replace `_fingerprint` and add helper)
- Modify: `tests/test_detector_profiles.py` (add stability test)

### Steps

- [ ] **Step 1: Write the failing test**

Append to `tests/test_detector_profiles.py`:

```python
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

    assert result_a.fingerprint == result_b.fingerprint
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_detector_profiles.py::test_fingerprint_is_stable_across_rotating_tokens_and_scripts -v`

Expected: FAIL. The current `_fingerprint` fingerprints `_html_text(probe.text)`, which for the bodies above includes the script-tag text (token value, timestamp) and the HTML comment.

- [ ] **Step 3: Implement stable fingerprinting in `src/detector/profile.py`**

At the top of the file, replace the `_html_text` helper and `SiteProfile._fingerprint` method. Leave the rest of the file alone for now.

Replace lines 19–25 (`_normalize_text` and `_html_text`) with:

```python
_HIDDEN_VALUE_RE = re.compile(
    r'(<input[^>]+type=["\']hidden["\'][^>]*value=)["\'][^"\']*["\']',
    re.IGNORECASE,
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _semantic_html(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, type(soup.Comment)) if False else False):
        comment.extract()
    return soup.get_text(" ", strip=True)


def _html_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _fingerprint_text(value: str) -> str:
    stripped = _HIDDEN_VALUE_RE.sub(r'\1""', value)
    soup = BeautifulSoup(stripped, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    from bs4 import Comment  # local import keeps module load fast
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    return soup.get_text(" ", strip=True)
```

Then replace `SiteProfile._fingerprint` (currently lines 40–48) with:

```python
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
```

Note: `_semantic_html` above is unused — delete it; it was only scaffolding in the diff. The final file should only have `_normalize_text`, `_html_text`, and `_fingerprint_text` as module-level helpers, plus the `_HIDDEN_VALUE_RE` constant.

- [ ] **Step 4: Run all detector tests**

Run: `pytest tests/test_detector_profiles.py tests/test_detector_engine.py -v`

Expected: all PASS, including the new stability test.

- [ ] **Step 5: Commit**

```bash
git add src/detector/profile.py tests/test_detector_profiles.py
git commit -m "feat(detector): stable semantic fingerprint ignoring scripts and rotating tokens"
```

---

## Task 3: LivingScience must require a real application form marker

**Why:** Today any `<form>` tag (newsletter, search, cookie consent) or any `tx_powermail` reference drives `OPEN` at `open_marker_strength=0.95`, which bypasses the orchestrator's consecutive-confirmation gate. This is the single most dangerous false-positive path in the current code.

Since the real open-state form is not yet observed in production (per `docs/submission_strategy.md`, livingscience is alert-first and submission-disabled), the correct conservative behavior is: **never** declare `OPEN` from the public page alone. When the closed phrase disappears, hold at `OPENING_CANDIDATE` with a clear `state_reason`. The orchestrator already alerts on that transition. When we actually see the reopened form in production, we can upgrade this rule with a verified form marker.

**Files:**
- Modify: `src/detector/profile.py` — `LivingScienceProfile.classify`
- Modify: `tests/test_detector_profiles.py` — add false-positive and update existing tests

### Steps

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_detector_profiles.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_detector_profiles.py::test_livingscience_does_not_fire_open_on_incidental_form -v`

Expected: FAIL. The current profile returns `OPEN` with `open_marker_strength=0.95` because `<form\b` matches.

- [ ] **Step 3: Rewrite `LivingScienceProfile.classify`**

In `src/detector/profile.py`, replace the body of `LivingScienceProfile.classify` (the whole method after the docstring/signature) with the version below. The key change: the `form_visible` branch is gone. Until the real reopened form is observed, closed-phrase-absent always routes to `OPENING_CANDIDATE`.

```python
    def classify(
        self,
        probes: Mapping[str, ProbeResult],
        anti_bot: AntiBotObservation,
    ) -> DetectionResult:
        probe = probes["home"]
        text_lower = _normalize_text(_html_text(probe.text))

        closed_visible = self._closed_phrase in text_lower

        facts: list[str] = [
            "Observed April 23, 2026 closed phrase location on the public page is directly monitorable in HTML.",
        ]
        signals: list[str] = []
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
        )
```

- [ ] **Step 4: Check that the old open-state test for livingscience is still appropriate**

Look at `tests/test_detector_profiles.py::test_livingscience_open_candidate_without_form`. That test asserts `OPENING_CANDIDATE` for a closed-phrase-absent page with no form, so it still passes. There is no existing livingscience-open test to update.

- [ ] **Step 5: Run full detector tests**

Run: `pytest tests/test_detector_profiles.py tests/test_detector_engine.py -v`

Expected: all PASS, including the new incidental-form test.

- [ ] **Step 6: Commit**

```bash
git add src/detector/profile.py tests/test_detector_profiles.py
git commit -m "fix(detector): livingscience never declares open from incidental forms"
```

---

## Task 4: LivingScience closed-phrase matching is whitespace and punctuation tolerant

**Why:** Today the closed phrase is matched by `self._closed_phrase in text_lower` against a single pre-normalized literal. If the site edits the phrase (comma moves, word order swap, adds a word, reformats), the detector silently drops to `OPENING_CANDIDATE` and spams alerts. A modest regex that requires both halves of the phrase (core tokens "wartelisten ... voll" AND "warteliste ... geöffnet") provides robustness without expanding scope.

**Files:**
- Modify: `src/detector/profile.py` — closed-phrase detection in `LivingScienceProfile`
- Modify: `tests/test_detector_profiles.py` — phrase variants

### Steps

- [ ] **Step 1: Write the failing test**

Append to `tests/test_detector_profiles.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_detector_profiles.py::test_livingscience_closed_state_tolerant_to_minor_phrase_edits -v`

Expected: FAIL. The literal `_closed_phrase` does not match the reworded version.

- [ ] **Step 3: Replace the exact match with a two-marker regex check**

In `src/detector/profile.py`, inside `LivingScienceProfile`, replace the class-level `_closed_phrase` attribute and its use in `classify` with two compiled regex patterns. Both must match for `CLOSED`:

```python
    _closed_phrase = _normalize_text(
        "Unsere Wartelisten sind derzeit voll. Vorübergehend können wir keine neuen Anmeldungen annehmen. "
        "Sobald die Warteliste wieder geöffnet ist, wird das Anmeldeformular wieder zur Verfügung stehen."
    )
    _closed_marker_full = re.compile(r"wartelisten?\b.{0,40}\bvoll\b", re.IGNORECASE | re.DOTALL)
    _closed_marker_reopen = re.compile(
        r"warteliste\b.{0,60}\bgeöffnet\b", re.IGNORECASE | re.DOTALL
    )
```

Then in `classify`, replace the `closed_visible = self._closed_phrase in text_lower` line with:

```python
        closed_visible_literal = self._closed_phrase in text_lower
        closed_visible_markers = bool(
            self._closed_marker_full.search(text_lower)
            and self._closed_marker_reopen.search(text_lower)
        )
        closed_visible = closed_visible_literal or closed_visible_markers
```

And in the `if closed_visible:` branch, add a `signals.append` so we can distinguish which rule fired:

```python
        if closed_visible:
            if closed_visible_literal:
                signals.extend(["closed_phrase_present", "html_monitorable"])
            else:
                signals.extend(["closed_phrase_markers_present", "html_monitorable"])
            # … unchanged remainder …
```

- [ ] **Step 4: Run full detector tests**

Run: `pytest tests/test_detector_profiles.py -v`

Expected: all PASS. The existing `test_livingscience_closed_state_detected` still passes (literal wins); the new tolerance test passes (markers win).

- [ ] **Step 5: Commit**

```bash
git add src/detector/profile.py tests/test_detector_profiles.py
git commit -m "feat(detector): livingscience closed-phrase tolerant to minor edits"
```

---

## Task 5: StudentVillage OPEN requires positive structural signals

**Why:** Today `OPEN` fires on *absence* of closed banners across three pages. A site redesign that moves the banner to a different DOM node, a cookie-consent overlay that hides the text from `get_text`, or a partial maintenance page could all satisfy this. Require that the apply page still exposes the register-form structure we know about — that is a positive signal and cheap to check.

**Files:**
- Modify: `src/detector/profile.py` — `StudentVillageProfile.classify`
- Modify: `tests/test_detector_profiles.py` — new test; update the existing OPEN test to include the form markers

### Steps

- [ ] **Step 1: Write the failing test**

Append to `tests/test_detector_profiles.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_detector_profiles.py::test_studentvillage_open_requires_register_form_present_on_apply_page -v`

Expected: FAIL. The current profile returns `DetectorState.OPEN` because none of the closed banners match.

- [ ] **Step 3: Update `StudentVillageProfile.classify` to require positive open signals**

In `src/detector/profile.py`, locate the OPEN branch:

```python
        if not apply_closed and not home_closed and not contact_closed:
```

Replace the entire `if not apply_closed and not home_closed and not contact_closed:` block with the version below. Key change: `register_form_present` must be true to fire `OPEN`; otherwise fall through to `OPENING_CANDIDATE` with `register_form_missing` in signals.

```python
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
            )
```

- [ ] **Step 4: Re-verify existing OPEN test still passes**

The existing `test_studentvillage_open_requires_banner_removal_across_pages` already includes `<form id="register_form">` and `<input type="hidden" name="form_token" value="abc123">`, so it still satisfies the new positive-signal requirement.

- [ ] **Step 5: Run all detector tests**

Run: `pytest tests/test_detector_profiles.py tests/test_detector_engine.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/detector/profile.py tests/test_detector_profiles.py
git commit -m "fix(detector): studentvillage open requires register form on apply page"
```

---

## Task 6: Confirmation policy adds a minimum-time-between-observations floor

**Why:** Current rule: if two consecutive cycles produce `OPEN` with the same fingerprint, confirm. There is no wall-clock minimum between those two cycles. A short poll interval (e.g. 15s) combined with cached upstream responses can satisfy this in seconds. Requiring a minimum gap (default 60s) makes the confirmation meaningful without changing the positive-case behavior for ordinary polling.

Also: expose `min_open_signal_strength` as config so we can tune the fast-path threshold (currently hardcoded 0.95) without touching code.

**Files:**
- Modify: `src/config/models.py` — add `confirmation_min_gap_seconds` and `open_signal_fast_path_strength` to `AppConfig`
- Modify: `src/config/settings.py` — read env vars
- Modify: `.env.example` — document them
- Modify: `src/orchestrator/service.py` — `_apply_confirmation_policy` uses them
- Create: `tests/test_orchestrator_confirmation.py`

### Steps

- [ ] **Step 1: Read the current `AppConfig` and `_apply_confirmation_policy`**

Open `src/config/models.py` and `src/orchestrator/service.py:213-252` so you know the fields that already exist and the exact structure of the policy method.

- [ ] **Step 2: Write the failing test**

Create `tests/test_orchestrator_confirmation.py`:

```python
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from src.config.models import (
    AppConfig,
    BrowserSettings,
    NotificationSettings,
    SiteMonitorConfig,
    SubmissionMode,
)
from src.detector.models import (
    AntiBotObservation,
    AntiBotSeverity,
    DetectionExecution,
    DetectionResult,
    DetectorState,
)
from src.orchestrator.service import DormAlertService
from src.persistence.sqlite_store import SiteRuntimeRecord


def _config(tmp_path: Path) -> AppConfig:
    site = SiteMonitorConfig(
        site_id="studentvillage",
        enabled=True,
        poll_interval_seconds=15,
        jitter_seconds=0.0,
        timeout_seconds=5,
        max_retries=0,
        submission_mode=SubmissionMode.DRY_RUN,
    )
    return AppConfig(
        database_path=tmp_path / "db.sqlite",
        artifacts_dir=tmp_path / "artifacts",
        log_dir=tmp_path / "logs",
        log_level="INFO",
        user_agent="test",
        detector_only=True,
        notification=NotificationSettings(
            enable_console=False,
            webhook_url=None,
            webhook_timeout_seconds=10,
            email_enabled=False,
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_starttls=True,
            email_from=None,
            email_to=(),
            alert_reminder_minutes=15,
        ),
        browser=BrowserSettings(headless=True, slow_mo_ms=0),
        failure_alert_threshold=3,
        closed_artifact_retention_days=7,
        sites={"studentvillage": site},
        studentvillage_applicant=None,
        studentvillage_success_phrases=(),
        studentvillage_failure_phrases=(),
        confirmation_min_gap_seconds=60,
        open_signal_fast_path_strength=0.95,
    )


def _open_result(fingerprint: str, open_strength: float, timestamp: str) -> DetectionResult:
    return DetectionResult(
        site_id="studentvillage",
        display_name="Student Village",
        state=DetectorState.OPEN,
        confidence=0.9,
        state_reason="closed_banners_removed_and_register_form_present",
        signal_scores={"closed_marker_strength": 0.0, "open_marker_strength": open_strength, "drift_risk": 0.15},
        state_version="test",
        signals=("closed_banners_removed",),
        facts=(), inferences=(), uncertainties=(),
        anti_bot=AntiBotObservation(AntiBotSeverity.NONE),
        page_urls=(),
        timestamp_utc=timestamp,
        fingerprint=fingerprint,
    )


def _runtime(fingerprint: str, transition_at: str) -> SiteRuntimeRecord:
    return SiteRuntimeRecord(
        site_id="studentvillage",
        display_name="Student Village",
        last_page_state=DetectorState.OPEN.value,
        last_workflow_state="open",
        last_confidence=0.9,
        last_fingerprint=fingerprint,
        last_checked_at=transition_at,
        consecutive_failures=0,
        last_transition_at=transition_at,
        updated_at=transition_at,
    )


def _service(tmp_path: Path) -> DormAlertService:
    config = _config(tmp_path)
    return DormAlertService(
        config=config,
        profiles={},
        detector=MagicMock(),
        store=MagicMock(),
        artifacts=MagicMock(),
        notifier=MagicMock(),
        verifier=MagicMock(),
    )


def test_confirmation_downgrades_if_consecutive_observations_are_too_close(tmp_path: Path) -> None:
    service = _service(tmp_path)
    execution = DetectionExecution(
        result=_open_result(fingerprint="fp1", open_strength=0.9, timestamp="2026-04-23T18:00:05Z"),
        probes=(),
    )
    runtime = _runtime(fingerprint="fp1", transition_at="2026-04-23T18:00:00Z")  # 5 seconds ago

    result = service._apply_confirmation_policy(execution, runtime).result

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert result.state_reason == "awaiting_consecutive_open_confirmation"


def test_confirmation_promotes_when_gap_is_wide_enough(tmp_path: Path) -> None:
    service = _service(tmp_path)
    execution = DetectionExecution(
        result=_open_result(fingerprint="fp1", open_strength=0.9, timestamp="2026-04-23T18:02:00Z"),
        probes=(),
    )
    runtime = _runtime(fingerprint="fp1", transition_at="2026-04-23T18:00:00Z")  # 120 seconds ago

    result = service._apply_confirmation_policy(execution, runtime).result

    assert result.state is DetectorState.OPEN
    assert result.state_reason == "consecutive_open_confirmation_satisfied"


def test_high_strength_open_still_fast_paths_without_gap(tmp_path: Path) -> None:
    service = _service(tmp_path)
    execution = DetectionExecution(
        result=_open_result(fingerprint="fp1", open_strength=0.99, timestamp="2026-04-23T18:00:05Z"),
        probes=(),
    )
    runtime = _runtime(fingerprint="fp1", transition_at="2026-04-23T18:00:00Z")

    result = service._apply_confirmation_policy(execution, runtime).result

    assert result.state is DetectorState.OPEN
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_orchestrator_confirmation.py -v`

Expected: FAIL. `AppConfig` has no `confirmation_min_gap_seconds` / `open_signal_fast_path_strength` fields yet.

- [ ] **Step 4: Add the fields to `AppConfig`**

Open `src/config/models.py` and find the `AppConfig` dataclass. Add two fields at the end (before the closing of the dataclass definition):

```python
    confirmation_min_gap_seconds: int = 60
    open_signal_fast_path_strength: float = 0.95
```

- [ ] **Step 5: Wire the env vars in `src/config/settings.py`**

In `load_settings()`, inside the `return AppConfig(...)` call, add:

```python
        confirmation_min_gap_seconds=get_int("DORMALERT_CONFIRMATION_MIN_GAP_SECONDS", 60),
        open_signal_fast_path_strength=get_float("DORMALERT_OPEN_SIGNAL_FAST_PATH_STRENGTH", 0.95),
```

(Use `get_float` — import it at the top if not already: `from src.utils.env import get_bool, get_csv, get_float, get_int, load_dotenv` — this import is already present.)

- [ ] **Step 6: Document in `.env.example`**

Append to `.env.example` near the other `DORMALERT_*` settings:

```
DORMALERT_CONFIRMATION_MIN_GAP_SECONDS=60
DORMALERT_OPEN_SIGNAL_FAST_PATH_STRENGTH=0.95
```

- [ ] **Step 7: Update `_apply_confirmation_policy` in `src/orchestrator/service.py`**

Replace the body of `_apply_confirmation_policy` with:

```python
    def _apply_confirmation_policy(
        self,
        execution: DetectionExecution,
        runtime,
    ) -> DetectionExecution:
        result = execution.result
        if result.state is not DetectorState.OPEN:
            return execution

        open_strength = result.signal_scores.get("open_marker_strength", 0.0)
        if open_strength >= self.config.open_signal_fast_path_strength:
            return execution

        same_fingerprint = runtime is not None and runtime.last_fingerprint == result.fingerprint
        prior_positive = runtime is not None and runtime.last_page_state in {
            DetectorState.OPEN.value,
            DetectorState.OPENING_CANDIDATE.value,
        }
        gap_satisfied = False
        if runtime is not None and runtime.last_transition_at is not None:
            gap_seconds = (
                parse_utc_iso(result.timestamp_utc) - parse_utc_iso(runtime.last_transition_at)
            ).total_seconds()
            gap_satisfied = gap_seconds >= self.config.confirmation_min_gap_seconds

        if same_fingerprint and prior_positive and gap_satisfied:
            confirmed = replace(
                result,
                state=DetectorState.OPEN,
                confidence=max(result.confidence, 0.94),
                state_reason="consecutive_open_confirmation_satisfied",
                inferences=result.inferences
                + ("A second consecutive matching positive detection confirmed the open state.",),
            )
            return DetectionExecution(result=confirmed, probes=execution.probes)

        downgraded = replace(
            result,
            state=DetectorState.OPENING_CANDIDATE,
            confidence=min(result.confidence, 0.78),
            state_reason="awaiting_consecutive_open_confirmation",
            signal_scores={**result.signal_scores, "confirmation_strength": 0.5},
            inferences=result.inferences
            + ("A second consecutive matching positive detection is required before declaring open.",),
        )
        return DetectionExecution(result=downgraded, probes=execution.probes)
```

No other changes to `service.py` are required — `parse_utc_iso` is already imported at the top of the file.

- [ ] **Step 8: Run the new tests**

Run: `pytest tests/test_orchestrator_confirmation.py -v`

Expected: all 3 PASS.

- [ ] **Step 9: Run the full suite**

Run: `pytest`

Expected: all tests PASS. The existing `test_orchestrator.py` tests should be unaffected because they don't exercise the confirmation gate directly; if any break, the most likely cause is an `AppConfig` construction missing the two new fields — add them with their defaults.

- [ ] **Step 10: Commit**

```bash
git add src/config/models.py src/config/settings.py src/orchestrator/service.py tests/test_orchestrator_confirmation.py .env.example
git commit -m "feat(orchestrator): confirmation policy enforces minimum observation gap"
```

---

## Task 7: False-positive battery in detector profile tests

**Why:** The existing profile tests are happy-path. Add an explicit suite of pages that historically *look* like opens but aren't, so regressions show up loudly.

**Files:**
- Modify: `tests/test_detector_profiles.py`

### Steps

- [ ] **Step 1: Write the new tests**

Append to `tests/test_detector_profiles.py`:

```python
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
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_detector_profiles.py -v`

Expected: all PASS immediately because the hardening from Tasks 3 and 5 already covers these cases. If any FAIL, go back to Tasks 3/5 and fix the gap.

- [ ] **Step 3: Commit**

```bash
git add tests/test_detector_profiles.py
git commit -m "test(detector): false-positive battery for cookie banners and mismatched forms"
```

---

## Task 8: Bump `state_version` and update detection strategy docs

**Why:** The semantic fingerprint and classification rules changed. Bumping `state_version` invalidates cached per-site runtime fingerprints so the orchestrator treats the next cycle as a transition and captures fresh evidence. Documenting the new rules in `docs/detection_strategy.md` keeps operators aligned with code.

**Files:**
- Modify: `src/detector/profile.py` — `SiteProfile.state_version`
- Modify: `docs/detection_strategy.md` — append a "2026-04-23 reliability upgrade" section

### Steps

- [ ] **Step 1: Bump the version string**

In `src/detector/profile.py`, change:

```python
    state_version = "2026-04-24.v2"
```

to:

```python
    state_version = "2026-04-23.reliability-v1"
```

- [ ] **Step 2: Document the change**

Append to `docs/detection_strategy.md`:

```markdown
## 2026-04-23 Reliability upgrade

The detector was hardened against the following failure modes. Each rule has a
test in `tests/test_detector_profiles.py` or `tests/test_detector_engine.py`.

- Responses that are not HTTP 2xx, not `text/html`, or shorter than
  `MIN_PLAUSIBLE_BODY_CHARS` (500) are classified as `FAILED` with the signal
  `implausible_response`. Classification never sees them.
- Fingerprints strip `<script>`, `<style>`, `<noscript>`, HTML comments, and
  `value="…"` on hidden inputs before hashing. This keeps the orchestrator's
  consecutive-confirmation gate useful even when pages contain rotating tokens.
- `livingscience` no longer declares `OPEN` from incidental `<form>` elements
  on the public page. Until the real reopened application form is observed
  in production, absence of the closed phrase routes to `OPENING_CANDIDATE`
  with `state_reason=closed_phrase_absent_pending_operator_verification`.
- `livingscience` closed-phrase detection matches either the exact literal or
  both of two tolerant markers (`wartelisten … voll` and `warteliste … geöffnet`),
  so minor copy edits do not flap the state.
- `studentvillage` requires a positive structural signal (the `register_form`
  element with its hidden `form_token`) in addition to banner absence before
  declaring `OPEN`. Otherwise it holds at `OPENING_CANDIDATE` with
  `register_form_missing`.
- The orchestrator's confirmation policy requires the two consecutive matching
  `OPEN` observations to be at least `DORMALERT_CONFIRMATION_MIN_GAP_SECONDS`
  (default 60s) apart. The fast-path strength threshold is
  `DORMALERT_OPEN_SIGNAL_FAST_PATH_STRENGTH` (default 0.95).

`state_version` was bumped to `2026-04-23.reliability-v1` to invalidate
fingerprints persisted under the old rules.
```

- [ ] **Step 3: Run the full suite once more**

Run: `pytest`

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/detector/profile.py docs/detection_strategy.md
git commit -m "docs(detector): bump state_version and record reliability changes"
```

---

## Verification checklist (post-implementation)

After all 8 tasks, confirm:

- `pytest` → all green.
- `pytest --cov=src --cov-report=term-missing` → detector modules ≥ 80% coverage.
- `python3 -m src.main detect-once --site studentvillage --detector-only` against the live pages still returns `closed` (since both sites are, as of reconnaissance, closed). The JSON output should now include `state_version: "2026-04-23.reliability-v1"`.
- `python3 -m src.main detect-once --site livingscience --detector-only` returns `closed`.
- With `DORMALERT_LOG_LEVEL=DEBUG`, running `python3 -m src.main run --detector-only` for one cycle logs one `probe_completed` per target and one `detection_cycle_complete` per site with the new state reasons.
- `grep -R "state_version" src tests docs` shows only the new version string.

If any of the above fails, return to the task that owns the relevant file and fix it before declaring done.
