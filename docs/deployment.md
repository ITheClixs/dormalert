# Deployment

## Recommended target

Use an always-on Linux VPS with:

- Python 3.11+
- outbound HTTPS access
- enough memory for a headless Chromium session

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
