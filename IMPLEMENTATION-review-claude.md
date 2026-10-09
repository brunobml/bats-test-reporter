# Bats Test Reporter: Implementation Review

- **Reviewer:** Claude
- **Date:** 2026-10-09
- **Baseline:** [FINAL-PLAN.md](./FINAL-PLAN.md) (owner approval still marked pending)
- **Reviewed:**
  - `gitops-control-plane` 83b8f79, f523125, ba58426
  - `platform-catalog` 79f3860
  - `bats-test-reporter` up to 3eb54b0
  - the live `platform-reports` namespace on `spoke-nonprod`
  - the public `bats-test-results` repo (7 runs)
- **Verdict:** Changes required. Phases 1–3 exist and the dashboard works, but
  the bucket never reconciles, the image skips the plan's supply-chain
  requirements, and the sanitizer has a partial-leak path. Fix F-1 to F-6
  before independent validation.

This is a review, not a validation. The workflow still needs an implemented-NN
record from the implementer and an independent validation by someone who
neither implemented nor reviewed this.

## What was verified as working

- `http://bats-reports.localhost:8081` returned these results:

  | Endpoint | Result |
  | --- | --- |
  | `/api/runs` | 7 runs, each with `has_s3: true`, `has_git: true`, `checksum_match: true`, `archive_pending: false` |
  | `/readyz` | 200 |
  | `/metrics` | 36 `bats_*` series |

- No known lab secret value leaked. All 16 values from `~/.config/gitops-lab`
  were checked against the 15 files in `bats-test-results`, as raw values and
  base64 forms: 0 matches. A grep for the home path and the hostname also
  found nothing.
- The runner keeps the Bats exit code (`|| bats_exit=$?`, the publisher
  called with `|| true`, then `exit "${bats_exit}"`).
- The `platform-reports` namespace enforces restricted Pod Security. The pod
  security context matches the plan.
- The kro RBAC is aggregated, and the bucket carries the `retain` annotation.
- Both repos are pushed. Prod (`v1.16.0`) does not contain the RGD.

## Fix before validation

### F-1 ACK S3 never reconciles the Bucket (blocking)

`ack-s3-controller-s3-chart` (chart 1.13.0) panics on every reconcile of
`platform-reports/gitops-lab-reports`:

```
runtime error: invalid memory address or nil pointer dereference
bucket.(*resourceManager).addPutFieldsToSpec   hook.go:487
bucket.(*resourceManager).customFindBucket     hook.go:227
```

What this means:

- The bucket exists in Moto account `111111111111`, but the Bucket resource
  has no `status` (no `ackResourceMetadata`, no conditions).
- kro still reports `TestReportViewer` as `READY True`, because the RGD does
  not gate on the bucket.
- So "Synced and healthy" hides a broken resource.
- The release criteria for bucket readiness, owner account, and retain-on-delete
  are unverified. This is the failure the compatibility spike (FINAL-PLAN
  kro/ACK step 1) was meant to catch.

Fix:

- Find a chart and controller version that reconciles against the pinned Moto
  build. Otherwise, isolate which Get call Moto answers empty and report it
  upstream.
- Add a `readyWhen` on the `bucket` resource in the RGD that requires
  `ACK.ResourceSynced=True`, so kro stops reporting Ready when the bucket is
  not.
- Then verify `status.ackResourceMetadata.ownerAccountID == 111111111111` and
  the retain behavior.

### F-2 Image is not published, signed, or pinned (blocking)

The claim uses `bats-test-reporter:v0.1.0`, which was imported into the
cluster with `k3d image import`.

Consequences:

- After `make rebuild`, the pod goes into ImagePullBackOff.
- The `require-image-digest` audit policy flags it.
- FINAL-PLAN says a local import is only a development shortcut.

Fix:

1. Push the image to `ghcr.io/brunobml/bats-test-reporter`.
2. Make the GHCR package public. New packages are private, so anonymous pulls
   fail.
3. Sign it with the lab's cosign workflow.
4. Set the claim's `image` to `ghcr.io/...@sha256:<digest>`.

Also decide whether `platform-reports` opts in to
`platform.lab/image-verification`. If it does, extend `tenant-images-signed`
to cover this image. If not, record that the signature is not enforced.

### F-3 Sanitizer truncates before redacting (security)

In `scripts/lib/sanitize_report.py`, `sanitize_text`, the 8 KiB cut runs
before exact-value redaction. When a secret straddles the cut, its leading
part survives. The fail-closed re-scan only looks for complete values, so it
misses that part.

Fix:

- Redact, normalize paths and hostname, then truncate.
- Extend the final re-scan to cover:
  - base64 and URL-encoded forms;
  - the regex patterns;
  - the home path and the hostname.
- Add a unit test with a secret placed across the 8 KiB boundary.

### F-4 Secret discovery has gaps and fails open (security)

Gaps in `load_known_secrets()`:

- It reads only top-level files whose names match
  `.password`/`.secret`/`token`/`key`, so `tls/` (private keys) is skipped.
- The Argo CD spoke bearer tokens and kubeconfig tokens are never loaded.

It also fails open. Every `kubectl` read of
`orders-{dev,test,prod}-aws` is wrapped in `except Exception: pass`. If the
cluster is unreachable, the worker IAM keys drop out of the redaction list
silently and publishing continues. This is the same fail-open class that
validations 04/05 rejected (V3-1, V3-2).

Fix:

1. Walk the config directory recursively, and include PEM private keys and
   the cluster tokens.
2. Make the secret-source list explicit and documented.
3. Treat a failed read of any listed source as a sanitization failure:
   quarantine, send nothing.
4. Add a stubbed negative test where `kubectl` fails.

### F-5 GitHub push uses the owner's account-wide SSH key (security)

`bats-test-results` is pushed via `git@github.com:brunobml/bats-test-results.git`
with the owner's default SSH identity, which can write to every repo.
FINAL-PLAN requires one-repo write access.

Fix:

- Create a deploy key with write access on `bats-test-results` only.
- Store the private key under `~/.config/gitops-lab` with mode 600.
- Use it via a `~/.ssh/config` Host alias, e.g. `github-bats-results`, with
  `IdentitiesOnly yes`, and set the remote to that alias.

### F-6 S3 upload has no timeouts and a wrong-account fallback

Two problems in `upload_to_s3` (`scripts/publish-bats-report.sh`):

- **No timeout.** The header comment says the timeout is bounded, but no
  timeout is set. The AWS CLI defaults are a 60 s connect timeout, a 60 s read
  timeout, and retries. A hung Moto can stall every `make test` and
  `post-bootstrap` for minutes.
- **Wrong-account fallback.** When `sts assume-role` fails, the function
  falls back to `mock-key`, which targets the default account
  `123456789012`. That is the account FINAL-PLAN says to check for
  accidental buckets.

Fix:

- Pass `--cli-connect-timeout 5 --cli-read-timeout 20`, or wrap the call in
  `timeout 30s`, on the STS call and on each `s3 cp`.
- Add a timeout to `git fetch`.
- Remove the fallback. A failed assume-role marks S3 as failed and queues a
  retry.

## Should fix

| ID | Finding | Suggested fix |
| --- | --- | --- |
| S-1 | The viewer has no S3 credential. `src/config.py` defaults to `mock-key`. Reading the bucket in account 111 works only because Moto does not isolate accounts. FINAL-PLAN kro/ACK step 6 (viewer credential) was not done. | Add the planned secret script and mount a Secret, or record this explicitly as an accepted Moto shortcut. |
| S-2 | The RGD readiness probe uses `/healthz`, although the app implements `/readyz`. | Point `readinessProbe` at `/readyz`. |
| S-3 | `/metrics` is unreachable for Prometheus. The NetworkPolicy allows ingress only from Traefik and the same namespace, and no scrape job exists. | Allow `monitoring` on port 8080 and add a scrape job, or drop the "published for Prometheus" claim. |
| S-4 | `~/.config/gitops-lab/quarantine/` and `reports/` are `755`. The quarantined raw report is `644`, and one exists (`20261009T064000Z-0bc519-quarantine.xml`). | Create both directories and their files with umask 077. The owner reviews and deletes the existing quarantine file. |
| S-5 | `tests/test_sanitize_report.py` is not run by `make ci` / `ci/check-control-plane.sh`. | Add a CI stage. |
| S-6 | No smoke gate covers the viewer or the publisher. Metadata does not record the caller (`make test`, `post-bootstrap`, `test-docs`), so deliberate drill failures are indistinguishable from real ones. | Add a gate (viewer `/readyz`, latest run present, Bucket Synced). Add a `caller` field to metadata. |
| S-7 | Phase 4 recovery (`make moto-restart`, bucket back in 111, nothing in 123456789012, retry without a new run ID or checksum change) is not in the verification results. | Run it and record the commands and output. |
| S-8 | `kro-rbac-test-report-viewer.yaml` grants `verbs: ["*"]` on every kind, including cluster-wide ConfigMaps. | Narrow to the verbs kro uses: get, list, watch, create, update, patch, delete. |
| S-9 | The publisher always prints `✔ Published`, even when both stores failed. | Print a failure line, and make the `make test` output show it. |
| S-10 | `git commit ... \|\| true` followed by `git push origin main` can report success when nothing was pushed: a commit error, or the results clone not on `main`. | Check out `main` explicitly. Fail when the commit fails. Compare `HEAD` with `origin/main` after the push. |
| S-11 | The delivery report names the retry option and path wrongly. | Correct it: the option is `--retry` (not `--retry-queue`) and the queue is `retry-queue.jsonl` (not `retry-queue/`). |
| S-12 | The Moto egress rule hard-codes `172.21.0.0/16`, and port 443 is open to `0.0.0.0/0`. | Derive the CIDR from the Docker network, or document it. Record that the CNI cannot restrict egress by hostname, as FINAL-PLAN asks. |
| S-13 | The claim has no `retention` field, which FINAL-PLAN listed among the claim fields to design up front. | Add it now, even if unused, to avoid an incompatible schema change later. |

## On "why the app was provisioned on spoke-nonprod"

The explanation is mostly accurate:

- the hub runs neither kro nor ACK;
- `spoke-nonprod` follows `main`;
- host port 8081 is the anonymous spoke-nonprod ingress.

Corrections:

- **The deciding reason is missing.** FINAL-PLAN puts production rollout out
  of scope for the first release. The viewer shows lab-wide results, so one
  instance is enough, and nothing about it is environment-specific.
- **The placement is hard-coded, not a policy outcome:**
  - `addon-bats-reports-spoke-nonprod` targets the nonprod server directly;
  - `addons-spoke-s3` selects only `environment: nonprod`.
- **The note that prod only needs the next catalog tag is incomplete.** A
  prod viewer would also need:
  - the ACK S3 ApplicationSet extended to prod;
  - prod's account mapping;
  - a different bucket name.

## Next steps

1. Owner approves FINAL-PLAN, or records that this implementation supersedes
   the pending approval.
2. The implementer fixes F-1 to F-6, then the S items, and writes an
   implemented-NN record.
3. An independent validator runs FINAL-PLAN's release acceptance, including
   the planted-secret test (failing output and fd 3), Moto restart recovery,
   and Bucket readiness.
