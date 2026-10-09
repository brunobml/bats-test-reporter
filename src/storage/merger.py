"""
Storage merger and reconciler combining S3 and Git data sources.
"""

import logging
from typing import Optional
from src.parser import parse_junit_xml
from src.storage.base import RunSummary, RunDetail
from src.storage.s3 import S3StorageAdapter
from src.storage.git import GitStorageAdapter

logger = logging.getLogger(__name__)


class StorageManager:
    def __init__(self):
        self.s3_adapter = S3StorageAdapter()
        self.git_adapter = GitStorageAdapter()

    def list_all_runs(self) -> list[RunSummary]:
        """
        Gathers runs from S3 and Git, reconciles by run_id, and checks checksums.
        """
        s3_runs = {r["run_id"]: r for r in self.s3_adapter.list_runs() if "run_id" in r}
        git_runs = {r["run_id"]: r for r in self.git_adapter.list_runs() if "run_id" in r}

        all_ids = set(s3_runs.keys()) | set(git_runs.keys())
        merged = []

        for run_id in all_ids:
            s3_data = s3_runs.get(run_id)
            git_data = git_runs.get(run_id)

            has_s3 = s3_data is not None
            has_git = git_data is not None

            # Prefer git metadata if available, else s3
            base = git_data if git_data is not None else s3_data

            checksum_match = True
            if has_s3 and has_git:
                s3_sha = s3_data.get("report_sha256")
                git_sha = git_data.get("report_sha256")
                if s3_sha and git_sha and s3_sha != git_sha:
                    checksum_match = False
                    logger.error(f"Checksum mismatch for run {run_id}: S3={s3_sha} vs Git={git_sha}")

            archive_pending = has_s3 and not has_git

            summary = RunSummary(
                run_id=run_id,
                date_path=base.get("date_path", ""),
                start_time_utc=base.get("start_time_utc", ""),
                end_time_utc=base.get("end_time_utc", ""),
                bats_version=base.get("bats_version", "1.14.0"),
                suite=base.get("suite", "smoke"),
                filter=base.get("filter"),
                exit_code=base.get("exit_code", 0),
                summary=base.get("summary", {}),
                source=base.get("source", {}),
                report_sha256=base.get("report_sha256", ""),
                has_s3=has_s3,
                has_git=has_git,
                archive_pending=archive_pending,
                checksum_match=checksum_match,
            )
            merged.append(summary)

        # Sort descending by start_time_utc / run_id
        merged.sort(key=lambda r: (r.start_time_utc, r.run_id), reverse=True)
        return merged

    def get_run_detail(self, run_id: str) -> Optional[RunDetail]:
        """
        Retrieves full run detail and parsed test cases.
        Prefers Git archive as durable source; falls back to S3.
        """
        all_runs = self.list_all_runs()
        summary = next((r for r in all_runs if r.run_id == run_id), None)
        if not summary:
            return None

        metadata = None
        report_xml = None

        # 1. Try Git first
        if summary.has_git:
            metadata, report_xml = self.git_adapter.get_run_files(summary.date_path, run_id)

        # 2. Fall back to S3
        if not report_xml and summary.has_s3:
            metadata, report_xml = self.s3_adapter.get_run_files(summary.date_path, run_id)

        if not report_xml:
            return None

        # Parse XML
        suites = parse_junit_xml(report_xml)

        return RunDetail(
            **summary.model_dump(),
            report_xml=report_xml,
            suites=suites,
        )
