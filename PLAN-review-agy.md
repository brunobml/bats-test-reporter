# Bats Test Reporter: Architecture & Feasibility Review

- **Reviewer:** Antigravity (AGY)
- **Date:** 2026-10-09
- **Target Plan:** [`PLAN.md`](./PLAN.md)
- **Target Repository:** `bats-test-reporter`
- **Context:** GitOps Control Plane Lab (`gitops-control-plane`, `platform-catalog`, `tenant-workloads`)

---

## 1. Executive Summary

The proposal in [`PLAN.md`](./PLAN.md) to build a Bats test reporting pipeline and web dashboard backed by **Moto S3** and composed via **kro** and **ACK S3** is an **architecturally sound and high-value platform capability**.

It builds directly on the strengths of the current lab:
1. **Keeps Bats execution host-side**: Avoids containerizing complex multi-cluster kubeconfig credentials and host network tunnels.
2. **Standardizes on JUnit XML**: Bats natively outputs standard JUnit XML (`bats --report-formatter junit -o <dir>`), ensuring broad compatibility.
3. **Expands Platform Capabilities**: Introduces ACK S3 (`s3.services.k8s.aws`) alongside the existing SQS, IAM, EC2, and EKS controllers.
4. **Demonstrates Real GitOps Composition**: Uses kro to combine an S3 bucket, workload deployment, service, ingress, and RBAC into a single self-service Custom Resource (`TestReportViewer`).

This review endorses the project while highlighting **5 critical technical concerns** and providing concrete remediation strategies to ensure smooth implementation.

---

## 2. Technical Evaluation & Key Concerns

### Finding 1: Moto S3 DNS & Addressing Mode (Critical)

* **Analysis:**
  In Docker networking (`k3d-cloud-net`), `moto-cloud` resolves to its specific container IP (`172.21.0.7`). However, Docker's embedded DNS server does **not** support wildcard DNS.
  A live DNS resolution test on the node confirms:
  ```text
  $ docker exec k3d-spoke-nonprod-server-0 nslookup my-bucket.moto-cloud
  ** server can't find my-bucket.moto-cloud: NXDOMAIN
  ```
* **The Risk:**
  Standard AWS SDKs (AWS CLI, Go SDK, boto3) default to **virtual-hosted style** addressing (`http://<bucket>.moto-cloud:5000/...`). Any client attempting virtual-hosted calls against Moto in this network will immediately fail with `NXDOMAIN`.
* **Remediation & Guardrails:**
  1. **Enforce Path-Style Addressing**: All AWS clients (the publisher script, the Python/Go reporter app, and the ACK S3 controller) must explicitly enable path-style addressing:
     - AWS CLI / Boto3: `s3_force_path_style = true` / `endpoint_url = http://localhost:5000`
     - Go SDK: `UsePathStyle: true` or `AWS_S3_USE_PATH_STYLE=true`
     - In-cluster endpoints: `http://moto-cloud:5000/<bucket>/`
  2. **CoreDNS Wildcard Rewrite (Optional Safeguard)**:
     If virtual-hosted calls are ever emitted by third-party charts, add a CoreDNS rewrite rule in `coredns-custom`:
     ```text
     rewrite name regex (.*)\.moto-cloud moto-cloud
     ```

---

### Finding 2: Storage Architecture — S3-First vs. GitHub Dual-Storage

* **Analysis:**
  [`PLAN.md`](./PLAN.md) defines a two-tier storage model: Moto S3 for recent runs and a dedicated GitHub repository (`bats-test-results`) for durable history.
* **The Risks:**
  1. **GitHub API Rate Limits**: Unauthenticated calls to `api.github.com` are limited to **60 requests/hour** per IP address. Merging S3 with GitHub on every page load risks frequent HTTP 403 `rate limit exceeded` errors without credentials.
  2. **Secret Management Overhead**: Authenticated reads require injecting a GitHub Personal Access Token (PAT) as a Kubernetes Secret into the cluster, and authenticated writes require another PAT on the host.
  3. **High Git Churn**: Committing XML reports to Git after every smoke test run creates push contention and high repository commit noise.
* **Remediation & Guardrails:**
  * **Adopt an S3-First Progressive Delivery**:
    - **Step 1 (Core)**: Build the publisher and dashboard entirely around **Moto S3**. S3 provides instant uploads, standard object prefixes (`runs/YYYY/MM/DD/<run-id>/`), and native filtering without external API limits.
    - **Step 2 (Durable Archive)**: Treat the GitHub repository (`bats-test-results`) as an **asynchronous archive adapter** or periodic backup rather than a synchronous blocker for the dashboard UI.
    - The reporter app should function seamlessly in **S3-only mode** by default.

---

### Finding 3: Secret Leakage & Sanitization in Bats Failure Traces

* **Analysis:**
  Bats smoke tests (`04_credentials_and_sso.bats`, `01_infra_and_control_plane.bats`) test Keycloak credentials (`~/.config/gitops-lab/keycloak-tenant-a-user.password`), AWS IAM secret keys, and JWT bearer tokens.
* **The Risk:**
  When a test fails, Bats captures `stdout` and `stderr` into `<failure message="...">` and `<system-out>`. If raw JUnit XML is uploaded to S3 or pushed to GitHub, sensitive credentials could be exposed.
* **Remediation & Guardrails:**
  * Implement an automated **redaction filter** in the host-side publisher before any upload:
    - Redact patterns matching:
      - AWS Secret Access Keys (`[A-Za-z0-9/+=]{40}`)
      - Bearer tokens (`Bearer eyJ[A-Za-z0-9_-]+`)
      - Keycloak client secrets and passwords
    - Validate that redacted XML remains well-formed before dispatch.
    - If the safety check fails, quarantine the report locally and do not publish.

---

### Finding 4: Pod Security Admission (PSA) & Kyverno Compliance

* **Analysis:**
  Tenant and application namespaces on `spoke-nonprod` strictly enforce `pod-security.kubernetes.io/enforce=restricted` and run Kyverno admission policies.
* **The Risk:**
  Standard application container images often run as root or require writable root filesystems, causing immediate admission denial by Kyverno or the Kubernetes API server.
* **Remediation & Guardrails:**
  The `bats-test-reporter` Dockerfile and the kro Deployment template must adhere to the restricted security profile:
  ```yaml
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop:
        - ALL
    seccompProfile:
      type: RuntimeDefault
  ```
  Provide an `emptyDir` mount at `/tmp` for temporary cache files if needed.

---

### Finding 5: Image Distribution & Observability Integration

* **Analysis:**
  The reporter app needs a container image deployed into `k3d-spoke-nonprod`.
* **Recommendations:**
  1. **Image Loading**:
     - During local development, use `k3d image import bats-test-reporter:v0.1.0 -c k3d-spoke-nonprod` to avoid mandatory remote pushes to GHCR or Docker Hub.
     - For production GitOps, publish multi-arch images pinned by SHA-256 digest to GHCR.
  2. **Prometheus Metrics**:
     - In addition to the HTML dashboard, expose a `/metrics` Prometheus endpoint in `bats-test-reporter`:
       - `bats_test_run_status{run_id="...", suite="..."}`
       - `bats_test_gate_status{gate="...", suite="..."}`
       - `bats_test_duration_seconds{gate="..."}`
     - This allows test health and pass/fail trends to appear immediately on the existing lab Grafana dashboard!

---

## 3. Recommended Phased Implementation Roadmap

```mermaid
flowchart TD
    subgraph "Phase 1: Foundation & S3 Contract"
        P1A["1. Verify Bats JUnit output\n(tests/smoke -> report.xml)"] --> P1B["2. Redaction & Metadata Script\n(scripts/publish-bats-s3.sh)"]
        P1B --> P1C["3. Test Moto S3 Upload\n(Path-style verify)"]
    end

    subgraph "Phase 2: Reporter App Development"
        P2A["4. Build bats-test-reporter\n(Python FastAPI / Go)"] --> P2B["5. Implement S3 Adapter & Parser"]
        P2B --> P2C["6. PSA/Kyverno-compliant Dockerfile"]
    end

    subgraph "Phase 3: kro & ACK Platform Integration"
        P3A["7. Deploy ACK S3 Controller\n(gitops-control-plane)"] --> P3B["8. Author TestReportViewer RGD\n(platform-catalog)"]
        P3B --> P3C["9. Deploy Nonprod Claim\n(tenant-workloads)"]
    end

    subgraph "Phase 4: Durable Archive & Verification"
        P4A["10. GitHub Archive Adapter\n(bats-test-results repo)"] --> P4B["11. End-to-End Verification\n(make test & UI validation)"]
    end

    Phase 1 --> Phase 2 --> Phase 3 --> Phase 4
```

### Phase 1: Local Contract & S3 Publishing (Host Side)
1. Initialize Git repository in `/home/bleite/repos/bats-test-reporter`.
2. Verify JUnit XML output using `bats --report-formatter junit -o <dir> tests/smoke`.
3. Create `scripts/publish-bats-report.sh` in `gitops-control-plane` with:
   - Secret redaction filter.
   - Run metadata generator (`metadata.json`: timestamp, git commit, exit code, duration, sha256).
   - Moto S3 path-style uploader (`s3://gitops-lab-reports/runs/YYYY/MM/DD/<run-id>/`).

### Phase 2: Reporter Microservice (`bats-test-reporter`)
1. Create lightweight web service (FastAPI or Go) providing:
   - `GET /healthz`: Liveness & readiness probes.
   - `GET /api/runs`: Run history with pass/fail counts.
   - `GET /api/runs/{run_id}`: Detailed test case diagnostics.
   - `GET /metrics`: Prometheus exporter for Grafana.
   - Web UI: Clean dashboard displaying latest status and historical test runs.
2. Build non-root container image and verify locally with `k3d image import`.

### Phase 3: kro Blueprint & ACK S3 Controller
1. Register `ack-s3` in `applicationsets/addons-spoke.yaml` using chart `s3-chart` from `public.ecr.aws/aws-controllers-k8s`.
2. Configure `controllers/ack/values-s3.yaml` in `platform-catalog` with `endpoint_url: "http://moto-cloud:5000"` and path-style options.
3. Create `TestReportViewer` ResourceGraphDefinition in `platform-catalog` composing:
   - ACK S3 `Bucket` (`gitops-lab-reports`).
   - Reporter `Deployment`, `Service`, and Traefik `IngressRoute` (`http://reports.localhost`).
4. Apply the claim in `tenant-workloads` on `spoke-nonprod`.

### Phase 4: GitHub Archive & End-to-End Verification
1. Create `bats-test-results` repository on GitHub.
2. Add optional secondary archive step in the publisher script.
3. Validate complete workflow across test passes, test failures, and Moto restarts (`make restart-moto`).

---

## 4. Conclusion & Approval

The plan is **technically sound and approved for execution** with the incorporated guardrails:
* **Enforce path-style S3 addressing** to avoid Moto DNS failures.
* **Implement strict secret redaction** prior to report upload.
* **Adopt an S3-first architecture**, treating GitHub archiving as a non-blocking secondary tier.
* **Enforce restricted PodSecurity standards** in container builds and deployment manifests.
