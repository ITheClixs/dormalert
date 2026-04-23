# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

DormAlert is a long-running Python monitor that detects when dormitory waitlist pages transition from *closed* to *open*, preserves evidence, alerts, and optionally submits. See `README.md` for the operator view and `AGENTS.md` for the engineering principles (detection-first, separation of concerns, no anti-bot bypass).

## Commands

Dev environment lives in `.venv/`. All commands run from the repo root.

```bash
# install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium    # only needed for studentvillage live/dry_run

# run
python3 -m src.main detect-once [--site studentvillage] [--detector-only]
python3 -m src.main run [--site studentvillage] [--detector-only]
python3 -m src.main submit-once --site studentvillage --dry-run
python3 -m src.main list-openings [--active-only]
python3 -m src.main ack-opening --event-id <N>
python3 -m src.main status

# tests
pytest                                    # full suite (pyproject sets pythonpath="." and testpaths=["tests"])
pytest tests/test_orchestrator.py         # single file
pytest tests/test_orchestrator.py::test_name -v
pytest --cov=src --cov-report=term-missing
```

There is no lint/format config committed — follow standard ruff/black/mypy defaults if you add one. The project requires Python **3.11+** (uses PEP 604 `X | None` types and `from __future__ import annotations` throughout).

## Architecture

### Two state machines, not one

This is the most important thing to understand before editing the orchestrator.

- **`DetectorState`** (`src/detector/models.py`): what the page *looks like* right now — `closed | opening_candidate | open | failed`. Pure output of the detector.
- **`WorkflowState`** (same file): where the *system* is in its reaction lifecycle — `closed | opening_candidate | open | submitted | verified | failed`. Derived in `DormAlertService._derive_workflow_state`; persists across cycles so a `submitted`/`verified` site stays there even while the detector keeps re-observing `open` on the same fingerprint.

Never conflate them. The detector never emits `submitted` or `verified`; the orchestrator never overrides detector output directly — it transitions workflow state based on detector output *plus* prior runtime record.

### Detection pipeline (`src/detector/`)

`HttpProbeClient` → per-site `SiteProfile.classify(probes, anti_bot)` → `AntiBotObservation` merge → `_apply_confirmation_policy` (in orchestrator) → artifact capture → persistence.

Site profiles live in `src/detector/profile.py` and bump `state_version` (currently `"2026-04-24.v2"`) whenever classification rules change — this invalidates cached fingerprints.

The detector is HTTP-first (httpx + BeautifulSoup). Playwright is reserved for the submitter only. Do not add Playwright to the detection path.

### Confirmation policy (non-obvious)

`DormAlertService._apply_confirmation_policy` gates every `DetectorState.OPEN` before it's trusted:

- If `signal_scores["open_marker_strength"] >= 0.95` → accept immediately.
- Else, require a **second consecutive detection with the same fingerprint** while the prior state was already `open`/`opening_candidate`. First hit gets downgraded to `opening_candidate` with `state_reason="awaiting_consecutive_open_confirmation"`.

This is the main defense against false-positive "open" spikes. If you add a new positive signal, decide whether it should fast-path to 0.95 or rely on consecutive confirmation.

### Opening event lifecycle (`_reconcile_opening_event`)

An **OpeningEvent** is a persisted record separate from runtime state. One active event per site at a time. Created on first `OPEN` detection, refreshed while the fingerprint holds, closed on `CLOSED` or when a new fingerprint replaces it. Reminder cadence is `notification.alert_reminder_minutes` (default 15). Acknowledgement (`ack-opening`) stops reminders without closing.

**Important:** when `email_enabled=true`, `_opening_delivery_succeeded` requires an *email* delivery specifically to succeed before marking the event notified. Console/webhook success alone is insufficient. Don't "fix" this to accept any channel — email is the designated durable channel for opens.

### Submission safety gates

`_handle_submission` enforces, in order:
1. `detector_only` global flag OR per-site `SubmissionMode.DISABLED` → notify `manual_action_required`, stop.
2. Live mode + anti-bot `warning` severity → notify `manual_action_required`, stop.
3. Anti-bot `blocking` severity → `SubmissionStatus.BLOCKED`, never attempts.
4. Per-fingerprint dedupe via `store.action_exists("submit:{site}:{fingerprint}")`.

`load_settings()` additionally hard-rejects `LIVINGSCIENCE_SUBMISSION_MODE=live` — livingscience has no mapped form yet. Don't remove this check; re-enable it by first mapping selectors in `src/submitter/` and `docs/submission_strategy.md`.

### Notification dedupe

`_notify_once` computes a SHA-256 dedupe key from `event_type|site|title|message` (or takes an explicit `dedupe_key`) and stores it via `store.remember_action`. The same logical event won't re-send on subsequent cycles. When adding new notification types, pass a `dedupe_key` that includes the `fingerprint` or a state-specific suffix — otherwise transient classification flaps will spam.

### Scheduling (`src/app/runner.py`)

`ContinuousRunner` maintains per-site `next_run` timestamps, sleeps 1s between checks, and logs `scheduler_wait` every `WAIT_LOG_INTERVAL_SECONDS` (15s) during idle so operators see the monitor is alive. Pruning of closed-state artifacts runs hourly. Jitter is *added* to `poll_interval_seconds` (`uniform(0, jitter_seconds)`) — never subtract, never zero the floor, keep traffic bounded-but-not-periodic.

### Persistence (`src/persistence/sqlite_store.py`)

Single SQLite file at `DORMALERT_DATABASE_PATH` (default `./artifacts/dormalert.db`). Tables: `site_runtime`, detection history, `opening_events`, submission attempts, `actions` (dedupe markers). Schema is created idempotently in `initialize()`. Crash recovery = restart the process; runtime state and dedupe markers survive.

### Configuration (`src/config/settings.py`)

Everything comes from env + `.env` (loaded by the hand-rolled `load_dotenv` in `src/utils/env.py` — do not add `python-dotenv` as a dep). `AppConfig` is a frozen dataclass; use `dataclasses.replace` to override (see `_build_service` using `replace(config, detector_only=True)` for the `--detector-only` CLI flag).

Validation happens at load time: live mode for studentvillage requires a fully populated `StudentVillageApplicant`; missing required fields raise `ValueError` at startup. Preserve this fail-loud behavior — don't defer config errors to runtime.

## Conventions

- `from __future__ import annotations` at the top of every module.
- Dataclasses are `frozen=True` where feasible; mutate by constructing new instances (see heavy use of `dataclasses.replace` in orchestrator and engine).
- Timestamps are UTC ISO-8601 strings produced by `src/utils/time.utcnow_iso()`; parse with `parse_utc_iso`. Don't sprinkle `datetime.utcnow()` around.
- Logging uses structured `extra={"event": "...", ...}` fields. Keep `event` names stable — they're how operators filter `logs/app.log`.
- Artifacts (HTML snapshots, screenshots, detection JSON) are written via `ArtifactManager`; never write to `artifacts/` directly.
- Never hardcode selectors or URLs outside `src/detector/profile.py` / `src/submitter/studentvillage.py`.
