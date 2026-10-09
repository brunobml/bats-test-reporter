"""
Base data models and abstract interfaces for report storage.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field
from src.parser import TestSuite, TestCase


class RunSummary(BaseModel):
    run_id: str
    date_path: str
    start_time_utc: str
    end_time_utc: str
    bats_version: str = "1.14.0"
    suite: str = "smoke"
    filter: Optional[str] = None
    exit_code: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    report_sha256: str
    has_s3: bool = False
    has_git: bool = False
    archive_pending: bool = False
    checksum_match: bool = True


class RunDetail(RunSummary):
    report_xml: str
    suites: list[TestSuite] = Field(default_factory=list)
