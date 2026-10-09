"""
Git Storage adapter reading Bats test history via shallow clone of bats-test-results.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional
from src.config import settings

logger = logging.getLogger(__name__)


class GitStorageAdapter:
    def __init__(self):
        self.repo_url = settings.GIT_REPO_URL
        self.cache_dir = Path(settings.GIT_CACHE_DIR)
        self.fetch_interval = settings.GIT_FETCH_INTERVAL_SECONDS
        self.last_fetch_time: float = 0.0

    def sync(self, force: bool = False) -> bool:
        """
        Clones or fetches latest changes from the public git repo.
        Throttled by fetch_interval unless force=True.
        """
        now = time.time()
        if not force and (now - self.last_fetch_time) < self.fetch_interval:
            return True

        try:
            if not (self.cache_dir / ".git").is_dir():
                self.cache_dir.parent.mkdir(parents=True, exist_ok=True)
                cmd = ["git", "clone", "--depth", "1", self.repo_url, str(self.cache_dir)]
                logger.info(f"Cloning {self.repo_url} into {self.cache_dir}...")
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                if res.returncode != 0:
                    logger.warning(f"Git clone failed: {res.stderr}")
                    return False
            else:
                cmd = ["git", "-C", str(self.cache_dir), "fetch", "--depth", "1", "origin", "main"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
                if res.returncode != 0:
                    logger.warning(f"Git fetch failed: {res.stderr}")
                    return False
                reset_cmd = ["git", "-C", str(self.cache_dir), "reset", "--hard", "origin/main"]
                subprocess.run(reset_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)

            self.last_fetch_time = now
            return True
        except Exception as e:
            logger.warning(f"Git sync error: {e}")
            return False

    def list_runs(self) -> list[dict]:
        """
        Lists all runs present in the local git repository cache.
        """
        self.sync()
        runs = []
        runs_dir = self.cache_dir / "runs"
        if not runs_dir.is_dir():
            return runs

        for meta_file in runs_dir.glob("*/*/*/*/metadata.json"):
            try:
                content = meta_file.read_text(encoding="utf-8")
                meta = json.loads(content)
                rel_parts = meta_file.relative_to(runs_dir).parts
                if len(rel_parts) >= 4:
                    meta["date_path"] = f"{rel_parts[0]}/{rel_parts[1]}/{rel_parts[2]}"
                runs.append(meta)
            except Exception as e:
                logger.error(f"Error reading git metadata file {meta_file}: {e}")

        return runs

    def get_run_files(self, date_path: str, run_id: str) -> tuple[Optional[dict], Optional[str]]:
        self.sync()
        run_dir = self.cache_dir / "runs" / date_path / run_id
        meta_file = run_dir / "metadata.json"
        xml_file = run_dir / "report.xml"

        if meta_file.is_file() and xml_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                xml = xml_file.read_text(encoding="utf-8")
                return meta, xml
            except Exception as e:
                logger.warning(f"Error reading files for git run {run_id}: {e}")

        return None, None
