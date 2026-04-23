# Risk Register

## High

### Hidden anti-bot controls appear only on submit

Impact:

- live submission may fail or be blocked

Mitigation:

- detector-first rollout
- browser-based submitter
- anti-bot observation layer
- human-assisted fallback

### False positive on `studentvillage` due to always-present form

Impact:

- premature alert or premature submission

Mitigation:

- classify using closed-state banners across multiple pages
- reserve `open` for stronger multi-page agreement
- keep auto-submit disabled by default

### Unknown success criteria after submission

Impact:

- system cannot safely declare success

Mitigation:

- configurable verification phrases
- ambiguous result escalation
- no blind repeat-submit loops

## Medium

### Selector drift

Impact:

- submission flow breaks

Mitigation:

- isolate selectors in the submitter
- preserve screenshots and final HTML
- validate in dry run before live

### Site layout changes invalidate text rules

Impact:

- detector may degrade to `opening_candidate` or `failed`

Mitigation:

- preserve HTML evidence
- use explicit facts/inferences
- keep rules centralized in site profiles

### Excessive polling perceived as abusive

Impact:

- throttling or blocking

Mitigation:

- conservative intervals
- bounded jitter
- no aggressive retries

## Low

### Host restart or process crash

Impact:

- temporary monitoring gap

Mitigation:

- SQLite state
- `systemd` restart policy

### Notification channel outage

Impact:

- opening detected but alert delivery fails

Mitigation:

- log notifier failures
- persist events locally
- support multiple notifiers later if needed

