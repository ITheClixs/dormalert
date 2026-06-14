# Deployment

## Recommended target

Use an always-on Linux VPS with:

- Python 3.11+
- outbound HTTPS access
- enough memory for a headless Chromium session

## No-VPS alert-only deployment

For alert-only LivingScience monitoring, a VPS is not strictly required. Use
cron-job.org as the scheduler and GitHub Actions as the compute host:

```text
cron-job.org -> GitHub workflow_dispatch -> GitHub-hosted runner -> detect-once -> SMTP alert
```

This mode is best for detecting a LivingScience waitlist text change and
emailing immediately. It is not a durable always-on process, and it does not
persist SQLite dedupe state between runs unless a separate state backend is
added.

### GitHub Actions setup

The workflow lives at:

```text
.github/workflows/dormalert-detect.yml
```

Add these repository secrets in GitHub:

```text
DORMALERT_SMTP_HOST=smtp.gmail.com
DORMALERT_SMTP_PORT=587
DORMALERT_SMTP_USERNAME=your-sender@gmail.com
DORMALERT_SMTP_PASSWORD=your-google-app-password
DORMALERT_EMAIL_FROM=your-sender@gmail.com
DORMALERT_EMAIL_TO=your-receiver@gmail.com
```

For Gmail, use a Google App Password from an account with 2-step verification
enabled. Do not use the normal mailbox password.

After the workflow is on `main`, test it manually:

1. open the repository on GitHub
2. go to `Actions`
3. select `DormAlert Detect`
4. click `Run workflow`
5. choose `livingscience`
6. confirm the run completes

### cron-job.org trigger

Create a fine-grained GitHub personal access token with access only to this
repository:

```text
Repository: ITheClixs/dormalert
Permissions:
- Actions: Read and write
- Metadata: Read-only
```

Create a cron-job.org job:

```text
URL:
https://api.github.com/repos/ITheClixs/dormalert/actions/workflows/dormalert-detect.yml/dispatches

Method:
POST

Headers:
Accept: application/vnd.github+json
Authorization: Bearer YOUR_FINE_GRAINED_PAT
X-GitHub-Api-Version: 2026-03-10
Content-Type: application/json

Body:
{"ref":"main","inputs":{"site":"livingscience"}}
```

Use a one-minute schedule only if you accept the operational tradeoff of more
frequent requests. A two- to five-minute interval is more conservative.

Expected result:

- cron-job.org receives a successful GitHub API response
- a GitHub Actions run appears under `Actions -> DormAlert Detect`
- the run executes `python -m src.main detect-once --site livingscience --detector-only`
- if the monitored LivingScience closed/waitlist text disappears or changes,
  DormAlert sends a `closed_text_missing_alert` email

Because GitHub-hosted runners are ephemeral, if the text is missing on every
subsequent run, this mode can send repeated alerts. Disable the cron job after
receiving the opening alert, or add durable state before relying on long-lived
dedupe behavior.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

## Configuration

- keep `.env` outside version control
- restrict file permissions on `.env`
- keep applicant credentials and personal data in environment variables only

## Local validation

```bash
python3 -m src.main detect-once
python3 -m src.main run --detector-only
python3 -m src.main detect-once --site studentvillage
```

Dry-run validation:

```bash
python3 -m src.main submit-once --site studentvillage --dry-run
```

## systemd example

An example service unit is shipped at:

- `scripts/dormalert.service.example`

Typical flow:

1. copy the file to `/etc/systemd/system/dormalert.service`
2. adjust `WorkingDirectory`, user, and environment paths
3. reload `systemd`
4. enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable dormalert
sudo systemctl start dormalert
sudo systemctl status dormalert
```

## Process supervision

`systemd` is preferred because it provides:

- restart policy
- log integration
- boot startup
- failure visibility

## Log inspection

If running under `systemd`:

```bash
journalctl -u dormalert -f
```

File log:

```bash
tail -f logs/app.log
```

## Validation after deployment

1. confirm periodic detection logs appear
2. confirm SQLite state file is created
3. confirm HTML artifacts are written on transitions or failures
4. run `python3 -m src.main test-email` if SMTP alerts are enabled
5. confirm `demirguven178@gmail.com` receives the test email outside spam
6. trigger a webhook test if configured
7. keep submission disabled until dry-run evidence is reviewed
