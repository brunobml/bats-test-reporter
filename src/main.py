"""
Main FastAPI web application for Bats Test Reporter.
"""

from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from src.config import settings
from src.storage.merger import StorageManager

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
storage = StorageManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initial sync on startup
    if settings.ENABLE_GIT_ARCHIVE:
        storage.git_adapter.sync()
    yield


app = FastAPI(title="Bats Test Reporter", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    # Ready if at least one backend is responsive
    s3_ok = storage.s3_adapter.is_available()
    git_ok = (storage.git_adapter.cache_dir / ".git").is_dir() if settings.ENABLE_GIT_ARCHIVE else True
    if not (s3_ok or git_ok):
        raise HTTPException(status_code=503, detail="No storage backends available")
    return {"status": "ready", "s3": s3_ok, "git": git_ok}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, run_id: Optional[str] = Query(None)):
    runs = storage.list_all_runs()
    selected_run = None
    if run_id:
        selected_run = storage.get_run_detail(run_id)
    elif runs:
        selected_run = storage.get_run_detail(runs[0].run_id)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "runs": runs,
            "selected_run": selected_run,
        },
    )


@app.get("/api/runs")
def list_runs():
    return storage.list_all_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    detail = storage.get_run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Run not found")
    return detail


@app.get("/api/runs/{run_id}/cases")
def get_run_cases(run_id: str):
    detail = storage.get_run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Run not found")
    all_cases = []
    for s in detail.suites:
        for c in s.cases:
            all_cases.append({"suite": s.name, **c.model_dump()})
    return all_cases


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """
    Exposes low-cardinality Prometheus metrics.
    Avoids unbounded run_id labels.
    """
    runs = storage.list_all_runs()
    if not runs:
        return "# No test runs recorded yet\n"

    latest = runs[0]
    detail = storage.get_run_detail(latest.run_id)

    lines = [
        "# HELP bats_last_run_timestamp_seconds Unix timestamp of the most recent Bats run",
        "# TYPE bats_last_run_timestamp_seconds gauge",
    ]

    import datetime
    try:
        dt = datetime.datetime.fromisoformat(latest.start_time_utc.replace("Z", "+00:00"))
        ts = dt.timestamp()
        lines.append(f"bats_last_run_timestamp_seconds {ts}")
    except Exception:
        pass

    lines.extend([
        "# HELP bats_last_run_status Overall status of the most recent run (1=passed, 0=failed)",
        "# TYPE bats_last_run_status gauge",
        f"bats_last_run_status {1 if latest.exit_code == 0 else 0}",
        "# HELP bats_last_run_tests Test counts by result in the most recent run",
        "# TYPE bats_last_run_tests gauge",
        f'bats_last_run_tests{{result="passed"}} {latest.summary.get("passed", 0)}',
        f'bats_last_run_tests{{result="failed"}} {latest.summary.get("failed", 0)}',
        f'bats_last_run_tests{{result="skipped"}} {latest.summary.get("skipped", 0)}',
    ])

    if detail:
        lines.extend([
            "# HELP bats_test_last_result Latest result for each test gate (1=passed, 0=failed/skipped)",
            "# TYPE bats_test_last_result gauge",
        ])
        for s in detail.suites:
            for c in s.cases:
                # Escape label values
                suite_name = s.name.replace('"', '\\"')
                case_name = c.name.replace('"', '\\"')
                val = 1 if c.status == "passed" else 0
                lines.append(f'bats_test_last_result{{suite="{suite_name}",test="{case_name}"}} {val}')

    return "\n".join(lines) + "\n"
