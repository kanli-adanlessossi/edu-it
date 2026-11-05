# ADR 0011: Image Supply Chain

- **ID:** A11
- **Status:** Accepted
- **Date:** 2025-01-01
- **Decision topic:** Production image hardening and provenance (distroless bases, scan threshold, SBOM, keyless signing, provenance, admission policy)
- **Finalized in:** task 13.5 of the `kubernetes-deployment` spec (status `Accepted`)

## Context

The Image_Set (web, api, worker, migrations, plus lab images) runs in production on Kubernetes.
Security engineers need to trust what runs: images must be minimal, scanned, traceable to the
build that produced them, and verifiable at admission. The CI pipeline is GitHub Actions under
`.github/workflows`, and the chart references images by immutable digest (Req 7.11, 12.11). See
#[[file:.kiro/steering/deployment.md]] for the deployment rules.

Pull-request runs cannot sign (no OIDC identity with registry write), so the design must define
what is verified there and what is only verified on the default branch and release tags.

## Decision

**Distroless bases.** The final stage of each image is a distroless `nonroot` base: Node for `web`
(Next.js standalone output), Python for `api` and `worker` (dependencies installed in a virtual
environment in a builder stage). `migrations` uses the same Dockerfile as `api` with a separate
target and entrypoint. Final stages contain no shell and no package manager, and `USER` is a
numeric ID above 0. A CI check inspects each built image config and layer file list for forbidden
binaries (`sh`, `bash`, `apk`, `apt`, `apt-get`, `dnf`, `yum`, `pip`). Dockerfiles live in
`deploy/images/<name>/Dockerfile`; base image versions are pinned by ADR 0001. Images are built
for `linux/amd64` and `linux/arm64` and published as a single multi-architecture manifest; a
failure on either platform fails the build and publishes nothing from the run (Req 12.1, 12.2).
In `.github/workflows/deployment.yml` the `build` job runs once per image and platform (matrix), so a
failure names both; it builds an OCI archive and never pushes. The `publish` job needs every build
job and joins the two platform images into one multi-architecture manifest, then checks that both
platforms are present.

**Scan threshold.** Every image is scanned for vulnerabilities in CI. The build fails on any
CRITICAL or HIGH finding that has a fix available (Req 15.2); the threshold is a pipeline
configuration value (`SCAN_SEVERITY_CUTOFF: high`, with `only-fixed: true`), not a literal in the steps.
The scan runs per image and platform on the OCI archive in the `build` job, before anything is
published. A scanner error, an unavailable vulnerability
database or a scan that exceeds the pipeline scan timeout fails the build, reports the scan as
incomplete and does not publish the image (Req 12.6).

**SBOM.** After a passing scan, CI generates an SPDX JSON SBOM for each platform image and attaches
both to the multi-architecture manifest with `oras attach`, so they can be retrieved by the
manifest digest (`oras discover` confirms two SBOMs) (Req 12.7). The `publish` job does this
against the local registry; the `sign` job repeats the attach in the real registry. A failure to generate or attach fails
the build and the image is not marked released (Req 12.9).

**Keyless signing and provenance.** The `sign` job pushes the built platform images to the real
registry (ghcr.io), joins them into one manifest, signs the manifest by digest with `cosign sign`
using the OIDC keyless identity of the workflow run, and the signature is published to the registry
next to the image (Req 12.8). A SLSA provenance attestation (`slsaprovenance1`) is attached by
digest with `cosign attest` (Req 12.13). The job then verifies the signature with
`tools/verify_image_signatures.py` and the attestation with `cosign verify-attestation`, and only
then uploads the `released-<image>` marker. The `sign` job needs every verification job (tests,
chart, publish, image-refs, kind, static-checks) and runs only for a push to the default branch or
a `v*` tag. Workflow permissions are least privilege: the top-level
`permissions` block is `contents: read`, and `id-token: write` (with `packages: write` for the
push) is set only on the signing job, which runs only on the default branch and release tags
(Req 12.12). A `release-gate` job always runs after `sign` and records in the run summary whether
the images were released or why not; it fails the run when `sign` ended in anything but success or
skipped. Every third-party action is pinned to a full 40-character commit SHA.

**Verification in CI.** `tools/verify_image_signatures.py` runs `cosign verify` on every image
digest and fails on a missing or invalid signature (Req 12.14). The `sign` job runs it on the
released digests with the workflow identity regexp and the GitHub OIDC issuer. The kind job runs it
with `--pull-request` because its images live only in the local harness registry and are never
signed: the tool checks that each image is a pinned local-registry digest, runs no `cosign`
command and prints that signature verification was skipped. Pull-request and other branch runs
likewise verify against the local-registry digests, record that signature verification was skipped
in the run output, and neither sign nor push to a registry (Req 15.8). The Upgrade_Test step
(task 15.1) reuses the same tool.

**Admission policy.** Where `imagePolicy.enabled` is true, the chart renders a Kyverno
`ClusterPolicy` with `verifyImages` that admits only images carrying a valid signature from the
expected workflow identity and rejects the rest (Req 12.10). Kyverno is the Image_Policy_Engine,
an operator-installed optional prerequisite that the chart does not install; the default is
`false`. Signature verification is not available in upstream admission, so this is the only kind
added to satisfy Req 12.10 (see ADR 0010).

**Digest references.** The chart and CI reference each image by its content digest and never by a
mutable tag alone (Req 12.11).

## Consequences

Distroless final stages shrink the attack surface and remove shells and package managers from
production, but they also remove debugging tools: operators debug with ephemeral containers or
the builder-stage image. Pod `exec` health or freeze helpers cannot rely on a shell in platform
images, which is why the drain uses an HTTP `preStop` hook instead of a command.

Failing closed on scanner errors and on SBOM or signing failures keeps unverified images out of the
registry, at the cost of blocked releases when the vulnerability database or the signing service is
down. The CRITICAL or HIGH with a fix available threshold avoids blocking on unfixable findings but
accepts them silently, so the scan report remains a Go_Live_Checklist input.

Keyless signing needs no long-lived key to rotate, but ties trust to the CI workflow identity and
the public transparency log. Pull-request runs verify less than release runs; the run output states
this so it is never mistaken for a signature check. The admission policy is only as strong as the
operator's decision to install Kyverno and enable it, and a misconfigured identity pattern can
block all deployments, so the Deployment_Guide documents how to test it in audit mode first.

This decision flows back into #[[file:.kiro/steering/deployment.md]] per
#[[file:.kiro/steering/working-agreements.md]]. The ADR was finalized (status `Accepted`) in task
13.5 to match the shipped pipeline.
