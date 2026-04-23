from __future__ import annotations

from src.config.models import AppConfig
from src.submitter.base import SubmissionResult, SubmissionStatus
from src.verifier.base import VerificationResult, VerificationStatus


class RuleBasedVerifier:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def verify(self, site_id: str, submission_result: SubmissionResult) -> VerificationResult:
        if submission_result.status in {SubmissionStatus.SKIPPED, SubmissionStatus.DRY_RUN}:
            return VerificationResult(
                site_id=site_id,
                status=VerificationStatus.NOT_APPLICABLE,
                message="Verification not applicable for skipped or dry-run submissions.",
            )

        if submission_result.status in {SubmissionStatus.FAILED, SubmissionStatus.BLOCKED}:
            return VerificationResult(
                site_id=site_id,
                status=VerificationStatus.FAILED,
                message="Submission failed before a positive verification signal could be checked.",
                facts=(submission_result.message,),
            )

        page_text = submission_result.final_page_text.lower().strip()
        success_phrases = tuple(phrase.lower() for phrase in self.config.studentvillage_success_phrases)
        failure_phrases = tuple(phrase.lower() for phrase in self.config.studentvillage_failure_phrases)

        if success_phrases and any(phrase in page_text for phrase in success_phrases):
            return VerificationResult(
                site_id=site_id,
                status=VerificationStatus.CONFIRMED,
                message="A configured success phrase was detected in the post-submit page.",
                facts=tuple(f"Matched success phrase: {phrase}" for phrase in success_phrases if phrase in page_text),
            )

        if failure_phrases and any(phrase in page_text for phrase in failure_phrases):
            return VerificationResult(
                site_id=site_id,
                status=VerificationStatus.FAILED,
                message="A configured failure phrase was detected in the post-submit page.",
                facts=tuple(f"Matched failure phrase: {phrase}" for phrase in failure_phrases if phrase in page_text),
            )

        return VerificationResult(
            site_id=site_id,
            status=VerificationStatus.AMBIGUOUS,
            message="No configured success or failure phrase matched the post-submit page.",
            inferences=(
                "A human review of the saved artifacts is required before treating the attempt as successful.",
            ),
        )
