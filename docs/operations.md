# Operations

## Operating model

DormAlert is intended to run continuously.

The detector runs on a schedule, records every cycle, and only escalates when:

- the page state changes
- an open or opening-candidate state appears
- repeated failures occur
- a submission or verification event occurs

## Runtime modes

### Detector-only

Use when:

- first deploying
- validating detection rules
- monitoring unsupported sites
- anti-bot risk is high

### Dry run

Use when:

- selector mapping exists
- browser flow needs validation
- you want evidence without a real submit

### Live mode

Use only after:

- repeated detector runs look stable
- dry-run artifacts show correct field mapping
- post-submit verification rules are configured

## Logging

Logs are written as structured JSON lines.

Key events:

- startup
- shutdown
- detection cycle summary
- anti-bot observation
- state transition
- alert delivery
- submission started
- submission result
- verification result
- repeated failure threshold reached

## Artifacts

Artifacts are written under `artifacts/`.

Detection bundles include:

- HTML snapshots
- response headers
- detection summary JSON

Submission bundles may include:

- pre-submit screenshot
- post-submit screenshot
- final HTML
- submission metadata JSON

## Failure handling

Single transient failures are logged.

Repeated failures:

- increment a persisted counter
- produce a notification when the threshold is crossed

The system avoids noisy alert storms by deduping repeated identical failures.

## Operational safeguards

- conservative polling intervals
- bounded retries
- no anti-bot bypass logic
- deduped submit attempts
- configurable detector-only override
- persisted runtime state for restart safety

## Recommended operational checklist

Before enabling live submission:

1. run at least several days in detector-only mode
2. review artifacts for both sites
3. test webhook notifications
4. confirm `studentvillage` field mapping in dry-run mode
5. configure verification phrases if known
6. enable live mode only for the supported site

