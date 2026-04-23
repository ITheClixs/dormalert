# Implementation Plan

## Phase 1: site inspection and live reconnaissance

Objective:

- establish ground truth for both sites before designing automation

Concrete tasks:

- inspect rendered content
- inspect raw HTML and headers
- inspect forms, hidden inputs, and session markers
- inspect obvious anti-bot indicators

Files/modules:

- `docs/reconnaissance.md`

Risks:

- hidden controls may only appear later

Exit criteria:

- observed facts are documented
- uncertainties are explicit
- feasibility statement is clear

## Phase 2: page state detector prototype

Objective:

- implement the core subsystem first

Concrete tasks:

- define detector models
- implement HTTP probe client
- implement anti-bot observation
- implement site profiles and scoring

Files/modules:

- `src/detector/models.py`
- `src/detector/http_client.py`
- `src/detector/antibot.py`
- `src/detector/profile.py`
- `src/detector/engine.py`

Risks:

- false positives on `studentvillage`

Exit criteria:

- detector returns structured results
- core rules are covered by tests

## Phase 3: notification pipeline

Objective:

- emit useful alerts from real state transitions

Concrete tasks:

- define notification event model
- implement console notifier
- implement generic webhook notifier
- add dedupe for repeat alerts

Files/modules:

- `src/notifier/base.py`
- `src/notifier/stdout.py`
- `src/notifier/webhook.py`
- `src/notifier/registry.py`

Risks:

- noisy alerts if dedupe is weak

Exit criteria:

- open and failure events can be delivered without duplication

## Phase 4: artifact capture and diagnostics

Objective:

- preserve enough evidence to debug transitions and failures

Concrete tasks:

- create artifact bundles
- store HTML, headers, screenshots, metadata
- connect artifact capture to orchestrator decisions

Files/modules:

- `src/diagnostics/artifacts.py`

Risks:

- storing too little evidence for debugging

Exit criteria:

- every meaningful transition produces usable evidence

## Phase 5: submission path abstraction

Objective:

- keep detection and submission cleanly separated

Concrete tasks:

- define submitter result model and interface
- implement `disabled` and `dry_run` behavior
- add per-site submitter registry

Files/modules:

- `src/submitter/base.py`
- `src/submitter/dry_run.py`
- `src/submitter/registry.py`

Risks:

- coupling submit logic into detector decisions

Exit criteria:

- submission can be enabled per site without changing detector code

## Phase 6: real submission automation

Objective:

- implement a real but staged browser submitter for the supported site

Concrete tasks:

- map current `studentvillage` fields
- preserve session/token state
- submit through the page’s own JS flow
- capture screenshots and final HTML

Files/modules:

- `src/submitter/studentvillage.py`

Risks:

- selector drift
- unknown post-submit behavior
- anti-bot measures on submit

Exit criteria:

- dry-run flow works end to end
- live mode is guarded and isolated

## Phase 7: verification and duplicate prevention

Objective:

- prevent repeated actions and classify outcomes safely

Concrete tasks:

- add SQLite persistence
- store runtime state, detections, dedupe keys, submission attempts
- implement configurable verification rules

Files/modules:

- `src/persistence/sqlite_store.py`
- `src/verifier/base.py`
- `src/verifier/rules.py`

Risks:

- ambiguous submission results

Exit criteria:

- duplicate alerts/submits are prevented
- verification can return confirmed, ambiguous, or failed

## Phase 8: deployment and operational hardening

Objective:

- make the system suitable for always-on use

Concrete tasks:

- build CLI entrypoint
- implement scheduler loop
- add structured logging
- write deployment and operations docs
- add systemd example

Files/modules:

- `src/main.py`
- `src/app/runner.py`
- `src/utils/logging.py`
- `docs/operations.md`
- `docs/deployment.md`
- `scripts/dormalert.service.example`

Risks:

- weak observability in production

Exit criteria:

- service can run unattended
- logs and artifacts support investigation

