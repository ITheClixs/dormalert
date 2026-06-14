# Free 24/7 WhatsApp alerts via GitHub Actions

This is how DormAlert watches `livingscience` around the clock with **no VPS, no
always-on PC, and no paid services**. GitHub Actions is the free "server";
CallMeBot is the free WhatsApp relay.

## How it works

- A scheduled GitHub Actions workflow (`.github/workflows/dormalert-detect.yml`)
  runs `detect-once` every ~5 minutes on GitHub's machines.
- The detector compares the LivingScience page to the known closed/waitlist
  text. When that text **disappears or changes**, it raises a CRITICAL
  `closed_text_missing_alert` (plus an `availability_change`).
- Those alerts fan out to every configured notifier. With WhatsApp enabled and
  email disabled, you get a **WhatsApp message**.
- The SQLite state is cached between runs, so you are alerted **once** per
  change instead of every 5 minutes.

> Note: For `livingscience` the detector reports `opening_candidate`, not
> `open` — the live "open" form markup has not been mapped yet, so the system
> deliberately does not auto-submit. The WhatsApp alert means **"go check the
> page now,"** which is exactly what you asked for.

> Timing reality: GitHub's scheduled cron is best-effort and can run a few
> minutes late under load. For more punctual triggering, keep your cron-job.org
> trigger (Step 5) — but it bills the same Actions minutes.

---

## Step 1 — Get your free CallMeBot WhatsApp API key

From the phone whose WhatsApp should receive alerts:

1. Add the contact **+34 644 51 95 23** (CallMeBot).
2. Send this exact WhatsApp message to it:
   `I allow callmebot to send me messages to this number`
3. You'll get a reply with your **API key** (a number like `123456`), usually
   within minutes.

Your phone number must be in full international format, e.g. `+41791234567`.

Free, no signup, no subscription. (Messages pass through CallMeBot's server —
fine for "waitlist opened" pings.)

## Step 2 — Make the repo public (for unlimited free minutes)

Private repos only get 2000 free Actions min/month — not enough for 5-minute
polling. Public repos get **unlimited** free minutes. The code contains no
secrets (they live in GitHub Secrets, below).

GitHub → your repo → **Settings → General → Danger Zone → Change repository
visibility → Make public**.

## Step 3 — Add your secrets to GitHub

GitHub → repo → **Settings → Secrets and variables → Actions → New repository
secret**. Add two:

| Secret name | Value |
|---|---|
| `DORMALERT_WHATSAPP_PHONE` | your number, e.g. `+41791234567` |
| `DORMALERT_WHATSAPP_APIKEY` | the key from Step 1, e.g. `123456` |

Secrets are encrypted and are **not** visible even on a public repo.

## Step 4 — Push this code and confirm the workflow runs

Once `.github/workflows/dormalert-detect.yml` is on the `main` branch:

- GitHub → repo → **Actions** tab → enable workflows if prompted.
- Open **DormAlert Detect → Run workflow** to trigger one manually.
- After ~1 minute, check the run log. A normal "still closed" run logs
  `state: closed` and sends **no** WhatsApp (by design).

To test the WhatsApp path end-to-end without waiting for an opening, run locally:

```bash
source .venv/bin/activate
export DORMALERT_WHATSAPP_ENABLED=true
export DORMALERT_WHATSAPP_PHONE="+41791234567"
export DORMALERT_WHATSAPP_APIKEY="123456"
python -m src.main test-whatsapp
```

You should receive a "DormAlert WhatsApp test" message.

## Step 5 (optional) — Punctual triggering via cron-job.org + your PAT

GitHub's own cron can lag. To poke the workflow on a precise schedule, keep your
cron-job.org job calling the GitHub API:

- **URL:** `https://api.github.com/repos/ITheClixs/dormalert/actions/workflows/dormalert-detect.yml/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Authorization: Bearer <YOUR_FINE_GRAINED_PAT>`
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
- **Body:** `{"ref":"main","inputs":{"site":"livingscience"}}`

Your fine-grained PAT needs, **on this repo only**:
- **Actions:** Read and write
- **Contents:** Read-only
- **Metadata:** Read-only (auto-selected)

A successful dispatch returns HTTP **204** with an empty body.

---

## Turning it off / acknowledging

- Stop everything: GitHub → Actions → DormAlert Detect → **⋯ → Disable
  workflow**.
- Change polling speed: edit the `cron:` line in the workflow
  (`*/5 * * * *` = every 5 min; `*/15 * * * *` = every 15 min).
- The dedupe cache means a single change alerts once. If the page changes again
  (different content fingerprint), you get a fresh alert.
