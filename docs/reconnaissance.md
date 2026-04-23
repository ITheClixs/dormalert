# Technical Reconnaissance And Feasibility Assessment

Observed on April 23, 2026.

## Summary

Both target sites are monitorable, but they differ materially.

- `livingscience.ch` currently exposes a clean public closed-state message in server-rendered HTML. Detection is straightforward by raw HTTP.
- `studentvillage.ch` currently exposes closed-state messaging on multiple public pages while still rendering a registration form on the apply page. Detection is feasible, but the detector must key off the closed-state banners and related signals rather than assuming that form presence means the site is open.

## Site 1: livingscience.ch

Target URL:

- `https://livingscience.ch/wohnen-studieren-zuerich/?L=0`

### Observed facts

- The public HTML contains the exact closed-state message:
  - `Unsere Wartelisten sind derzeit voll. Vorübergehend können wir keine neuen Anmeldungen annehmen. Sobald die Warteliste wieder geöffnet ist, wird das Anmeldeformular wieder zur Verfügung stehen.`
- The response is plain `HTTP/2 200` from Apache.
- The response sets a `fe_typo_user` cookie.
- No visible public application form is currently rendered on the inspected page.
- The page source references TYPO3 `powermail` frontend assets, which implies a form framework is present somewhere in the site stack even though no form is currently visible on this page.
- No visible `reCAPTCHA`, `hCaptcha`, Cloudflare challenge, or JavaScript challenge marker was present in the inspected HTML or headers.

### Feasibility assessment

- A page state detector is highly feasible.
- The current closed state is directly observable in raw HTML without browser automation.
- HTTP polling is sufficient for the primary detector on this site.
- Browser automation is not currently required for detection.

### Anti-bot assessment

Observed:

- No visible CAPTCHA or challenge widget.
- No Cloudflare-specific headers or challenge markers.
- A TYPO3 session cookie is set.

Inferred:

- If the form reappears later, TYPO3 `powermail` may introduce hidden inputs, per-request tokens, or server-side validation rules that are not visible today.
- Submission should remain disabled until the reopened form is observed and mapped.

### Current uncertainty

- The real reopened form structure is not currently visible.
- Hidden anti-automation controls could appear only after the form becomes available.
- Success criteria for a future submission flow are unknown until the form returns.

## Site 2: studentvillage.ch

Primary monitored pages:

- `https://studentvillage.ch/en/`
- `https://studentvillage.ch/en/apply/`
- `https://studentvillage.ch/en/contact/`

Secondary inspected page:

- `https://studentvillage.ch/en/login/`

### Observed facts

Home page:

- The home page currently displays:
  - `All rooms are currently occupied`

Apply page:

- The apply page currently displays:
  - `Currently all rooms are rented. We do not have a waiting list.`
- The same page also renders a registration form with `id="register_form"`.
- The form includes hidden fields:
  - `form_type=register_form`
  - `form_token=<token>`
- The form includes application/account fields such as name, address, DOB, nationality, faculty, username, password, and a hidden `request_rentaldate`.
- The submit button invokes `regformhash(...)`.

Contact page:

- The contact page FAQ content includes:
  - `There are currently no rooms available and we do not have a waiting list.`
- The contact form also contains a hidden `form_token`.

Login page:

- The login page contains hidden fields:
  - `form_type=login_form`
  - `form_token=<token>`
- The submit button invokes `formhash(...)`.

JavaScript:

- `general-v14.js` defines `formhash(...)` and `regformhash(...)`.
- Those functions SHA-512 hash the password client-side into a hidden `p` field before submit.

Headers and cookies:

- Responses come from Apache.
- The apply page sets a `PHPSESSID`.
- No visible Cloudflare challenge page or CAPTCHA widget was present in the inspected public pages.

### Feasibility assessment

- A detector is feasible, but it must use banner/state text rather than naive form detection.
- Raw HTTP polling is sufficient for the primary detector because the closed-state phrases are server-rendered.
- Submission is likely possible with browser automation because the public form, token field, and client-side hashing behavior are visible.
- Direct HTTP submission might also be possible because the password hashing logic is visible, but browser automation is safer and less brittle for a first live implementation.

### Anti-bot assessment

Observed:

- No visible reCAPTCHA, hCaptcha, Turnstile, or Cloudflare challenge marker on the inspected public pages.
- Hidden per-request `form_token` values are present.
- Session cookies are present.
- Client-side password hashing is required for login and registration submits.

Inferred:

- The server likely expects valid session state plus the hidden `form_token`.
- Additional validation, rate limiting, or server-side anti-automation rules may appear only on submit.
- The site could introduce challenge pages later without changing the public landing pages.

### What is monitorable via HTTP vs browser

Raw HTTP is enough for:

- Closed/open banner text detection
- Hidden input and token presence checks
- Header and cookie inspection
- Evidence capture of HTML snapshots

Browser automation is preferred for:

- Safe submission when the site appears open
- Any flow that depends on JavaScript submit helpers, session continuity, or DOM interaction
- Screenshot capture during submission attempts

### Current uncertainty

- The real successful post-submit confirmation text is not yet known.
- It is not yet proven whether the currently visible register form is intended to accept a real new application when the closed banner disappears.
- It is possible that the public open state later changes into a room-selection or booking workflow not yet visible today.

## Final feasibility statement

The project is feasible.

- `livingscience`: detector-first, alert-first, submission disabled until a real public form reappears.
- `studentvillage`: detector-first, notification-ready, staged browser-based submission implementation available now but defaulted to `dry_run` until the open-state behavior and post-submit verification signals are validated.

