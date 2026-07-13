# WhatsApp alerts via Meta's official Cloud API

The official WhatsApp Business Cloud API is the reliable WhatsApp channel:
free at DormAlert's volume, no third-party relay, no ban risk. It replaces the
flaky CallMeBot route (which stays in the code but is no longer wired into the
workflows).

Design: WhatsApp is an **additive best-effort ping on top of email**. Email
remains the durable channel — the orchestrator only marks an opening notified
after an email lands. The daily heartbeat also goes to WhatsApp so a silently
broken channel (expired token, paused template) is noticed within a day.

Business-initiated messages outside a 24-hour reply window must be **template
messages**, so DormAlert sends every alert through one pre-approved utility
template (`dormalert_alert`, body `DormAlert: {{1}}`) with the alert text as
the single parameter.

## One-time setup (~30-45 min)

### 1. Create a Meta app

1. Go to https://developers.facebook.com and log in with a Facebook account
   (create one if needed).
2. **My Apps → Create App**. Use case: **Other** → app type: **Business** →
   name e.g. `DormAlert`. If asked, let it create a Business portfolio.
3. On the app dashboard find the **WhatsApp** product → **Set up**. This gives
   you a free **test phone number** (a +1 555 number) that can message up to 5
   verified recipients — exactly enough.

### 2. Get the Phone number ID and verify your own number

1. In the app: **WhatsApp → API Setup**.
2. Under "From", the **Test number** is selected — copy its
   **Phone number ID** (a long digit string, *not* the +1 555 number itself).
3. Under "To": **Manage phone number list → Add phone number** → enter your
   own WhatsApp number → you receive a code **in WhatsApp** → enter it.
4. Use the page's "Send message" test (hello_world template) and confirm it
   arrives in your WhatsApp.

### 3. Create a permanent access token

The token shown on the API Setup page expires in 24 hours — do not use it.

1. Go to https://business.facebook.com/settings (pick the business portfolio
   created above).
2. **Users → System users → Add**: name `dormalert-bot`, role **Admin**.
   (Meta may require two-factor authentication on your account first.)
3. Select the system user → **Add assets → Apps** → pick the DormAlert app →
   enable **Manage app (full control)** → save.
4. **Generate new token** → app: DormAlert → expiration: **Never** →
   permissions: `whatsapp_business_messaging` and
   `whatsapp_business_management` → generate, and copy the token now (it is
   shown once).

### 4. Create the alert template

1. Go to WhatsApp Manager → **Message templates** (
   https://business.facebook.com/wa/manage/message-templates/) → **Create
   template**.
2. Category: **Utility**. Name: `dormalert_alert`. Language: **English**
   (plain "English" = code `en`; if you pick "English (US)" instead, set the
   repo variable `DORMALERT_WA_CLOUD_TEMPLATE_LANG=en_US`).
3. Body: `DormAlert: {{1}}` — add any sample value when prompted. No header,
   no footer, no buttons.
4. Submit. Utility templates are usually approved within minutes; status shows
   in Message templates.

### 5. Add the repo secrets

```bash
gh secret set DORMALERT_WA_CLOUD_TOKEN                # paste the permanent token
gh secret set DORMALERT_WA_CLOUD_PHONE_NUMBER_ID      # from step 2
gh secret set DORMALERT_WA_CLOUD_TO                   # your number, digits only with country code, e.g. 41791234567
```

Both workflows enable the channel automatically once all three secrets exist —
no workflow edit needed.

### 6. Verify end to end

Locally:

```bash
source .venv/bin/activate
export DORMALERT_WA_CLOUD_ENABLED=true
export DORMALERT_WA_CLOUD_TOKEN="EAAG..."
export DORMALERT_WA_CLOUD_PHONE_NUMBER_ID="1234567890..."
export DORMALERT_WA_CLOUD_TO="41791234567"
python -m src.main test-whatsapp
```

Then in CI: `gh workflow run dormalert-heartbeat.yml` — the heartbeat should
arrive on both Gmail and WhatsApp.

## What pings the phone

Opening alerts and reminders, closed-text-disappeared, availability changes,
manual-action-required, repeated detector failures, and the daily heartbeat.
Everything else stays on email/console.

## Known limits

- The free test number can only message the (up to 5) verified recipients —
  fine for self-alerting, forever.
- Template language code must match exactly (`en` vs `en_US`); a mismatch
  returns error 132001 ("template name does not exist in the translation").
- If Meta ever pauses the template (user reports spam etc.), the heartbeat
  stops arriving on WhatsApp — that is the built-in early warning; email keeps
  working regardless.
