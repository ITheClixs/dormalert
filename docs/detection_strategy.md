# Detection Strategy

## Goals

The detector must:

1. identify real availability changes quickly
2. avoid false positives
3. preserve evidence
4. remain cheap enough to run continuously

## Detector design

The system uses site profiles.

Each profile defines:

- monitored URLs
- expected closed-state phrases
- stronger open-state indicators
- fallback candidate logic
- confidence scoring

## Site-specific strategy

### livingscience

Primary URL:

- `https://livingscience.ch/wohnen-studieren-zuerich/?L=0`

Observed closed-state marker:

- `Unsere Wartelisten sind derzeit voll.`

Current strategy:

- fetch the page by HTTP
- normalize HTML text
- classify as `closed` if the known phrase is present
- classify as `opening_candidate` if the phrase disappears without strong form markers
- classify as `open` only if the phrase disappears and a visible actionable form marker appears

Why this works:

- the current state is server-rendered and explicit
- no browser is needed for routine polling

### studentvillage

Monitored URLs:

- home: `https://studentvillage.ch/en/`
- apply: `https://studentvillage.ch/en/apply/`
- contact: `https://studentvillage.ch/en/contact/`

Observed closed-state markers:

- home: `All rooms are currently occupied`
- apply: `Currently all rooms are rented. We do not have a waiting list.`
- contact: `There are currently no rooms available and we do not have a waiting list.`

Observed always-present form markers:

- `register_form`
- `form_token`
- `regformhash(...)`

Current strategy:

- fetch all three pages
- inspect the apply page for form availability and token presence
- inspect all pages for closed-state banners
- classify:
  - `closed` when the banners still align
  - `opening_candidate` when the apply page changes before the others or the site becomes inconsistent
  - `open` when the closed-state language disappears from the monitored pages while the application path remains available

Why this works:

- the form is already present during the closed state
- therefore open/closed must be inferred from the banners, not from form existence

## Anti-bot-aware detection

Every probe is also inspected for:

- `g-recaptcha`
- `hcaptcha`
- `turnstile`
- `cf-chl`, `__cf_bm`, or Cloudflare challenge text
- generic CAPTCHA/challenge text
- tokenized form inputs
- session cookies

Policy:

- `info`: token/session observations
- `warning`: challenge-like or suspicious markers
- `blocking`: visible CAPTCHA or explicit challenge page

Blocking signals do not trigger bypass logic. They only change the orchestration policy.

## Polling policy

Defaults:

- `livingscience`: `300s + jitter`
- `studentvillage`: `180s + jitter`

Jitter:

- bounded positive jitter to avoid robotic cadence

Retries:

- bounded retry count per cycle
- no aggressive escalation of load during failures

## Evidence capture policy

The detector captures full evidence bundles when:

- page state changes
- anti-bot severity is `warning` or `blocking`
- detector state is `open`, `opening_candidate`, or `failed`

Each bundle includes:

- HTML per target
- response headers per target
- detection summary JSON

## 2026-04-23 Reliability upgrade

The detector was hardened against the following failure modes. Each rule has a
test in `tests/test_detector_profiles.py`, `tests/test_detector_engine.py`, or
`tests/test_orchestrator_confirmation.py`.

- Responses that are not HTTP 2xx, not `text/html`, or shorter than
  `MIN_PLAUSIBLE_BODY_CHARS` (500) are classified as `FAILED` with the signal
  `implausible_response`. Classification never sees them.
- Fingerprints strip `<script>`, `<style>`, `<noscript>`, and HTML comments
  before hashing, and seed the digest with `state_version`. This keeps the
  orchestrator's consecutive-confirmation gate useful even when pages contain
  rotating tokens, and ensures `state_version` bumps invalidate persisted
  fingerprints.
- `livingscience` no longer declares `OPEN` from incidental `<form>` elements
  on the public page. Until the real reopened application form is observed
  in production, absence of the closed phrase routes to `OPENING_CANDIDATE`
  with `state_reason=closed_phrase_absent_pending_operator_verification`.
- `livingscience` closed-phrase detection matches either the exact literal or
  both of two tolerant markers (`wartelisten … voll` and `warteliste … geöffnet`),
  so minor copy edits do not flap the state. The marker branch emits the
  signal `closed_phrase_markers_present`.
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
