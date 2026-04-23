AGENTS.md

Purpose

This repository contains a production oriented monitoring and reaction system for dormitory waitlist openings.

The system has one central goal:

Detect the exact moment a dormitory application flow becomes available again and react with minimal delay and high reliability.

The system is built around a page state detector. Everything else exists to support that detector, react to its findings, and safely attempt or prepare submission.

⸻

Core product objective

The system must detect transitions between at least these states:

1. Closed
    The waitlist or application flow is unavailable.
2. Opening candidate
    Some signals suggest the page may have changed, but confidence is not yet high enough.
3. Open
    The application form, registration path, or actionable submission flow is available.
4. Submitted
    A submission attempt has been made.
5. Verified
    Submission success has been confirmed.
6. Failed
    A detection or submission path failed and requires retry, escalation, or human attention.

The main engineering priority is not flashy automation. It is correctness, reliability, and operational resilience.

⸻

Non goals

This repository is not intended to:

1. Bypass CAPTCHAs or challenge systems.
2. Evade rate limits or anti abuse systems.
3. Hammer target sites with aggressive polling.
4. Depend on fragile one off scripts without observability.
5. Mix detection logic and submission logic into one monolithic file.
6. Assume that full autonomous submission is always the first deployment stage.

If a target site introduces anti bot controls, challenge pages, or human verification, the system should surface that clearly and fall back to alerting or human assisted handling where appropriate.

⸻

Engineering principles

All agents working in this repository must follow these principles:

1. Detection first
    The page state detector is the foundation of the system.
2. Separation of concerns
    Detection, orchestration, submission, verification, persistence, notifications, and diagnostics must remain clearly separated.
3. Production mindset
    Prefer maintainability, testability, and observability over clever shortcuts.
4. Safe automation
    Respect site constraints. Use conservative polling. Do not introduce behavior that looks abusive.
5. Fact versus inference
    Distinguish clearly between:
    1. observed page behavior
    2. inferred behavior
    3. unverified assumptions
6. Evidence preserving
    On meaningful state changes and failures, store enough evidence to debug the issue later.
7. Idempotent behavior
    The system must avoid duplicate submissions and accidental repeated actions.
8. Graceful degradation
    If full submission is not safe or possible, the system should still provide high quality detection and alerting.

⸻

Preferred stack

Unless there is a strong reason otherwise, agents should use the following stack:

1. Python for implementation
2. Playwright for browser automation
3. httpx or requests for direct HTTP polling where feasible
4. SQLite for lightweight persistent state
5. Structured logging using Python logging with JSON or consistently parseable text output
6. pytest for tests

Do not introduce a heavier framework without a concrete need.

⸻

Repository architecture expectations

Agents should preserve or evolve the repo toward a structure similar to this:

.
├── AGENTS.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── detection_strategy.md
│   ├── submission_strategy.md
│   ├── operations.md
│   ├── deployment.md
│   └── risk_register.md
├── src/
│   ├── main.py
│   ├── app/
│   ├── config/
│   ├── detector/
│   ├── orchestrator/
│   ├── submitter/
│   ├── verifier/
│   ├── notifier/
│   ├── persistence/
│   ├── diagnostics/
│   └── utils/
├── scripts/
├── tests/
├── artifacts/
├── logs/
├── requirements.txt
├── pyproject.toml
└── .env.example

If the actual structure differs, keep the same conceptual boundaries.

⸻

Module responsibilities

detector/

Responsible for page state detection.

Expected responsibilities:

1. Poll target pages or endpoints conservatively
2. Extract text, DOM, or network level signals
3. Compare observed state with prior state
4. Produce structured detection results with confidence
5. Trigger artifact capture when meaningful changes occur

orchestrator/

Responsible for workflow control.

Expected responsibilities:

1. Schedule checks
2. Evaluate detector outputs
3. Decide whether to alert, verify, submit, or wait
4. Prevent duplicate or conflicting actions
5. Record significant state transitions

submitter/

Responsible for submission attempts.

Expected responsibilities:

1. Load prepared form values securely
2. Map values to selectors or request payloads
3. Perform submission in dry run or live mode
4. Capture evidence around submission attempts
5. Return structured outcomes

verifier/

Responsible for success confirmation.

Expected responsibilities:

1. Check post submission success conditions
2. Validate confirmation signals
3. Detect ambiguous outcomes
4. Escalate uncertainty rather than silently assuming success

notifier/

Responsible for outbound alerts.

Expected responsibilities:

1. Send opening detected alerts
2. Send submission success alerts
3. Send failure or escalation alerts
4. Support quiet repetitive alerts control

persistence/

Responsible for durable state.

Expected responsibilities:

1. Store latest observed state
2. Store submission attempts
3. Store confirmation references
4. Preserve deduplication markers
5. Support crash recovery

diagnostics/

Responsible for debugging evidence.

Expected responsibilities:

1. Save screenshots on important events
2. Save HTML snapshots
3. Save relevant response bodies where appropriate
4. Preserve timestamps and context metadata

⸻

Page state detector requirements

The detector is the central subsystem and must be treated accordingly.

Agents must ensure the detector:

1. Explicitly models known page states
2. Produces structured outputs, not vague booleans
3. Uses confidence based reasoning where useful
4. Supports at least:
    1. text based detection
    2. DOM based detection
    3. optional network level detection
5. Minimizes false positives
6. Preserves evidence for any claimed state transition
7. Is testable independently of the submitter

Detector outputs should contain fields similar to:

{
  "state": "closed",
  "confidence": 0.98,
  "signals": ["closed_text_present"],
  "page_url": "https://example.com",
  "timestamp_utc": "2026-04-23T12:00:00Z",
  "evidence_paths": []
}

Exact format may evolve, but outputs must remain structured and machine actionable.

⸻

Submission workflow requirements

Agents must not assume a live form is always available during development.

If form selectors or flow details are unknown, implement the system so that:

1. detection is still complete and useful
2. unknown submission details are isolated behind interfaces
3. placeholder mappings are explicit
4. configuration requirements are documented
5. dry run mode works without live submission

Submission logic must support:

1. dry run mode
2. live mode
3. duplicate prevention
4. post action verification
5. safe failure reporting

Never hardcode secrets directly into source files.

⸻

Polling and site interaction rules

Agents must design polling conservatively.

Guidelines:

1. Prefer stable intervals with optional bounded jitter
2. Avoid extremely aggressive refresh behavior
3. Use timeouts and retry limits
4. Treat repeated failures as signals, not as reasons to increase load
5. Distinguish:
    1. temporary site errors
    2. layout changes
    3. likely anti bot responses
    4. real opening signals

Do not implement behavior intended to conceal automation.

⸻

Anti bot and challenge handling

Agents must inspect target pages and flows for anti automation signals.

Examples include:

1. CAPTCHA or challenge widgets
2. Cloud based protection pages
3. CSRF or dynamic hidden fields
4. login and session expiration behavior
5. suspicious polling blocks
6. form side validation that appears automation sensitive

If such controls appear:

1. record the observation clearly
2. preserve evidence
3. avoid adding bypass logic
4. fall back to alerting or human assisted handling where necessary
5. document architectural implications in docs/

⸻

Configuration rules

Agents must keep runtime configuration externalized.

Expected configuration categories include:

1. target URLs
2. polling parameters
3. detector thresholds
4. notifier credentials
5. form values
6. student ID and related sensitive fields
7. submission mode flags
8. artifact storage paths

Rules:

1. Use environment variables or config files excluded from version control
2. Maintain .env.example
3. Validate configuration at startup
4. Fail loudly on missing critical config

⸻

Logging and diagnostics rules

Every important subsystem must log in a consistent and structured way.

Minimum logging events:

1. startup and shutdown
2. each detection cycle summary
3. state transition detected
4. submission started
5. submission result
6. verification result
7. notifier result
8. retry and backoff behavior
9. unexpected exceptions

On meaningful failures or major transitions, capture:

1. screenshot if browser context exists
2. HTML snapshot
3. relevant response metadata
4. timestamps
