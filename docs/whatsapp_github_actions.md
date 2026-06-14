# Free 24/7 alerts via GitHub Actions (Telegram)

This is how DormAlert watches `livingscience` around the clock with **no VPS, no
always-on PC, and no paid services**. GitHub Actions is the free "server";
Telegram's Bot API is the free, instant, reliable alert channel.

> WhatsApp via CallMeBot is supported too but is frequently overloaded (with a
> 24h retry penalty), so Telegram is the recommended channel. CallMeBot steps
> are at the bottom.

## How it works

- A scheduled GitHub Actions workflow (`.github/workflows/dormalert-detect.yml`)
  runs `detect-once` every ~5 minutes on GitHub's machines.
- The detector compares the LivingScience page to the known closed/waitlist
  text. When that text **disappears or changes**, it raises a CRITICAL
  `closed_text_missing_alert` (plus an `availability_change`).
- Those alerts fan out to every configured notifier. With Telegram enabled and
  email disabled, you get a **Telegram message**.
- The SQLite state is cached between runs, so you are alerted **once** per
  change instead of every 5 minutes.

> Note: For `livingscience` the detector reports `opening_candidate`, not
> `open` — the live "open" form markup has not been mapped yet, so the system
> deliberately does not auto-submit. The alert means **"go check the page
> now,"** which is exactly what you asked for.

> Timing reality: GitHub's scheduled cron is best-effort and can run a few
> minutes late under load. For more punctual triggering, keep your cron-job.org
> trigger (Step 5) — but it bills the same Actions minutes.

---

## Step 1 — Create a Telegram bot and get two values

You need a **bot token** and a **chat id**.

1. Install Telegram (phone or desktop) and open it.
2. Search for **@BotFather** (the official one, blue checkmark) → start it →
   send `/newbot`.
3. Pick a name and a username ending in `bot` (e.g. `dormalert_xyz_bot`).
4. BotFather replies with a **token** like
   `8123456789:AAExampletoKEN-do_not_share_this`. That is your
   `DORMALERT_TELEGRAM_BOT_TOKEN`.
5. **Open a chat with your new bot and press Start / send any message** (the bot
   can't message you until you do).
6. Get your **chat id**: search for **@userinfobot**, start it, and it replies
   with `Id: 123456789`. That number is your `DORMALERT_TELEGRAM_CHAT_ID`.

Free, instant, no phone-number sharing, no third-party relay.

## Step 2 — Make the repo public (for unlimited free minutes)

Private repos only get 2000 free Actions min/month — not enough for 5-minute
polling. Public repos get **unlimited** free minutes. The code contains no
secrets (they live in GitHub Secrets, below). *(Already done for this repo.)*

GitHub → repo → **Settings → General → Danger Zone → Change repository
visibility → Make public**.

## Step 3 — Add your secrets to GitHub

GitHub → repo → **Settings → Secrets and variables → Actions → New repository
secret**. Add two:

| Secret name | Value |
|---|---|
| `DORMALERT_TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `DORMALERT_TELEGRAM_CHAT_ID` | the number from @userinfobot |

Secrets are encrypted and are **not** visible even on a public repo.

## Step 4 — Confirm it works

End-to-end test without waiting for an opening — run locally:

```bash
source .venv/bin/activate
export DORMALERT_TELEGRAM_ENABLED=true
export DORMALERT_TELEGRAM_BOT_TOKEN="8123456789:AA..."
export DORMALERT_TELEGRAM_CHAT_ID="123456789"
python -m src.main test-telegram
```

You should receive a "DormAlert Telegram test" message in the chat with your bot.

Then trigger the live pipeline once: GitHub → **Actions → DormAlert Detect → Run
workflow**. A normal "still closed" run logs `state: closed` and sends **no**
message (by design).

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

---

## Alternative: WhatsApp via CallMeBot (flaky)

If you prefer WhatsApp and CallMeBot is responding:

1. Add the contact **+34 621 34 34 03** (number changes occasionally — confirm at
   https://www.callmebot.com/blog/free-api-whatsapp-messages/).
2. Send it exactly: `I allow callmebot to send me messages`.
3. It replies with an API key (e.g. `123456`). If nothing arrives in 2 minutes,
   CallMeBot enforces a 24h retry wait.
4. Set secrets `DORMALERT_WHATSAPP_PHONE` (your number, `+41...`) and
   `DORMALERT_WHATSAPP_APIKEY`, and in the workflow set
   `DORMALERT_WHATSAPP_ENABLED: "true"`.
5. Verify locally with `python -m src.main test-whatsapp`.
