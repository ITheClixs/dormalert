# DormAlert

DormAlert is a production-oriented dormitory opening monitor built around one core subsystem: a page state detector.

The goal is not blind automation. The goal is to detect the exact moment a dormitory waitlist or application path becomes available again, preserve evidence, alert immediately, and only attempt submission when the site behavior is well understood and the risk is acceptable.

This repository is intentionally structured as a long-running monitoring system rather than a one-off script. Detection, orchestration, persistence, diagnostics, notification, submission, and verification are separated so the system can be operated safely on an always-on host.

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

## Operating modes

- `disabled`: no submission attempt.
- `dry_run`: the submitter prepares the flow, validates config, and captures evidence without sending the final request.
- `live`: the submitter can perform the real browser workflow if the site is open and no blocking anti-bot signal is present.

The current default in `.env.example` is:

- `livingscience`: `disabled`
- `studentvillage`: `dry_run`

## Documentation index

- [Technical reconnaissance](docs/reconnaissance.md)
- [Architecture](docs/architecture.md)
- [Detection strategy](docs/detection_strategy.md)
- [Submission strategy](docs/submission_strategy.md)
- [Operations](docs/operations.md)
- [Deployment](docs/deployment.md)
- [Risk register](docs/risk_register.md)
- [Implementation plan](docs/implementation_plan.md)

