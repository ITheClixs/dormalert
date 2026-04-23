from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.detector.models import DetectionExecution
from src.utils.serialization import to_jsonable
from src.utils.time import age_in_days, timestamp_slug


class ArtifactManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_bundle_dir(self, site_id: str, category: str, reason: str) -> Path:
        bundle_dir = self.base_dir / site_id / category / f"{timestamp_slug()}_{reason}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        return bundle_dir

    def write_text(self, bundle_dir: Path, filename: str, content: str) -> Path:
        target = bundle_dir / filename
        target.write_text(content, encoding="utf-8")
        return target

    def write_json(self, bundle_dir: Path, filename: str, payload: Any) -> Path:
        target = bundle_dir / filename
        target.write_text(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=True), encoding="utf-8")
        return target

    def write_bytes(self, bundle_dir: Path, filename: str, content: bytes) -> Path:
        target = bundle_dir / filename
        target.write_bytes(content)
        return target

    def capture_detection(self, execution: DetectionExecution, reason: str) -> tuple[str, ...]:
        bundle_dir = self.create_bundle_dir(execution.result.site_id, "detection", reason)
        saved: list[str] = []
        saved.append(str(self.write_json(bundle_dir, "detection_result.json", execution.result)))

        for probe in execution.probes:
            saved.append(
                str(self.write_text(bundle_dir, f"{probe.target_name}.html", probe.text))
            )
            saved.append(
                str(
                    self.write_json(
                        bundle_dir,
                        f"{probe.target_name}.headers.json",
                        {
                            "requested_url": probe.requested_url,
                            "final_url": probe.final_url,
                            "status_code": probe.status_code,
                            "headers": probe.headers,
                            "duration_ms": probe.duration_ms,
                            "fetched_at": probe.fetched_at,
                        },
                    )
                )
            )

        return tuple(saved)

    def capture_submission(
        self,
        *,
        site_id: str,
        reason: str,
        metadata: dict[str, Any],
        text_files: dict[str, str] | None = None,
        binary_files: dict[str, bytes] | None = None,
    ) -> tuple[str, ...]:
        bundle_dir = self.create_bundle_dir(site_id, "submission", reason)
        saved: list[str] = [str(self.write_json(bundle_dir, "metadata.json", metadata))]

        for filename, content in (text_files or {}).items():
            saved.append(str(self.write_text(bundle_dir, filename, content)))

        for filename, content in (binary_files or {}).items():
            saved.append(str(self.write_bytes(bundle_dir, filename, content)))

        return tuple(saved)

    def prune_closed_detection_artifacts(self, retention_days: int) -> int:
        removed = 0
        detection_roots = self.base_dir.glob("*/detection/*")
        for bundle_dir in detection_roots:
            if not bundle_dir.is_dir():
                continue
            result_path = bundle_dir / "detection_result.json"
            if not result_path.exists():
                continue
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("state") != "closed":
                continue
            created_at = payload.get("timestamp_utc")
            if not created_at:
                continue
            if age_in_days(created_at) < retention_days:
                continue
            shutil.rmtree(bundle_dir, ignore_errors=True)
            removed += 1
        return removed
