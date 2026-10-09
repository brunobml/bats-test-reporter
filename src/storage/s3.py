"""
S3 Storage adapter reading Bats reports from Moto / AWS S3 using path-style addressing.
"""

import json
import logging
from typing import Optional
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from src.config import settings

logger = logging.getLogger(__name__)


class S3StorageAdapter:
    def __init__(self):
        self.endpoint_url = settings.S3_ENDPOINT_URL
        self.bucket_name = settings.S3_BUCKET_NAME
        self.region = settings.S3_REGION
        self._client = None

    def _get_client(self):
        if self._client is None:
            config = Config(
                s3={"addressing_style": "path"},
                signature_version="s3v4",
                connect_timeout=3,
                read_timeout=3,
                retries={"max_attempts": 2}
            )
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                region_name=self.region,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                config=config,
            )
        return self._client

    def is_available(self) -> bool:
        try:
            client = self._get_client()
            client.head_bucket(Bucket=self.bucket_name)
            return True
        except Exception as e:
            logger.warning(f"S3 storage check failed: {e}")
            return False

    def list_runs(self) -> list[dict]:
        """
        Lists all runs present in S3 by scanning metadata.json objects under runs/.
        Returns list of metadata dicts.
        """
        runs = []
        try:
            client = self._get_client()
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix="runs/"):
                for obj in page.get("Contents", []):
                    key = obj.get("Key", "")
                    if key.endswith("/metadata.json"):
                        # Key format: runs/YYYY/MM/DD/<run_id>/metadata.json
                        try:
                            resp = client.get_object(Bucket=self.bucket_name, Key=key)
                            meta = json.loads(resp["Body"].read().decode("utf-8"))
                            parts = key.split("/")
                            if len(parts) >= 6:
                                meta["date_path"] = f"{parts[1]}/{parts[2]}/{parts[3]}"
                            runs.append(meta)
                        except Exception as e:
                            logger.error(f"Failed to read S3 metadata {key}: {e}")
        except ClientError as e:
            logger.warning(f"S3 listing failed for bucket {self.bucket_name}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected S3 error: {e}")
        return runs

    def get_run_files(self, date_path: str, run_id: str) -> tuple[Optional[dict], Optional[str]]:
        try:
            client = self._get_client()
            prefix = f"runs/{date_path}/{run_id}"

            meta_resp = client.get_object(Bucket=self.bucket_name, Key=f"{prefix}/metadata.json")
            metadata = json.loads(meta_resp["Body"].read().decode("utf-8"))

            xml_resp = client.get_object(Bucket=self.bucket_name, Key=f"{prefix}/report.xml")
            report_xml = xml_resp["Body"].read().decode("utf-8")

            return metadata, report_xml
        except Exception as e:
            logger.warning(f"Could not retrieve S3 run {run_id}: {e}")
            return None, None
