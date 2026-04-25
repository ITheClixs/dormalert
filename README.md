# DormAlert: Find a student dorm in zurich in only 4 months!!!

DormAlert is a production-oriented dormitory opening monitor built around one core subsystem: a page state detector.

The goal is not blind automation. The goal is to detect the exact moment a dormitory waitlist or application path becomes available again, preserve evidence, alert immediately, and only attempt submission when the site behavior is well understood and the risk is acceptable.

This repository is intentionally structured as a long-running monitoring system rather than a one-off script. Detection, orchestration, persistence, diagnostics, notification, submission, and verification are separated so the system can be operated safely on an always-on host.

## Abstract

As you could already tell, finding a dorm room in Hoenggerberg, Zurich in your freshman years (Basisjahr) can be really challenging because there are only limited spots available and ETH does not admit a specific number of people every year. Thus the people that applied earlier will get in front of others in the waitlist that apply later. So in this repo I aimed for addressing this issue and make sure we get in front of others by applying extremely fast and by means of this applying instantly after the waitlist opens for the upcoming semester (HS 2026). And yes this repo is going to be gatekept for a while (that while is presumably defined as up until the moment we get a place to live in hoenggerg lol) so don't be mad.



## Scope

The initial implementation targets these public pages:

1. `https://livingscience.ch/wohnen-studieren-zuerich/?L=0`
2. `https://studentvillage.ch/en/`

The current reconnaissance, captured on April 23, 2026, is documented in [docs/reconnaissance.md](docs/reconnaissance.md).

## Recommended rollout

1. Run detector-only mode first.
2. Validate state transitions, notifications, and artifact capture.
3. Enable `dry_run` submission for `studentvillage` to validate selector mapping and browser flow without sending a real application.
4. Enable live submission only after reviewing captured artifacts from dry runs and confirming the post-submit success criteria.

`livingscience` currently has no visible public form, so the shipped implementation treats it as alert-first and submission-disabled until the real form reappears and selectors can be mapped.

## Repository structure

```text
.
├── README.md
├── docs/
│   ├── architecture.md
│   ├── deployment.md
│   ├── detection_strategy.md
│   ├── implementation_plan.md
│   ├── operations.md
│   ├── reconnaissance.md
│   ├── risk_register.md
│   └── submission_strategy.md
├── src/
│   ├── main.py
│   ├── app/
│   ├── config/
│   ├── detector/
│   ├── diagnostics/
│   ├── notifier/
│   ├── orchestrator/
│   ├── persistence/
│   ├── submitter/
│   ├── utils/
│   └── verifier/
├── tests/
├── scripts/
├── artifacts/
├── logs/
├── requirements.txt
├── pyproject.toml
└── .env.example
```

The structure reflects the operating model:

- `detector/` owns page state detection.
- `orchestrator/` owns scheduling and action decisions.
- `submitter/` owns staged submission automation.
- `verifier/` owns post-submit confirmation.
- `persistence/` owns durable state and dedupe.
- `diagnostics/` owns evidence capture.
- `notifier/` owns outbound alerts.

## Quick start

1. Create a virtual environment.
2. Install dependencies.
3. Copy `.env.example` to `.env`.
4. Run detector-only mode first.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
python3 -m src.main detect-once
python3 -m src.main run --detector-only
```

## Console output

When running continuously (`run`), terminal output is human-readable and concise by default, for example:

- probe completion and retry lines
- per-site detection summaries (`state`, `confidence`, `reason`)
- heartbeat summaries (active openings and failure counts)

Structured JSON logs are still preserved in `logs/app.log` for machine parsing and diagnostics.

If you want frequent checks (for example every 15 seconds), configure both interval and jitter:

```bash
LIVINGSCIENCE_POLL_INTERVAL_SECONDS=15
LIVINGSCIENCE_JITTER_SECONDS=0
STUDENTVILLAGE_POLL_INTERVAL_SECONDS=15
STUDENTVILLAGE_JITTER_SECONDS=0
```

With longer intervals, the runner now prints periodic scheduler lines so it is obvious the monitor is still alive and waiting for the next due checks.

## Detection Mechanism

DormAlert is built around a conservative page-state detector rather than a simple keyword alert. Each monitored site has a profile in `src/detector/profile.py` that defines the URLs to probe, the closed-state markers, the positive open-state markers, and the fallback behavior when the page changes in an ambiguous way.

Every detection cycle produces a structured `DetectionResult` with:

- `state`: `closed`, `opening_candidate`, `open`, or `failed`
- `confidence`: numeric confidence for downstream policy
- `state_reason`: a machine-readable explanation such as `strong_home_and_apply_closed_banners_present`
- `signals`: concrete observations like `apply_closed_banner_present`, `register_form_present`, or `closed_banners_removed`
- `facts`, `inferences`, and `uncertainties`: separated so observed page content is not mixed with assumptions
- `fingerprint`: a semantic hash used by the orchestrator to compare consecutive observations

The detector first validates the raw HTTP response before site-specific classification. A probe is rejected as `failed` if it is not HTTP 2xx, is not HTML, or has a body shorter than `MIN_PLAUSIBLE_BODY_CHARS` (`500`). This prevents maintenance pages, short error bodies, JSON responses, and proxy failures from being misread as an opening.

### Site Rules

`livingscience` is intentionally alert-first. The current public page has a strong closed-state phrase around the waitlist being full. The detector classifies it as `closed` when either the exact phrase or tolerant German markers (`wartelisten ... voll` and `warteliste ... geöffnet`) are present. If the phrase disappears, the profile does not assume the waitlist opened. It emits `opening_candidate` with `closed_phrase_absent_pending_operator_verification` until the real reopened form markup has been observed and mapped.

`studentvillage` is stricter because the apply page can expose a registration form even while the site is closed. Therefore the detector does not treat form presence alone as an opening. It probes three pages:

- home: `https://studentvillage.ch/en/`
- apply: `https://studentvillage.ch/en/apply/`
- contact: `https://studentvillage.ch/en/contact/`

The closed state is based on the known closed banners across those pages. A profile-level `open` classification requires all monitored closed banners to be gone and the apply page to still contain the expected structural markers: `id="register_form"` and `name="form_token"`. If banners disappear but the known register form/token structure is missing, the state stays `opening_candidate`, not `open`.

### False-Positive Controls

Several layers prevent false-positive alerts:

- `opening_candidate` is a quarantine state. It records evidence and can warn operators, but SMTP opening emails are only sent for confirmed `open` events and reminders.
- Semantic fingerprints strip `<script>`, `<style>`, `<noscript>`, and HTML comments before hashing, then seed the digest with `state_version`. This keeps rotating tokens, tracking scripts, and generated timestamps from looking like real state changes.
- The orchestrator confirmation policy downgrades most first `open` observations to `opening_candidate`. Unless `open_marker_strength` reaches `DORMALERT_OPEN_SIGNAL_FAST_PATH_STRENGTH` (`0.95` by default), the same fingerprint must be observed again after `DORMALERT_CONFIRMATION_MIN_GAP_SECONDS` (`60` by default) before the workflow becomes `open`.
- Student Village requires both negative evidence and positive structure: closed banners must be removed, and the known register form with `form_token` must still be present. Cookie banners, redesigned placeholder pages, or similar forms without the expected token remain `opening_candidate`.
- LivingScience cannot currently produce `open` from incidental page changes or unrelated forms. Closed phrase removal is treated as `opening_candidate` until the real application form is characterized.
- Anti-bot markers are inspected separately. Visible CAPTCHA, hCaptcha, Turnstile, Cloudflare challenge text, or blocking challenge markers force a `failed` result or manual handling instead of an opening alert.
- Meaningful transitions, candidates, opens, failures, and anti-bot warnings capture artifacts under `artifacts/` so later debugging can inspect the exact HTML, headers, and detection summary that drove the decision.

The main technical effect is that DormAlert prefers a missed early email over a false positive. A site must pass response plausibility, site-specific marker checks, and orchestration confirmation before a waitlist-opening email is emitted.

## Operating modes

- `disabled`: no submission attempt.
- `dry_run`: the submitter prepares the flow, validates config, and captures evidence without sending the final request.
- `live`: the submitter can perform the real browser workflow if the site is open and no blocking anti-bot signal is present.

The current default in `.env.example` is:

- `livingscience`: `disabled`
- `studentvillage`: `dry_run`

## Email Alerts

The system supports SMTP email alerts for startup checks, confirmed opening events, and opening reminders.

`DORMALERT_EMAIL_TO=demirguven178@gmail.com` only sets the receiver. DormAlert still needs an authenticated SMTP sender account because the monitor sends mail directly from the running process.

For Gmail SMTP, enable 2-step verification on the sender account, create a Google App Password, and use that app password here:

```bash
DORMALERT_EMAIL_ENABLED=true
DORMALERT_SMTP_HOST=smtp.gmail.com
DORMALERT_SMTP_PORT=587
DORMALERT_SMTP_USERNAME=your-sender@gmail.com
DORMALERT_SMTP_PASSWORD=your-google-app-password
DORMALERT_SMTP_STARTTLS=true
DORMALERT_EMAIL_FROM=your-sender@gmail.com
DORMALERT_EMAIL_TO=your-receiver@gmail.com
DORMALERT_ALERT_REMINDER_MINUTES=15
```

Behavior:

- one startup email is sent when `./.venv/bin/python -m src.main run --detector-only` begins
- one email is sent immediately when a site is confirmed `open`
- reminder emails repeat at the configured interval while the opening remains active
- reminders stop when the site closes or the opening event is acknowledged
- opening-candidate and detector-warning events are not sent through email

Before relying on email alerts, test the SMTP route and then test the monitor startup email:

```bash
./.venv/bin/python -m src.main test-email
./.venv/bin/python -m src.main run --detector-only
```

The `run --detector-only` command should send exactly one `DormAlert monitor is running` email when the process starts. If no email arrives, check `logs/app.log` for `startup_email_not_configured`, `notification_email_retry`, or `notifier_failed`.

Inbox placement cannot be guaranteed by application code alone. Use an authenticated SMTP sender whose `DORMALERT_EMAIL_FROM` mailbox is authorized by the sending domain, with SPF, DKIM, and DMARC configured by the mail provider. The notifier uses plain-text transactional content and standard `Date`, `Message-ID`, `Reply-To`, and auto-generated mail headers to avoid common spam-filter triggers.

To test the same code path used by a real Student Village opening alert, run:

```bash
./.venv/bin/python -m src.main simulate-opening --site studentvillage --send-email
```

This command does not touch the live Student Village site and does not write to the production SQLite database. It uses local fixture HTML, a temporary `/tmp/dormalert-sim-*` database/artifact directory, the real Student Village detector profile, and the normal orchestrator opening notification path. The simulated fixture is seeded as a prior `opening_candidate` older than the configured confirmation gap, then the normal detector run confirms it as `open` and sends a clearly marked `Student Village SIMULATION` opening email.

## Operational Commands

```bash
python3 -m src.main status
python3 -m src.main list-openings
python3 -m src.main list-openings --active-only
python3 -m src.main ack-opening --event-id 1
python3 -m src.main submit-once --site studentvillage --dry-run
python3 -m src.main test-email
python3 -m src.main simulate-opening --site studentvillage --send-email
```

## Documentation index

- [Technical reconnaissance](docs/reconnaissance.md)
- [Architecture](docs/architecture.md)
- [Detection strategy](docs/detection_strategy.md)
- [Submission strategy](docs/submission_strategy.md)
- [Operations](docs/operations.md)
- [Deployment](docs/deployment.md)
- [Risk register](docs/risk_register.md)
- [Implementation plan](docs/implementation_plan.md)
