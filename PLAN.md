# Bats Test Reporter: implementation plan

## Goal

Show the GitOps lab's Bats smoke-test results in a web dashboard. Read recent
JUnit XML reports from Moto S3 and durable history from a dedicated GitHub
`bats-test-results` repository. Use kro and ACK S3 to provision the dashboard's
Kubernetes workload and bucket. Keep test execution on the lab host, where the
current suite has access to the required kubeconfig contexts and localhost URLs.

## Repository ownership

| Repository | Responsibility |
| --- | --- |
| `bats-test-reporter` | Dashboard source, image build, report parser, API, UI, and tests. |
| `bats-test-results` | Immutable JUnit XML reports and a small metadata file per run. No application code or credentials. Create this repository before wiring up publishing. |
| `gitops-control-plane` | Host-side Bats publisher, ACK S3 addon ApplicationSet entry, and lab verification. |
| `platform-catalog` | ACK S3 controller values and the kro `TestReportViewer` ResourceGraphDefinition. |
| `tenant-workloads` | A `TestReportViewer` claim for the nonprod spoke. |

## Data flow

1. The existing host-side runner runs `bats --report-formatter junit --output <run-dir>`.
   Preserve Bats' exit status even when publishing is attempted after failures.
2. The publisher creates one UTC run ID and writes `report.xml` plus
   `metadata.json` (`run_id`, start/end time, suite, source revision, exit code,
   report SHA-256). It validates the XML and scans diagnostics for secrets before
   either upload. A failed safety check leaves the report local and reports why.
3. Upload the pair to Moto S3 under `runs/YYYY/MM/DD/<run-id>/`. Commit the same
   pair to `bats-test-results` at the same path, then push. Never overwrite an old
   run. If either destination fails, retain the local files and make a retry use
   the same run ID and checksum.
4. The dashboard lists recent S3 objects and archived GitHub paths, merges them
   by run ID, and prefers the GitHub copy when both are present. Mark the archive
   state explicitly if the GitHub push is still pending. Cache GitHub listings so
   each page load does not clone the repository or exhaust API limits.
5. Parse JUnit XML into run, suite, case, status, duration, and failure details.
   Show latest status, run history, per-test history, and a link to the original
   report. Escape all report text in the UI and limit XML/object sizes.

The browser calls only the reporter API. S3 and GitHub credentials stay on the
server. S3 is a recent-results cache; Moto loses its in-memory data after a
restart, so GitHub remains the source for durable history.

## Delivery phases

### 1. Reporting contract and local proof

- Confirm the installed Bats version and generate a sample JUnit report from
  `tests/smoke/` using `--report-formatter junit --output`.
- Define the immutable run path and metadata schema above. Ensure XML from
  failed runs is published without changing the test command's exit code.
- Create the private `bats-test-results` repository with a README documenting
  the layout and retention policy. Do not commit credentials or raw shell logs.
- Implement a host-side publish command in `gitops-control-plane` that uploads
  to Moto S3 and commits/pushes to the results repo. Add an explicit retry mode.
- Check that the installed Moto build handles the chosen S3 operations and that
  the publisher can use the lab's nonprod account and endpoint.

### 2. Reporter app

- Build a read-only web service in this repository with `/healthz`, an API for
  runs and cases, and a compact dashboard for latest status and history.
- Add separate S3 and GitHub adapters behind one run model. Support S3-only,
  GitHub-only, and merged operation, including a Moto restart.
- Use a private-repo read credential for the server-side GitHub adapter; if the
  results repo is public, allow unauthenticated reads. Do not give this service
  the publisher's write credential.
- Add parser fixtures for passed, failed, skipped, malformed, and duplicate
  reports; test ordering and reconciliation by run ID and checksum.
- Produce a pinned container image and document its runtime configuration.

### 3. kro and ACK deployment

- Add the ACK S3 controller to the existing spoke addon ApplicationSet, with
  version and image pinned to a validated release. Put controller values in
  `platform-catalog`, following the existing ACK endpoint/account pattern.
- Add a `TestReportViewer` ResourceGraphDefinition in `platform-catalog` that
  creates an ACK S3 `Bucket`, reporter `Deployment`, `Service`, ingress, and
  required configuration. Use the same nonprod account mapping as the claim;
  validate bucket naming and ACK S3/Moto reconciliation before rollout.
- Add the nonprod claim in `tenant-workloads` and let Argo CD deploy it. Keep
  production out of the first rollout.
- Mount S3 read credentials and GitHub read credentials from Kubernetes Secrets;
  do not put values in Git or the kro claim. Use the existing ingress/auth
  pattern and scoped network access. The viewer needs no GitHub write access.
- Start with S3 polling/listing on refresh. Add S3-to-SQS notifications only if
  measured latency or API use makes them useful.

### 4. End-to-end verification

- Run a small Bats selection with a passing and a failing case. Verify both
  reports reach S3 and GitHub and that the original Bats exit codes survive.
- Check the dashboard's latest, historical, duplicate, and failure views.
- Restart Moto using the lab's normal recovery path; verify GitHub history
  remains visible and new S3 reports appear after recovery.
- Confirm Argo CD sync/health, ACK bucket status, and kro claim readiness on
  the nonprod spoke. Record the commands and expected output in the app README.

## Credentials and operating rules

- Publisher: use a fine-grained GitHub token restricted to `bats-test-results`
  with `Contents: read/write`, or a write-enabled deploy key for that repo.
  Store it outside the repository; never pass a token in a logged URL or shell
  argument. Give the publisher only the S3 permissions it needs.
- Reporter: use a separate `Contents: read-only` token for a private results
  repo. Keep it in a Kubernetes Secret. No token is required for a public repo.
- Validate and redact report diagnostics before publishing: Bats failures can
  contain command output, URLs, tokens, and passwords. Keep an allowlist or
  targeted redaction rules and fail closed when uncertain.
- Use a single writer or serialize result-repo pushes. On a rejected push,
  fetch/rebase and retry without changing the run path. Never force-push.
- Retain every GitHub report initially. Set a short, documented S3 retention
  period only after the archive path and recovery test work reliably.

## Acceptance criteria

- One command runs the existing host Bats suite and publishes a uniquely named
  report to Moto S3 and `bats-test-results`, including on test failure.
- The dashboard displays recent and historical runs, with no duplicate when a
  report exists in both places, and works after Moto state is reset.
- The app deploys through a kro claim backed by an ACK S3 bucket in nonprod.
- The viewer cannot write to GitHub; credentials and sensitive diagnostics do
  not enter either repository or container image.
