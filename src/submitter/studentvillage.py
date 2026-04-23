from __future__ import annotations

from src.config.models import AppConfig
from src.detector.models import DetectionExecution
from src.diagnostics.artifacts import ArtifactManager
from src.submitter.base import SubmissionResult, SubmissionStatus
from src.utils.time import utcnow_iso


class StudentVillagePlaywrightSubmitter:
    APPLY_URL = "https://studentvillage.ch/en/apply/"
    CLOSED_PHRASE = "currently all rooms are rented. we do not have a waiting list."
    BLOCKING_MARKERS = (
        "g-recaptcha",
        "grecaptcha",
        "hcaptcha",
        "turnstile",
        "attention required",
        "cf-chl",
        "captcha",
    )

    def __init__(self, artifacts: ArtifactManager) -> None:
        self.artifacts = artifacts

    def submit(self, execution: DetectionExecution, config: AppConfig) -> SubmissionResult:
        started = utcnow_iso()
        applicant = config.studentvillage_applicant
        if applicant is None:
            finished = utcnow_iso()
            return SubmissionResult(
                site_id=execution.result.site_id,
                status=SubmissionStatus.FAILED,
                mode="live",
                attempted=False,
                started_at=started,
                finished_at=finished,
                message="Student Village live mode requires applicant configuration.",
                facts=("No applicant profile is configured.",),
                fingerprint=execution.result.fingerprint,
            )

        missing = applicant.missing_required_fields()
        if missing:
            finished = utcnow_iso()
            return SubmissionResult(
                site_id=execution.result.site_id,
                status=SubmissionStatus.FAILED,
                mode="live",
                attempted=False,
                started_at=started,
                finished_at=finished,
                message="Student Village applicant configuration is incomplete.",
                facts=(f"Missing fields: {', '.join(missing)}",),
                fingerprint=execution.result.fingerprint,
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on runtime environment
            finished = utcnow_iso()
            return SubmissionResult(
                site_id=execution.result.site_id,
                status=SubmissionStatus.FAILED,
                mode="live",
                attempted=False,
                started_at=started,
                finished_at=finished,
                message="Playwright is not installed in the runtime environment.",
                facts=(str(exc),),
                fingerprint=execution.result.fingerprint,
            )

        page = None
        browser = None
        context = None
        text_files: dict[str, str] = {}
        binary_files: dict[str, bytes] = {}

        try:  # pragma: no cover - exercised in live operation
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=config.browser.headless,
                    slow_mo=config.browser.slow_mo_ms,
                )
                context = browser.new_context(
                    user_agent=config.user_agent,
                    locale="en-US",
                )
                page = context.new_page()
                page.goto(self.APPLY_URL, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1_500)

                initial_html = page.content()
                initial_text = page.locator("body").inner_text()
                text_files["pre_submit.html"] = initial_html
                binary_files["pre_submit.png"] = page.screenshot(full_page=True)

                if self.CLOSED_PHRASE in initial_text.lower():
                    evidence_paths = self.artifacts.capture_submission(
                        site_id=execution.result.site_id,
                        reason="studentvillage_preflight_blocked",
                        metadata={
                            "reason": "closed_banner_still_present",
                            "detection_fingerprint": execution.result.fingerprint,
                            "applicant": applicant.redacted_summary(),
                        },
                        text_files=text_files,
                        binary_files=binary_files,
                    )
                    finished = utcnow_iso()
                    return SubmissionResult(
                        site_id=execution.result.site_id,
                        status=SubmissionStatus.BLOCKED,
                        mode="live",
                        attempted=False,
                        started_at=started,
                        finished_at=finished,
                        message="Student Village live submission aborted because the closed banner is still visible.",
                        facts=("Closed banner detected during browser preflight.",),
                        evidence_paths=evidence_paths,
                        fingerprint=execution.result.fingerprint,
                    )

                if any(marker in initial_html.lower() for marker in self.BLOCKING_MARKERS):
                    evidence_paths = self.artifacts.capture_submission(
                        site_id=execution.result.site_id,
                        reason="studentvillage_antibot_blocked",
                        metadata={
                            "reason": "blocking_marker_visible",
                            "detection_fingerprint": execution.result.fingerprint,
                            "applicant": applicant.redacted_summary(),
                        },
                        text_files=text_files,
                        binary_files=binary_files,
                    )
                    finished = utcnow_iso()
                    return SubmissionResult(
                        site_id=execution.result.site_id,
                        status=SubmissionStatus.BLOCKED,
                        mode="live",
                        attempted=False,
                        started_at=started,
                        finished_at=finished,
                        message="Student Village live submission aborted because a blocking anti-bot marker is visible.",
                        facts=("Blocking anti-bot marker observed in browser preflight.",),
                        evidence_paths=evidence_paths,
                        fingerprint=execution.result.fingerprint,
                    )

                self._fill_form(page, applicant.form_values())
                text_files["filled_form.html"] = page.content()
                binary_files["filled_form.png"] = page.screenshot(full_page=True)

                page.locator("input[type='submit'][value='Register']").click()
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                page.wait_for_timeout(2_500)

                final_url = page.url
                final_page_text = page.locator("body").inner_text()
                text_files["post_submit.html"] = page.content()
                binary_files["post_submit.png"] = page.screenshot(full_page=True)
                evidence_paths = self.artifacts.capture_submission(
                    site_id=execution.result.site_id,
                    reason="studentvillage_live",
                    metadata={
                        "reason": "live_submission_attempt",
                        "detection_fingerprint": execution.result.fingerprint,
                        "final_url": final_url,
                        "applicant": applicant.redacted_summary(),
                    },
                    text_files=text_files,
                    binary_files=binary_files,
                )
                finished = utcnow_iso()
                return SubmissionResult(
                    site_id=execution.result.site_id,
                    status=SubmissionStatus.AMBIGUOUS,
                    mode="live",
                    attempted=True,
                    started_at=started,
                    finished_at=finished,
                    message="Student Village live submission was executed. Verification is still required.",
                    facts=(
                        "Browser session preserved the server-issued form token and page JavaScript submit flow.",
                    ),
                    inferences=(
                        "Because the post-submit success criteria are not fully known yet, the result is treated as ambiguous until verified.",
                    ),
                    evidence_paths=evidence_paths,
                    final_url=final_url,
                    final_page_text=final_page_text,
                    fingerprint=execution.result.fingerprint,
                )
        except Exception as exc:  # pragma: no cover - exercised in live operation
            if page is not None:
                try:
                    text_files["error_page.html"] = page.content()
                    binary_files["error_page.png"] = page.screenshot(full_page=True)
                except Exception:
                    pass
            evidence_paths = self.artifacts.capture_submission(
                site_id=execution.result.site_id,
                reason="studentvillage_live_failed",
                metadata={
                    "reason": "exception",
                    "error": str(exc),
                    "detection_fingerprint": execution.result.fingerprint,
                    "applicant": applicant.redacted_summary(),
                },
                text_files=text_files,
                binary_files=binary_files,
            )
            finished = utcnow_iso()
            return SubmissionResult(
                site_id=execution.result.site_id,
                status=SubmissionStatus.FAILED,
                mode="live",
                attempted=True,
                started_at=started,
                finished_at=finished,
                message="Student Village live submission raised an exception.",
                facts=(str(exc),),
                evidence_paths=evidence_paths,
                fingerprint=execution.result.fingerprint,
            )
        finally:  # pragma: no cover - exercised in live operation
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def _fill_form(self, page, values: dict[str, str]) -> None:  # pragma: no cover - browser runtime
        for field in (
            "firstname",
            "lastname",
            "email",
            "second_email",
            "address",
            "zipcode",
            "city",
            "country",
            "phonenumber",
            "dob",
            "roomnumberonregister",
            "nationality",
            "studentfaculty",
            "username",
        ):
            if values.get(field):
                page.locator(f'input[name="{field}"]').fill(values[field])

        page.locator('textarea[name="parents"]').fill(values.get("parents", ""))
        page.locator('textarea[name="comments"]').fill(values.get("comments", ""))
        page.locator('input[name="password"]').fill(values["password"])
        page.locator('input[name="confirmpwd"]').fill(values["password"])
        page.locator(f'input[name="spokenlanguage"][value="{values["spokenlanguage"]}"]').check()
        page.locator(f'input[name="gender"][value="{values["gender"]}"]').check()
        page.evaluate(
            """
            ({ value }) => {
                const field = document.querySelector('input[name="request_rentaldate"]');
                if (field && value) {
                    field.value = value;
                }
            }
            """,
            {"value": values.get("request_rentaldate", "--")},
        )

