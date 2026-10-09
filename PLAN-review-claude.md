# Bats Test Reporter: plan review (Claude)

- **Reviewer:** Claude (Opus 5.5)
- **Date:** 2026-10-09
- **Reviewed:** [`PLAN.md`](./PLAN.md); also read [`PLAN-review-agy.md`](./PLAN-review-agy.md)
- **Method:** read against the running lab, with checks on the host and the clusters (listed in §5). Nothing in the lab was changed.

## Owner decisions (2026-10-09)

| Question | Decision | Consequence for the plan |
|---|---|---|
| Viewer access (B-4) | **Anonymous** ("it is just a lab") | Spoke ingress `http://bats-reports.localhost:8081`, no SSO, no basic auth. B-4 is closed |
| Where the claim lives (B-1) | Recommendation (below) | `gitops-control-plane/addons/bats-reports/` (namespace + `TestReportViewer` claim), deployed by its own Application `addon-bats-reports-spoke-nonprod` (spoke-nonprod only, prune and self-heal on). Not `tenant-workloads` (its template renders only `queue-backed-service`), and not `platform-config` (per-spoke settings with prune off; a viewer deserves its own health line and a clean removal) |
| When to publish (I-9) | **Immediately**, on every run | Every `scripts/smoke-test-hub-spoke-bats.sh` run publishes (`make test`, end of `post-bootstrap`, `make test-docs MODE=live-mutating`). Publishing must never change the test's exit code or block it: time-limited network calls; on failure, keep the files local and report the retry command |
| Results repository visibility | **Public** | The viewer needs **no GitHub credential** (anonymous `git fetch`); only the publisher has a write credential. Report text becomes world-readable |

**These decisions make B-3 (redaction) more important, not less:** with an anonymous viewer and a
public repository, anything that escapes redaction is public on the internet. B-3's exact-value
redaction, the hostname/path scrubbing and the fail-closed re-scan stay **mandatory**, including its
negative test.

## Verdict

**Approve with changes.** The plan's goal is sound: show smoke-test results in a dashboard, keep the tests
on the host, use kro and ACK S3 to build the viewer. So are its core rules: immutable run paths,
the Bats exit code preserved, separate read and write credentials, fail-closed handling of
diagnostics, never force-push. Four points must change before implementation starts, because the
lab will block or contradict them (§2, B-1 to B-4). The rest are improvements (§3).

## 1. What the plan gets right

- **Tests stay on the host.** The suite needs three kube contexts, `*.localhost` URLs and files in
  `~/.config/gitops-lab`; containerising it would be a project of its own.
- **GitHub as the durable store, S3 as a cache.** Moto keeps state only in memory and loses it on every
  restart, and the lab restarts often. The plan says this explicitly (Data flow, last paragraph).
- **The safety rules:** an immutable `runs/YYYY/MM/DD/<run-id>/` path; checksums in `metadata.json`;
  retries that reuse the same run ID; a single writer; never force-push; secrets kept out of Git,
  the image and the claim.
- **Least privilege by role:** the viewer gets a read-only token; only the publisher can write.
- **Small steps:** S3 polling first, SQS notifications only if measured need appears; production
  left out of the first rollout.

## 2. Blocking findings (change the plan before Phase 1)

### B-1. A `TestReportViewer` claim cannot go through `tenant-workloads` today

`tenant-workloads` registrations are data files that the `tenant-workloads-tenant-a` ApplicationSet
renders into **one chart only**: `queue-backed-service` (`applicationsets/tenant-workloads-tenant-a.yaml`,
`chart: queue-backed-service`). Its AppProject also limits destinations to `orders-*`/`tenant-*`. A new
kind of claim would need a new template, and a test viewer is not a tenant app anyway: it is a
**platform tool** that shows the platform's own smoke tests.

**Change (decided 2026-10-09):** a dedicated directory `gitops-control-plane/addons/bats-reports/`
(namespace `platform-reports` + the claim), deployed by its own Application
`addon-bats-reports-spoke-nonprod` in the `platform-addons` project (add the namespace to its
destinations). Remove `tenant-workloads` from the repository table.

### B-2. kro on the spokes runs with aggregated RBAC; the plan gives it no permissions

kro is installed with `rbac.mode: aggregation` (`platform-catalog/controllers/kro/values-kro.yaml`).
It may manage only the kinds a ClusterRole labelled `rbac.kro.run/aggregate-to-controller` grants it.
Without that, the RGD stays `Inactive` (`ControllerReady=False … cache sync timeout`) and claims get
no status at all. Lab 1, step 1 reproduces exactly this.

**Change:** add `blueprints/kro-rbac-test-report-viewer.yaml` next to the RGD, granting
`testreportviewers` (+ `/status`, `/finalizers`), `buckets.s3.services.k8s.aws`, `deployments`,
`services`, `ingresses`, `configmaps`, `serviceaccounts` and `networkpolicies` (only what the RGD
creates). Add it to the Phase 3 acceptance list.

### B-3. Diagnostics: the JUnit report really does contain test output, and regex redaction alone is not enough

Checked with Bats 1.14.0 (the installed version) and `--report-formatter junit --output <dir>`:

- **A failing test's printed output goes verbatim into `<failure>`.** A test that echoed
  `token=SECRET-VALUE-123` produced `diagnostic: token=SECRET-VALUE-123` in `report.xml`.
- **Everything written to fd 3 goes into `<system-out>`, even for passing tests.** In this lab that
  includes Gate 8's credential-expiry lines.
- **The report carries the host name and absolute file paths** (`hostname="OMEN30L"`,
  `/home/bleite/...`).

The plan's "allowlist or targeted redaction, fail closed" is the right direction. Antigravity's
40-character pattern (`[A-Za-z0-9/+=]{40}`) would also redact every 40-character Git SHA, and
would still miss short secrets.

**Change:** the lab knows its secrets, so redact by **exact value** first, then by pattern:
1. Read every file in `~/.config/gitops-lab` (and the worker IAM keys, if a test can print them),
   and replace each exact value, plus its URL-encoded and base64 forms, with `[REDACTED:<file name>]`.
2. Then apply patterns: JWT `eyJ…`, `Authorization:`/`Bearer`, `password=`, `token=`, `secret=`,
   AWS key IDs `AKIA…`/`ASIA…`.
3. Replace `$HOME` with `~` and drop or neutralise `hostname`.
4. **Fail closed:** if the result still contains any known secret value (re-scan), keep the report
   local and exit non-zero for the publish step (not for the test run).
5. Add a negative test that plants a secret value in a failing test and proves nothing leaves the host.

### B-4. Where the viewer is reachable, and who may read it (closed: anonymous, spoke ingress)

kro and ACK run only on the spokes, so the viewer runs on `spoke-nonprod`. A spoke ingress is reached
at `http://<host>.localhost:8081` and has **no SSO**; tenant apps there are open. Antigravity's
`http://reports.localhost` would hit the hub's Traefik, not the spoke. Failure diagnostics are
sensitive even after redaction (internal URLs, resource names), and "use the existing ingress/auth
pattern" does not exist on the spokes.

**Decided 2026-10-09:** anonymous access on the spoke ingress, `http://bats-reports.localhost:8081`.
Record it as an accepted residual risk in the app README (read-only results, redacted, lab only).

## 3. Important findings (fold into the phases)

| ID | Finding | Change |
|---|---|---|
| I-1 | **S3 addressing on Moto.** A bucket subdomain does not resolve on the cluster network (`nslookup bucket.moto-cloud` → NXDOMAIN); Antigravity is right. | ACK S3 chart **1.13.0** (latest) has `aws.endpoint_use_path_style`: set it `true` with `endpoint_url: http://moto-cloud:5000` and `allow_unsafe_aws_endpoint_urls: true`. Publisher and viewer: path-style in their SDK/CLI. Prove it in a Phase 1 spike *before* writing the RGD. Skip the CoreDNS wildcard rewrite unless the spike shows it is needed. |
| I-2 | **ACK after a Moto restart** (lab incidents F-6/F-7): until `make moto-restart` refreshes ACK's credentials, ACK recreates resources in the **default account 123456789012**, not 111. A bucket would reappear, empty, in the wrong account. | Phase 4 must restart Moto and recover **with `make moto-restart`**, then check the bucket's account (`status.ackResourceMetadata.ownerAccountID`). The publisher must handle "bucket missing" by keeping the report local and archiving to GitHub only. |
| I-3 | **Viewer S3 credentials have no provisioning path.** In this lab, IAM keys are created in Moto by `post-bootstrap` and stored as Secrets outside Git. Moto also does not enforce IAM policies, so "read-only" there is nominal. | Add a step to `post-bootstrap` (or a dedicated script, like `setup-policy-reporter-secrets.sh`) that creates the viewer's IAM user/key in account 111 and its Secret. Document that Moto does not enforce the policy, so read-only is a design property here, not a tested one. |
| I-4 | **Admission and the audit policies.** The `platform-reports` namespace will be under Pod Security `restricted` and the audit ValidatingPolicies: digest-pinned image, memory limit + CPU request, liveness and readiness probes, recommended labels. If it opts in to `platform.lab/image-verification`, the registry allowlist VAP also requires `ghcr.io/brunobml/…`. | Build the image in `bats-test-reporter` CI, push it to `ghcr.io/brunobml/bats-test-reporter`, sign it (cosign keyless, like orders-processor), and reference it **by digest**. Do not rely on `k3d image import` beyond local experiments. Design the RGD's Deployment to pass all audit policies from day one. |
| I-5 | **RGD schema evolution (incident D-14).** kro refuses to add constraints to an existing field later. | Design the claim's schema up front (bucket name, retention, replicas, results-repo URL, ingress host) without `minimum`/`enum` markers, and put validation in a ValidatingAdmissionPolicy versioned with the RGD, like `queuebackedservice-contract`. |
| I-6 | **CI schemas.** `platform-catalog` CI validates rendered manifests with kubeconform against `gitops-control-plane/ci/schemas/`, which has no `s3.services.k8s.aws` directory. | Add the ACK S3 CRD schemas (from the 1.13.0 chart) with the controller, or the catalog CI will fail or skip the Bucket. |
| I-7 | **Bucket lifecycle.** Deleting the claim would let kro delete the Bucket CR, and ACK would delete the bucket. | Annotate the Bucket `services.k8s.aws/deletion-policy: retain`, as prod queues do, and say how a bucket is removed on purpose. |
| I-8 | **Network.** The viewer needs egress to `moto-cloud:5000` and, for the archive, to GitHub. | Add a NetworkPolicy to the RGD: deny ingress except from Traefik, and allow egress only to DNS, Moto and GitHub (443). |
| I-9 | **When to publish** (decided: immediately, every run). `scripts/smoke-test-hub-spoke-bats.sh` runs from `make test`, at the end of `post-bootstrap`, and from `make test-docs MODE=live-mutating`. Pushing to GitHub from all of these would create noise and make offline runs slow or fragile. | Publish from the shared runner, so every run is covered. Never let a publish failure, a missing network or a missing token change the test's exit code. Bound every network call with a timeout; queue failed publishes locally for `--retry`. |
| I-10 | **Reading GitHub history** (public repository: no credential needed). Antigravity's rate-limit concern is real for the REST API (60 requests/hour unauthenticated, 5000 authenticated), but the plan's "cache listings" leaves the mechanism open. | Have the viewer keep a shallow `git clone` of `bats-test-results` in an `emptyDir` and `git fetch` on a timer (for example every 5 minutes). No REST API calls, no rate limit; anonymous for a public repository. |

## 4. Minor points

- **Run ID:** a UTC timestamp alone can collide when two runs start in the same second; use
  `YYYYMMDDTHHMMSSZ-<6 hex>`.
- **Metadata:** also record the Bats version, the selected test files/filters, the lab
  `gitops-control-plane` commit, and `platform-catalog` revisions per spoke (from
  `clusters/blueprint-revisions.env`). Without them, history cannot explain a change in results.
- **Report size:** cap per-test output (for example 8 KB) in the publisher, not only in the viewer.
- **Metrics:** Antigravity proposes `bats_test_run_status{run_id=…}`. A label per run creates a new
  time series per run (unbounded cardinality). Export instead
  `bats_last_run_timestamp_seconds`, `bats_last_run_tests{result="pass|fail|skip"}` and
  `bats_test_last_result{test="<gate>"}` (bounded by the number of gates). Optional, after the core works.
- **Retention:** "retain every GitHub report" is fine at the current scale (~5 KB per run). Note the
  growth assumption (runs per day × size) in the results-repo README.
- **Repository setup:** `bats-test-reporter` has no Git repository yet (only `PLAN.md` and the reviews).

## 5. Agreement with Antigravity's review

| AGY finding | My position |
|---|---|
| 1. Moto S3 path-style | **Agree.** Verified NXDOMAIN; the concrete fix is the ACK chart value `aws.endpoint_use_path_style` (I-1). |
| 2. S3-first, GitHub as secondary | **Partly disagree.** Building and testing S3 first is fine. But making the dashboard **S3-first in function** would lose all history on every Moto restart, which happens on every lab restart. Keep the plan's model: GitHub is durable truth, S3 is a cache. Fix the rate-limit concern with git fetch, not API calls (I-10). |
| 3. Secret redaction | **Agree on need; disagree on method.** Exact-value redaction from the lab's secret files, then patterns (B-3); the 40-character regex over-redacts SHAs and still misses short secrets. |
| 4. Pod Security / Kyverno | **Agree, and it goes further:** the audit policies, the registry allowlist and image signing also apply (I-4). |
| 5. Image and metrics | **Partly agree.** `k3d image import` only for experiments: the registry allowlist rejects local images in opted-in namespaces. Metrics without a `run_id` label (§4). |
| Roadmap and approval | Agree with the phase order; add B-1 to B-4 before Phase 1 and the I-items to their phases. |

## 6. Checks behind this review (2026-10-09)

| Check | Result |
|---|---|
| `bats --version`; `--report-formatter junit --output` on a 3-test sample | Bats 1.14.0. One `report.xml`; failure output verbatim in `<failure>`; fd 3 lines in `<system-out>`; `hostname="OMEN30L"` and absolute paths present; exit code 1 kept |
| Tenant ApplicationSet template | renders only `chart: queue-backed-service` (B-1) |
| kro values | `rbac.mode: aggregation` (B-2) |
| `docker exec k3d-spoke-nonprod-server-0 nslookup bucket.moto-cloud` | NXDOMAIN; `moto-cloud` → 172.21.0.7 (I-1) |
| ACK S3 chart | latest 1.13.0; values `aws.endpoint_url`, `allow_unsafe_aws_endpoint_urls`, `endpoint_use_path_style` (I-1) |
| `ci/schemas/` in gitops-control-plane | no `s3.services.k8s.aws` (I-6) |
| CARM map `ack-system/ack-role-account-map` | account → role mapping shared by all ACK controllers; usable for S3 unchanged |

## 7. Decisions for the owner

All four were answered on 2026-10-09; see "Owner decisions" at the top.
