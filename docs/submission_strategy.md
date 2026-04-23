# Submission Strategy

## Principles

1. submission is isolated behind interfaces
2. live submission is never the only useful mode
3. the system must remain operational when submission is disabled
4. ambiguous results are escalated, not silently accepted

## Current implementation stance

### livingscience

- live submission is not implemented
- reason: no public form is currently visible
- action on open: alert immediately and capture evidence

### studentvillage

- staged submission is implemented with Playwright
- default mode is `dry_run`
- live mode is available but should only be enabled after dry-run validation

## Why Playwright for submission

Observed on April 23, 2026:

- the apply page carries a hidden `form_token`
- the site uses a PHP session
- `regformhash(...)` hashes the password client-side before submit

That makes a real browser session the safest first live strategy.

## Submission workflow

1. re-open the public apply page in a browser
2. verify the closed-state banner is no longer present
3. ensure no blocking anti-bot signal is visible
4. fill mapped fields from environment-backed config
5. keep hidden tokens and session state intact
6. submit through the site’s own JS path
7. capture screenshots and final HTML
8. pass the result to the verifier

## Field mapping

The current `studentvillage` mapper supports the observed fields:

- `firstname`
- `lastname`
- `email`
- `second_email`
- `address`
- `zipcode`
- `city`
- `country`
- `phonenumber`
- `dob`
- `roomnumberonregister`
- `parents`
- `nationality`
- `studentfaculty`
- `spokenlanguage`
- `gender`
- `username`
- `password`
- `confirmpwd`
- `request_rentaldate`
- `comments`

Sensitive values live in `.env`, not in source.

## Hidden inputs and tokens

The implementation intentionally does not synthesize or bypass hidden tokens.

- browser session loads the current token from the server
- form submission happens in the same page context

## Duplicate prevention

The orchestrator creates dedupe keys from:

- `site_id`
- detection fingerprint
- action type

This prevents repeated alerts or repeated submits against the same observed opening signal.

## Verification

The verifier is rule-based and conservative.

- success phrases are configurable
- failure phrases are configurable
- absence of either becomes `ambiguous`

An ambiguous submission is still important operationally:

- evidence is preserved
- notification is sent
- the system does not blindly resubmit

## Human-assisted fallback

If any of the following occur, the system alerts instead of forcing autonomous behavior:

- visible CAPTCHA or challenge
- unclear post-submit result
- missing required live config
- unsupported site flow
- selector drift

