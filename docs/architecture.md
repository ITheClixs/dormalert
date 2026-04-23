# System Design Document

## A. Problem framing

DormAlert is a state detection and reaction system.

The core problem is not "submit a form." The core problem is:

1. detect a transition from unavailable to available with minimal delay
2. preserve enough evidence to trust the signal
3. trigger the safest appropriate reaction
4. avoid duplicate or conflicting actions
5. verify outcomes instead of assuming success

The system is divided into six operational concerns:

1. detection
2. orchestration
3. submission
4. verification
5. notifications
6. operations and evidence

## B. State model

### Detector states

The detector emits one of:

- `closed`
- `opening_candidate`
- `open`
- `failed`

### Workflow states

The orchestrator tracks the overall workflow as:

- `closed`
- `opening_candidate`
- `open`
- `submitted`
- `verified`
- `failed`

### Transition conditions

`closed -> opening_candidate`

- one or more closed-state banners disappear
- or the DOM meaningfully changes in a way that suggests availability may have changed
- but evidence is not yet strong enough for `open`

`opening_candidate -> open`

- the closed-state markers disappear on the key monitored page
- supporting signals agree
- no blocking anti-bot signal is present
- confidence meets the profile threshold

`open -> submitted`

- a submission attempt is actually made
- the attempt is recorded and deduped

`submitted -> verified`

- success criteria are positively observed

`any -> failed`

- repeated detection failures
- anti-bot blocking event
- submission exception
- explicit negative verification signal

### Confidence criteria

`livingscience`

- `closed`: exact closed phrase present in HTML
- `opening_candidate`: closed phrase absent but no visible form yet
- `open`: closed phrase absent and strong form/actionable submission markers appear

`studentvillage`

- `closed`: closed-state banners remain visible on the monitored apply/home/contact pages
- `opening_candidate`: the apply page banner changes before supporting pages do, or the content becomes inconsistent
- `open`: the closed banner disappears from the apply page and supporting pages, while the registration path remains available and no blocking anti-bot signal is observed

## C. Architecture

### Page state detector

Responsibilities:

- fetch target pages conservatively
- inspect server-rendered HTML and response metadata
- classify page state using site-specific rules
- attach explicit facts, inferences, uncertainties, and anti-bot observations

Implementation choice:

- HTTP-first multi-target detector
- site-specific profiles for `livingscience` and `studentvillage`

### Anti-bot observation layer

Responsibilities:

- inspect HTML and headers for:
  - CAPTCHA markers
  - reCAPTCHA/hCaptcha/Turnstile
  - Cloudflare challenge signals
  - session cookies
  - tokenized forms
  - suspicious challenge language
- mark observations as `info`, `warning`, or `blocking`

The anti-bot layer does not attempt bypasses. It only observes and informs policy decisions.

### Trigger/orchestrator

Responsibilities:

- run scheduled detection cycles
- compare against prior state
- capture evidence on important changes
- dedupe alerts and submission attempts
- decide whether to notify, submit, verify, or wait

### Submitter

Responsibilities:

- isolate live form automation behind explicit interfaces
- support `disabled`, `dry_run`, and `live`
- use Playwright for `studentvillage`
- refuse unsafe live submission if blocking anti-bot signals are present

### Verifier

Responsibilities:

- apply rule-based success/failure phrase checks
- classify post-submit state as confirmed, ambiguous, or failed
- escalate ambiguous results instead of silently accepting them

### Notifier

Responsibilities:

- emit state-transition alerts
- emit open-state alerts
- emit submission and verification alerts
- emit failure alerts after repeated failures

Initial channels:

- structured console logs
- generic outbound webhook

### Config and secrets manager

Responsibilities:

- load environment variables and `.env`
- validate critical configuration
- keep applicant data and secrets out of source control

### Persistence layer

SQLite stores:

- runtime state per site
- detection history
- dedupe markers
- submission attempts

### Logging and diagnostics layer

Responsibilities:

- JSON logs for each subsystem
- HTML/header capture on transitions and failures
- browser screenshots during submission attempts
- durable evidence for investigation

## D. Tradeoff analysis

### Browser extension vs browser automation vs direct HTTP vs hybrid

Browser extension:

- weak fit for VPS or headless operation
- harder to supervise as a service
- poor separation between detection and operations

Direct HTTP only:

- excellent for fast, cheap polling
- insufficient for JavaScript-bound submission flows

Browser automation only:

- more expensive
- slower polling
- more brittle for always-on monitoring

Hybrid:

- best fit
- HTTP handles most detection cycles
- Playwright is reserved for live submission or high-fidelity validation

### Playwright vs Selenium

Playwright is preferred because:

- reliable waiting model
- strong headless support
- easier screenshot/content capture
- better ergonomics for deterministic automation

### Local machine vs VPS vs Raspberry Pi vs home server

Recommended: VPS or other always-on Linux host.

- VPS: best availability and supervision
- home server: acceptable if uptime and networking are stable
- Raspberry Pi: workable for detector-only or light usage, but less ideal for browser automation
- laptop/local machine: useful for development, not recommended for production monitoring

## E. Detection strategy

### Text checks

- exact closed-state phrases
- supporting banners on multiple pages
- candidate open-state phrases when known

### DOM checks

- form presence
- hidden tokens
- action buttons
- form IDs and input names

### Network/API checks

- HTTP status
- cookies
- response headers
- challenge markers

### Polling intervals

Conservative defaults:

- `livingscience`: 300 seconds plus bounded jitter
- `studentvillage`: 180 seconds plus bounded jitter

These defaults balance timeliness and low load. They are configurable.

### Jitter

Bounded positive jitter prevents perfectly periodic traffic patterns.

### False-positive avoidance

- multi-signal classification
- supporting pages for `studentvillage`
- `opening_candidate` state instead of over-eager `open`
- anti-bot observations reduce automation confidence

### State evidence capture

On meaningful transitions and failures:

- HTML snapshots
- response headers
- detection summary JSON
- submission screenshots when applicable

## F. Submission strategy

### Selector discovery

The implementation ships with the currently observed `studentvillage` selectors and field names.

### Form field mapping

Applicant data is externalized in environment variables.

### Hidden inputs

The `studentvillage` flow currently exposes hidden `form_token` and `request_rentaldate`.

### Session and CSRF handling

The live submitter uses a real browser session so the server-issued token and cookies stay aligned.

### Safety controls

- `detector_only` global override
- per-site submission modes
- anti-bot blocking gate
- pre-submit closed-banner recheck

### Duplicate prevention

Submission dedupe keys combine site and detection fingerprint.

### Human-in-the-loop fallback

If the site opens but success cannot be safely verified, the system alerts immediately with evidence and stops short of repeated blind retries.

## G. Reliability

### Expected failure modes

- transient network failures
- site content changes
- anti-bot or challenge introduction
- selector drift
- ambiguous submission outcomes
- host restarts

### Mitigations

- retries with bounded limits
- persisted runtime state
- evidence capture
- repeated-failure escalation
- dry-run mode
- detector/submitter separation

### Crash recovery

The scheduler and dedupe state restart from SQLite state.

### Watchdogs and observability

- JSON logs
- process supervision via `systemd`
- artifact trail for each important event

## H. Deployment strategy

The system is designed to run continuously as a service.

Recommended deployment model:

1. Linux VPS
2. Python virtualenv
3. Playwright Chromium installed locally
4. `.env` stored with restricted permissions
5. process kept alive by `systemd`
6. logs written to stdout and `logs/app.log`

