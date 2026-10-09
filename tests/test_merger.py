from unittest.mock import MagicMock
from src.storage.merger import StorageManager


def test_merge_s3_and_git():
    manager = StorageManager()
    manager.s3_adapter.list_runs = MagicMock(return_value=[
        {
            "run_id": "run-001",
            "date_path": "2026/10/09",
            "start_time_utc": "2026-10-09T01:00:00Z",
            "end_time_utc": "2026-10-09T01:01:00Z",
            "exit_code": 0,
            "report_sha256": "sha-common",
            "summary": {"total": 5, "passed": 5, "failed": 0, "skipped": 0, "duration_seconds": 10.0}
        },
        {
            "run_id": "run-002-s3only",
            "date_path": "2026/10/09",
            "start_time_utc": "2026-10-09T02:00:00Z",
            "end_time_utc": "2026-10-09T02:01:00Z",
            "exit_code": 0,
            "report_sha256": "sha-s3only",
            "summary": {"total": 5, "passed": 5, "failed": 0, "skipped": 0, "duration_seconds": 10.0}
        }
    ])

    manager.git_adapter.list_runs = MagicMock(return_value=[
        {
            "run_id": "run-001",
            "date_path": "2026/10/09",
            "start_time_utc": "2026-10-09T01:00:00Z",
            "end_time_utc": "2026-10-09T01:01:00Z",
            "exit_code": 0,
            "report_sha256": "sha-common",
            "summary": {"total": 5, "passed": 5, "failed": 0, "skipped": 0, "duration_seconds": 10.0}
        },
        {
            "run_id": "run-003-gitonly",
            "date_path": "2026/10/08",
            "start_time_utc": "2026-10-08T01:00:00Z",
            "end_time_utc": "2026-10-08T01:01:00Z",
            "exit_code": 1,
            "report_sha256": "sha-gitonly",
            "summary": {"total": 5, "passed": 4, "failed": 1, "skipped": 0, "duration_seconds": 10.0}
        }
    ])

    runs = manager.list_all_runs()
    assert len(runs) == 3

    # Sorted by time descending: run-002 (02:00), run-001 (01:00), run-003 (yesterday)
    assert runs[0].run_id == "run-002-s3only"
    assert runs[0].has_s3 is True
    assert runs[0].has_git is False
    assert runs[0].archive_pending is True

    assert runs[1].run_id == "run-001"
    assert runs[1].has_s3 is True
    assert runs[1].has_git is True
    assert runs[1].archive_pending is False
    assert runs[1].checksum_match is True

    assert runs[2].run_id == "run-003-gitonly"
    assert runs[2].has_s3 is False
    assert runs[2].has_git is True


def test_checksum_mismatch():
    manager = StorageManager()
    manager.s3_adapter.list_runs = MagicMock(return_value=[{
        "run_id": "run-mismatch",
        "date_path": "2026/10/09",
        "start_time_utc": "2026-10-09T01:00:00Z",
        "end_time_utc": "2026-10-09T01:01:00Z",
        "exit_code": 0,
        "report_sha256": "sha-s3-different",
        "summary": {}
    }])
    manager.git_adapter.list_runs = MagicMock(return_value=[{
        "run_id": "run-mismatch",
        "date_path": "2026/10/09",
        "start_time_utc": "2026-10-09T01:00:00Z",
        "end_time_utc": "2026-10-09T01:01:00Z",
        "exit_code": 0,
        "report_sha256": "sha-git-different",
        "summary": {}
    }])

    runs = manager.list_all_runs()
    assert len(runs) == 1
    assert runs[0].checksum_match is False
