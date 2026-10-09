# Bats Test Reporter — Final Implementation Plan

## Review and approval history

| Date | Actor | Record | Outcome |
| --- | --- | --- | --- |
| 2026-10-09 | Initial proposal | [PLAN.md](./PLAN.md) | Baseline architecture: host-side Bats, Moto S3 for recent reports, GitHub for durable history, kro and ACK S3 for the viewer. |
| 2026-10-09 | Antigravity (AGY) | [PLAN-review-agy.md](./PLAN-review-agy.md) | Approved with guardrails for path-style S3, redaction, restricted pods, and phased delivery. |
| 2026-10-09 | Claude | [PLAN-review-claude.md](./PLAN-review-claude.md) | Approved with changes; identified four blocking lab-specific issues and ten implementation details. |
| 2026-10-09 | Owner decisions recorded in Claude's review | [PLAN-review-claude.md](./PLAN-review-claude.md#owner-decisions-2026-10-09) | Anonymous lab viewer; platform-owned claim; publish on every Bats run; public results repository. |
| 2026-10-09 | Final synthesis | This document | Incorporates both reviews and recorded decisions. **Owner approval of this final document: pending.** |

`bats-test-reporter` is now a Git repository on `main`. The review documents and
original plan remain as the decision record. This file is the implementation
baseline; approval of a review is not a claim that the final plan has been
implemented or approved by the owner.

## Outcome and boundaries

Provide a read-only dashboard at `http://bats-reports.localhost:8081` on
`spoke-nonprod`. It displays the latest Bats smoke-test result, prior runs,
individual cases, failures, and trends. Current reports arrive through Moto S3;
the public `bats-test-results` GitHub repository is the durable archive and the
source of history after Moto resets. The browser speaks only to the reporter API.

Bats stays on the lab host. Its tests use three kube contexts, host-only URLs,
and local credential files. The viewer does not execute tests. Production
rollout and S3-to-SQS notifications are outside the first release.

| Repository | Ownership |
| --- | --- |
| `bats-test-reporter` | Read-only parser, S3 and Git adapters, API, UI, container image, and app tests. |
| `bats-test-results` | Public immutable sanitized `report.xml` and `metadata.json` for each run; README with format and retention assumptions. Create this repo before archive integration. |
| `gitops-control-plane` | Host publisher and retry queue; automatic hook in the shared Bats runner; ACK S3 addon; dedicated Argo CD Application and `addons/bats-reports/` namespace and claim; CI schemas and recovery checks. |
| `platform-catalog` | ACK S3 values, `TestReportViewer` RGD, aggregated kro RBAC, and any contract validation policy. |

The `TestReportViewer` claim belongs to the platform, not
`tenant-workloads`: the current tenant ApplicationSet renders only the
`queue-backed-service` chart. A dedicated Application
`addon-bats-reports-spoke-nonprod` in the `platform-addons` project owns
`platform-reports` and the claim, with prune and self-heal enabled. Add that
namespace to the AppProject's destination list.

## Report contract and publication

1. Every call to `scripts/smoke-test-hub-spoke-bats.sh` creates a unique UTC
   run ID, for example `20261009T013000Z-a1b2c3`. Capture Bats 1.14.0 JUnit
   output with `--report-formatter junit --output <run-dir>`. Preserve the
   exact Bats exit status when the wrapper returns, including when publication
   fails. Keep the existing command-line filters and options working.
2. Build one sanitized `report.xml` and `metadata.json`. Metadata includes
   schema version, run ID, start/end UTC times, Bats version, selected files or
   filter, Bats exit code, test counts, lab source commit, per-spoke catalog
   revision if available, and the sanitized XML SHA-256. Do not copy environment
   variables, kubeconfig, credentials, or raw shell logs into metadata.
3. Parse and minimize JUnit content **before it leaves the host**. Include
   case names, outcomes, timings, and bounded diagnostic text (initial cap:
   8 KiB per case). Exact-value redact secrets from the lab's known local
   secret files and relevant worker IAM keys, including encoded forms; then
   apply targeted token/password/key patterns. Neutralize the hostname and
   absolute home paths. Re-parse the resulting XML and re-scan for known
   secrets. If validation is uncertain or fails, quarantine locally and send
   nothing. Test this with a planted secret in both failing output and fd 3
   output from a passing test.
4. Use the immutable path `runs/YYYY/MM/DD/<run-id>/` in both stores. Upload
   `report.xml` and `metadata.json` to Moto S3 using **path-style** addressing.
   Commit and push the same files to `bats-test-results` using a credential
   scoped to that one repo. Never force-push or rewrite earlier runs.
5. Attempt both destinations on every run with bounded network timeouts. A
   missing Moto bucket must not prevent a GitHub archive attempt. A GitHub
   outage must not prevent S3 upload. Store failed destinations in a local
   retry queue with the original run ID and checksum; expose a manual retry
   command. Serialize Git pushes and handle non-fast-forward races by
   fetch/rebase/retry. Publishing errors are visible but never replace the
   Bats exit code.

GitHub is durable history; Moto S3 is the fast recent-results source. Build
and test S3 first, but do not consider S3-only operation a complete release:
Moto state disappears on restart. At the current expected report size (about
5 KiB per run), start with indefinite Git retention and record the observed
runs/day and repo growth in `bats-test-results/README.md`. Set S3 retention
only after archive and recovery are proven.

## Reporter application

- Implement `GET /healthz`, `GET /readyz`, `GET /api/runs`,
  `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/cases`, and the dashboard.
  Show source availability and archive-pending state, not a false empty
  history, when a backend is unavailable.
- Read S3 through a path-style client pointed at `http://moto-cloud:5000`.
  Read the public GitHub results repo with a shallow HTTPS clone in an
  `emptyDir`, then fetch on a bounded interval (initial target: five minutes).
  Serve cached history while a fetch fails. No GitHub read token or per-page
  REST API listing is needed for the public repo.
- Merge by run ID. If both sources contain the run, verify their checksums and
  show one row, preferring the Git archive as the durable copy. Surface a
  checksum conflict as an error; do not silently choose one report.
- Parse XML with entity expansion disabled, limits on object and diagnostic
  size, and escaped UI output. Test passed, failed, skipped, malformed,
  duplicate, conflicting, S3-only, GitHub-only, and post-restart cases.
- Keep the container non-root with a read-only root filesystem, dropped
  capabilities, no privilege escalation, RuntimeDefault seccomp, and a
  writable `emptyDir` only for the Git cache. Include requests/limits,
  liveness/readiness probes, and recommended labels. Build and publish to
  `ghcr.io/brunobml/bats-test-reporter`, sign it with the lab's existing
  cosign workflow, and deploy by digest. Local `k3d image import` is only a
  development shortcut.
- Add optional low-cardinality `/metrics` after the core path works:
  last-run timestamp, pass/fail/skip counts, and bounded per-gate status.
  Never label a metric with an unbounded `run_id`.

## kro, ACK, and GitOps integration

1. **Compatibility spike before the blueprint:** verify the pinned Moto build,
   ACK S3 `Bucket` CRD, chosen chart version (Claude checked 1.13.0 on
   2026-10-09), and controller-to-Moto calls. Configure
   `aws.endpoint_url`, `aws.allow_unsafe_aws_endpoint_urls`, and
   `aws.endpoint_use_path_style: true`. Use path-style addressing in the host
   publisher and viewer as well; bucket subdomains under `moto-cloud` do not
   resolve on the lab network. Pin the validated chart and image digest.
2. Add ACK S3 to the existing spoke addon ApplicationSet and put its values
   in `platform-catalog/controllers/ack/values-s3.yaml`. Add matching ACK S3
   schemas to `gitops-control-plane/ci/schemas/` so catalog validation covers
   `Bucket` resources.
3. Create `TestReportViewer` RGD with an ACK `Bucket`, reporter Deployment,
   Service, Ingress, ConfigMap if needed, and NetworkPolicy. Design the claim
   fields up front: bucket name, retention, replicas, results repo URL, and
   ingress host. Put future value checks in a versioned admission policy
   rather than adding incompatible kro schema constraints later.
4. Add `blueprints/kro-rbac-test-report-viewer.yaml` with the
   `rbac.kro.run/aggregate-to-controller` label. Grant only the claim and
   child-resource verbs/kinds the RGD actually uses, including status and
   finalizers where needed. Check RGD `ControllerReady` and claim readiness;
   this lab uses `rbac.mode: aggregation`.
5. Give the bucket the ACK deletion-policy `retain` annotation, verify its
   behavior in the spike, and document deliberate bucket removal. Use the
   nonprod account `111111111111`; verify
   `status.ackResourceMetadata.ownerAccountID` and actual S3 access.
6. Provision viewer S3 credentials through the existing post-bootstrap or a
   dedicated local secret-setup script, outside Git. Moto does not enforce IAM
   policies, so read-only permissions are a design target to verify again
   against real AWS. The public GitHub repo requires no viewer credential.
7. Label `platform-reports` for restricted Pod Security. Route the anonymous
   spoke ingress at `http://bats-reports.localhost:8081`. Apply the lab's
   image verification and audit requirements to the Deployment. Record
   anonymous access as an accepted lab-only exposure in the app README.
8. Apply NetworkPolicy for ingress from the spoke Traefik path and required
   egress to DNS and Moto. The viewer also needs outbound HTTPS to GitHub;
   verify what the installed CNI can express before claiming a hostname-level
   egress restriction. Document and test the final effective policy.

## Delivery sequence and gates

### Phase 1 — Contract, sanitization, and host publisher

- Generate real sample JUnit reports from the installed Bats suite. Validate
  failure and fd 3 behavior with controlled data.
- Run the Moto/ACK S3 path-style compatibility spike. Choose and pin the
  working chart settings before writing the RGD.
- Create the public `bats-test-results` repo and its format/retention README.
  Configure one-repo write access for the host publisher.
- Implement publication on every shared-runner invocation, bounded timeouts,
  local retry, and tests for exit-code preservation and zero outbound data
  when sanitization fails.

### Phase 2 — Reporter

- Implement adapters, parser, API, UI, checksum reconciliation, and offline
  cache behavior in `bats-test-reporter`.
- Publish a digest-pinned, signed, restricted-compliant image. Validate API
  and UI against synthetic S3 and Git fixtures before cluster rollout.

### Phase 3 — Platform deployment

- Add ACK S3 addon and CI schemas; apply the RGD, aggregated kro RBAC, and
  any contract policy in `platform-catalog`.
- Add `platform-reports` namespace and claim in
  `gitops-control-plane/addons/bats-reports/`, plus its dedicated Argo CD
  Application and AppProject destination.
- Provision the Moto S3 viewer secret. Check admission, Argo CD health,
  RGD/claim readiness, bucket account, ingress, and network access.

### Phase 4 — End-to-end and recovery

- Run representative passing and failing Bats selections. Confirm publication
  to both stores, one dashboard row per run, case details, and original Bats
  exit codes even when one destination is unavailable.
- Exercise `make moto-restart`, then verify ACK credentials recover, the
  bucket returns in account `111111111111`, GitHub history remains visible,
  and a fresh S3 report appears. Check the default account `123456789012`
  for an accidental bucket.
- Verify retry of a queued publish without creating another run or changing
  its checksum. Record reproducible verification commands and operational
  recovery steps in the reporter README.

## Release acceptance

- Every shared Bats-runner invocation attempts to publish promptly; network
  or archive failure cannot change its test exit status and is recoverable.
- No known lab secret value, hostname, or absolute home path enters a
  published report. A negative secret test proves fail-closed behavior.
- The dashboard shows recent and archived runs without duplicates and retains
  history when Moto restarts or either backend is temporarily unavailable.
- The nonprod Application, RGD, claim, ACK bucket, and viewer are Healthy or
  Ready; the bucket is in the intended account and is retained on claim
  deletion according to the validated policy.
- GitHub write access exists only on the host publisher. The anonymous viewer
  exposes sanitized, read-only lab reports at the agreed spoke URL.
