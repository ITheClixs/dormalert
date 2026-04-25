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

## Operating modes

- `disabled`: no submission attempt.
- `dry_run`: the submitter prepares the flow, validates config, and captures evidence without sending the final request.
- `live`: the submitter can perform the real browser workflow if the site is open and no blocking anti-bot signal is present.

The current default in `.env.example` is:

- `livingscience`: `disabled`
- `studentvillage`: `dry_run`

## Email Alerts

The system now supports SMTP email alerts for opening events.

Set these values in `.env` to enable email delivery:

```bash
DORMALERT_EMAIL_ENABLED=true
DORMALERT_SMTP_HOST=smtp.example.com
DORMALERT_SMTP_PORT=587
DORMALERT_SMTP_USERNAME=your-user
DORMALERT_SMTP_PASSWORD=your-password
DORMALERT_SMTP_STARTTLS=true
DORMALERT_EMAIL_FROM=alerts@example.com
DORMALERT_EMAIL_TO=demirguven178@gmail.com
DORMALERT_ALERT_REMINDER_MINUTES=15
```

Behavior:

- one email is sent immediately when a site is confirmed `open`
- reminder emails repeat at the configured interval while the opening remains active
- reminders stop when the site closes or the opening event is acknowledged
- opening-candidate and detector-warning events are not sent through email

Before relying on email alerts, send a real SMTP test and check the receiver inbox:

```bash
python3 -m src.main test-email
```

Inbox placement cannot be guaranteed by application code alone. Use an authenticated SMTP sender whose `DORMALERT_EMAIL_FROM` mailbox is authorized by the sending domain, with SPF, DKIM, and DMARC configured by the mail provider. The notifier uses plain-text transactional content and standard `Date`, `Message-ID`, `Reply-To`, and auto-generated mail headers to avoid common spam-filter triggers.

## Operational Commands

```bash
python3 -m src.main status
python3 -m src.main list-openings
python3 -m src.main list-openings --active-only
python3 -m src.main ack-opening --event-id 1
python3 -m src.main submit-once --site studentvillage --dry-run
python3 -m src.main test-email
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
