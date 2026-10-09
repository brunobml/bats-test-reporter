# Bats Test Reporter

A cloud-native, GitOps-managed observability viewer and dashboard for Bats test results across the GitOps Control Plane lab.

## Overview

The Bats Test Reporter ingests JUnit XML test reports generated during smoke and integration test runs (e.g., `make test`), merges them across fast transient storage (**Moto S3**) and durable long-term storage (**GitHub Git Archive**), and serves both a responsive web UI and a structured JSON / Prometheus metrics API.

```
+-----------------------------------------------------------------------------------+
| Host Test Execution (make test / scripts/smoke-test-hub-spoke-bats.sh)             |
|                                                                                   |
|  1. Bats Runner (exit code preserved unconditionally)                            |
|  2. sanitize_report.py (fail-closed secret scrubbing, 8KiB bounds, SHA-256)      |
|  3. publish-bats-report.sh                                                        |
+--------------------------+--------------------------------+-----------------------+
                           | (instant upload)               | (git commit & push)
                           v                                v
                  +-----------------+              +--------------------+
                  |  Moto Cloud S3  |              |   GitHub Archive   |
                  | gitops-lab-rpts |              |  bats-test-results |
                  +--------+--------+              +---------+----------+
                           |                                 |
                           +----------------+----------------+
                                            |
                                            v
                  +-----------------------------------------------------+
                  |   bats-test-reporter Pod (spoke-nonprod)            |
                  |                                                     |
                  |   FastAPI Service:                                  |
                  |   - S3 Storage Adapter (path-style, 15s cache)      |
                  |   - Git Storage Adapter (shallow clone, 300s cache) |
                  |   - Merged Store (SHA-256 checksum verification)    |
                  |   - Low-cardinality Prometheus /metrics             |
                  |   - Responsive UI (/), REST API (/api/runs)         |
                  +--------------------------+--------------------------+
                                             |
                                             v
                  +-----------------------------------------------------+
                  | Traefik Ingress: http://bats-reports.localhost:8081  |
                  +-----------------------------------------------------+
```

---

## Key Features

1. **Dual Storage Engine**:
   - **Fast Tier (Moto S3)**: Immediate availability for recent test runs.
   - **Durable Tier (GitHub `bats-test-results`)**: Public git repository providing persistent history across Moto teardowns or lab rebuilds.
   - **Checksum Verification**: Verifies SHA-256 digests between S3 and Git sources to detect synchronization drift.

2. **Security & Compliance**:
   - **Zero Secret Exposure**: Sanitizer (`scripts/lib/sanitize_report.py`) strips AWS keys, bearer tokens, passwords, private keys, local user home paths, and internal hostnames.
   - **PodSecurity Standards `restricted`**: Container runs as non-root UID `10001`, with a read-only root filesystem, dropped Linux capabilities (`drop: ["ALL"]`), and `allowPrivilegeEscalation: false`.
   - **Fail-Closed Gate**: If any sensitive credential remains detectable post-sanitization, report publishing aborts immediately and alerts the operator.

3. **Kubernetes-Native Declarative Provisioning**:
   - Packaged as a **Kro ResourceGraphDefinition** (`TestReportViewer`).
   - S3 Bucket managed declaratively via AWS Controllers for Kubernetes (**ACK S3**).
   - Deployed and synchronized continuously via **Argo CD** ApplicationSets.

---

## Endpoints

| Path | Method | Description |
|---|---|---|
| `/` | GET | Interactive Web Dashboard (filter by run, gate-level view) |
| `/api/runs` | GET | List all runs sorted reverse-chronologically with metadata |
| `/api/runs/{run_id}` | GET | Get full run details including suite breakdown |
| `/api/runs/{run_id}/cases`| GET | Flat list of individual test cases with pass/fail status |
| `/metrics` | GET | Low-cardinality Prometheus metrics for alerting and dashboards |
| `/healthz` | GET | Liveness probe endpoint |
| `/readyz` | GET | Readiness probe endpoint |

### Example Prometheus Metrics (`/metrics`)

```text
# HELP bats_last_run_timestamp_seconds Unix timestamp of the most recent Bats run
# TYPE bats_last_run_timestamp_seconds gauge
bats_last_run_timestamp_seconds 1791529484.0

# HELP bats_last_run_status Overall status of the most recent run (1=passed, 0=failed)
# TYPE bats_last_run_status gauge
bats_last_run_status 1

# HELP bats_last_run_tests Test counts by result in the most recent run
# TYPE bats_last_run_tests gauge
bats_last_run_tests{result="passed"} 31
bats_last_run_tests{result="failed"} 0
bats_last_run_tests{result="skipped"} 0

# HELP bats_test_last_result Latest result for each test gate (1=passed, 0=failed/skipped)
# TYPE bats_test_last_result gauge
bats_test_last_result{suite="01_infra_and_control_plane.bats",test="Gate 1: Moto Cloud API is responding at http://localhost:5000"} 1
bats_test_last_result{suite="05_supply_chain_admission.bats",test="Gate 11a: Kyverno image verification policy tenant-images-signed is enforcing Deny..."} 1
```

---

## Verification & Usage

### 1. Execute Smoke Tests

Run the full 31-gate test suite:

```bash
make test
```

Or run a single test gate:

```bash
./scripts/smoke-test-hub-spoke-bats.sh -f "Gate 1:"
```

The test runner:
- Preserves the Bats exit code unconditionally.
- Generates a sanitized JUnit XML and metadata JSON in `~/.config/gitops-lab/reports/staging/<run_id>/`.
- Dual-publishes to Moto S3 bucket `gitops-lab-reports` and pushes to GitHub `bats-test-results`.

### 2. View in Browser

Access the dashboard via the spoke Traefik ingress:

```text
http://bats-reports.localhost:8081
```

*(Or use curl with the Host header)*:

```bash
curl -s -H "Host: bats-reports.localhost" http://localhost:8081/api/runs | jq '.[0]'
```

### 3. S3 Outage & Retry Queue

If Moto S3 is down during test execution:
- The runner completes and Git push succeeds.
- The failed S3 upload is queued in `~/.config/gitops-lab/reports/retry-queue/queue.jsonl`.
- The dashboard continues serving historical runs from the Git cache.
- Drain the retry queue once Moto is recovered:

```bash
./scripts/publish-bats-report.sh --retry-queue
```

---

## Repository Structure

- `src/`
  - `main.py` - FastAPI app, routing, templates, and metrics.
  - `config.py` - Environment configuration (S3 credentials, Git clone paths).
  - `parser.py` - JUnit XML and metadata parser.
  - `storage/`
    - `base.py` - Storage interface contracts.
    - `s3.py` - Boto3 S3 client (path-style addressing).
    - `git.py` - Shallow-clone git repository reader.
    - `merger.py` - Cache deduplication and checksum verification.
  - `templates/`
    - `index.html` - Responsive HTML dashboard.
- `tests/` - Pytest unit and integration test suite.
- `Dockerfile` - Security-hardened multi-stage container build (`nonroot:10001`).
