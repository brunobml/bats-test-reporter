# Implementation record 01 — Claude review fixes

- **Date:** 2026-10-09
- **Review:** [IMPLEMENTATION-review-claude.md](./IMPLEMENTATION-review-claude.md)
- **Scope:** `bats-test-reporter`, `gitops-control-plane`, `platform-catalog`, and the repo-scoped publisher key for `bats-test-results`
- **Status:** Implemented and exercised in nonprod. This is an implementer record, **not independent validation**.

## Blocking findings

| Finding | Resolution |
| --- | --- |
| F-1 ACK S3 panic and false kro readiness | Traced the panic to ACK S3 1.13.0 `addPutFieldsToSpec` dereferencing the nil `GetBucketEncryption` response when Moto returns `ServerSideEncryptionConfigurationNotFoundError`. The RGD now requests AES256 encryption, adopts the retained bucket, and gates readiness on `ACK.ResourceSynced=True`. A host recovery script seeds encryption in Moto before ACK resumes; the installed controller version still needs this Moto compatibility step. |
| F-2 Local, unsigned image | GitHub Actions builds and keylessly signs the reporter image. The claim uses `ghcr.io/brunobml/bats-test-reporter@sha256:a1abfb925a6158a1a8c16bd01e8f0a129d09c1f2615211468aaa18731ec92489`. Anonymous registry access and the signature were verified. The pod rolled out from that digest. |
| F-3 Truncation before redaction | Full diagnostic text is redacted and normalized before the 8 KiB cap; a partial redaction marker at the boundary is removed. Final scanning includes raw, base64, and URL-encoded known values, credential patterns, hostname, and home path. A boundary regression test passes. |
| F-4 Missing secret sources and fail-open reads | The sanitizer reads lab credential files recursively, local raw kubeconfig, Argo CD cluster config, and three worker Secrets. Every required read fails closed; the raw XML is quarantined with mode 600. A stubbed `kubectl` failure test passes. |
| F-5 Account-wide Git SSH key | A write-enabled deploy key was added only to `bats-test-results`; the local results repo pushes through the `github-bats-results` SSH alias with `IdentitiesOnly yes`. The publisher refuses another push remote. |
| F-6 Unbounded S3 calls and wrong-account fallback | STS and both S3 uploads have connect/read/wall timeouts and one attempt. The default-account fallback is removed. Git fetch/push has timeouts. A failed destination stays in the retry queue. |

## Other review findings

- **S-1/S-2:** `post-bootstrap` provisions a separate Moto IAM user and Kubernetes Secret for the viewer; the Deployment reads it. The readiness probe now uses `/readyz`.
- **S-3:** `/metrics` remains available through the viewer service, but is **not** claimed as scraped by Prometheus. A scrape job is a separate observability task.
- **S-4/S-5:** Local report and quarantine directories and files were restricted to owner access. The existing quarantined XML was kept for owner review. Sanitizer tests run in `make ci`.
- **S-6/S-7:** Gate 14 checks viewer readiness, prior results, bucket sync, and account. Metadata records `caller`. `make moto-restart` now checks fresh S3 sync and absence of the report bucket from Moto's default account. The full recovery passed.
- **S-8/S-9/S-10:** kro RBAC verbs are explicit. Publication prints an incomplete status when either destination fails. The publisher requires `main`, checks commit errors, serializes pushes, and confirms local and remote HEAD match.
- **S-11/S-12/S-13:** The retry command is `scripts/publish-bats-report.sh --retry`; the queue is `~/.config/gitops-lab/reports/retry-queue.jsonl`. The RGD documents the lab's fixed Moto CIDR and unrestricted HTTPS egress limitation of standard NetworkPolicy. The claim has `retentionDays: 14` (reserved, not yet enforced).

## Evidence collected

- `make ci` passed: 47 Applications rendered, 700 Kubernetes resources checked with no invalid resources, plus shell, secrets, policy, doc, and publisher stages.
- GitHub control-plane CI [37904308723](https://github.com/brunobml/gitops-control-plane/actions/runs/37904308723) passed after the sanitizer fixtures were made independent of the developer host.
- `make ci-catalog` equivalent targeted stages passed: CEL, RGD compatibility, render, and kubeconform (427 resources, no invalid resources).
- Reporter `pytest`: 8 passed. Sanitizer unit tests: 8 passed.
- GitHub Actions run [37902416622](https://github.com/brunobml/bats-test-reporter/actions/runs/37902416622) built and signed the image; `cosign verify` succeeded for its workflow identity; an unauthenticated GHCR manifest request returned HTTP 200.
- `make moto-restart` completed. ACK Bucket was `ResourceSynced=True` in account `111111111111`; the default account contained no report bucket. All 33 Bats smoke tests passed, and run `20261009T081409Z-c25de1` published to S3 and GitHub.
- The dashboard displayed eight archived runs after Moto reset and the new S3 run. Replaying the queued publish for that run left the results repo HEAD unchanged and emptied the queue.
- An isolated ACK S3 Bucket with `deletion-policy: retain` was deleted as a Kubernetes resource; its Moto bucket remained and was then cleaned up.

## Remaining limits and independent validation

- ACK S3 1.13.0 still contains the nil-response bug; the Moto recovery seed is a lab compatibility workaround. Recheck against a newer upstream controller before using real S3.
- `platform-reports` is anonymous and does not opt into the tenant image-signature admission policy. The image is signed and pinned, but admission does not enforce that signature. Native NetworkPolicy also cannot restrict GitHub egress by hostname.
- `retentionDays` is reserved in the claim but has no deletion automation. The existing quarantined raw report remains on disk for owner review.
- Independent validation of the final plan remains pending, as requested by the review.
