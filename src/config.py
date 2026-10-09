"""
Configuration settings for the Bats Test Reporter service.
"""

import os
from pathlib import Path


class Settings:
    # Service
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))

    # S3 Settings
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "http://moto-cloud:5000")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "gitops-lab-reports")
    S3_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    S3_USE_PATH_STYLE: bool = os.getenv("AWS_S3_USE_PATH_STYLE", "true").lower() in ("true", "1", "yes")
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "mock-key")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "mock-secret")

    # Git Archive Settings
    GIT_REPO_URL: str = os.getenv("GIT_REPO_URL", "https://github.com/brunobml/bats-test-results.git")
    GIT_CACHE_DIR: Path = Path(os.getenv("GIT_CACHE_DIR", "/tmp/bats-test-results-cache"))
    GIT_FETCH_INTERVAL_SECONDS: int = int(os.getenv("GIT_FETCH_INTERVAL_SECONDS", "300"))
    ENABLE_GIT_ARCHIVE: bool = os.getenv("ENABLE_GIT_ARCHIVE", "true").lower() in ("true", "1", "yes")

    # Diagnostic Limits
    MAX_DIAGNOSTIC_BYTES: int = 8192


settings = Settings()
