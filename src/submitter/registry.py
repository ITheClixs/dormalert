from __future__ import annotations

from src.config.models import SubmissionMode
from src.diagnostics.artifacts import ArtifactManager
from src.submitter.base import Submitter
from src.submitter.dry_run import DryRunSubmitter
from src.submitter.studentvillage import StudentVillagePlaywrightSubmitter


def build_submitter(site_id: str, mode: SubmissionMode, artifacts: ArtifactManager) -> Submitter:
    if mode is SubmissionMode.DRY_RUN:
        return DryRunSubmitter(artifacts)
    if mode is SubmissionMode.LIVE and site_id == "studentvillage":
        return StudentVillagePlaywrightSubmitter(artifacts)
    raise ValueError(f"No submitter available for site={site_id!r} mode={mode.value!r}")

