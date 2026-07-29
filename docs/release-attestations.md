# Synthetic Release Attestations

## Scope

Phase 7C integrates provenance and SBOM attestations for the verified
`v0.0.0-ci` synthetic release bundle. This is CI control evidence only. It is
not a production release, a production attestation, a signature, a security
certification, or authorization to publish.

The integration is limited to `.github/workflows/ci.yml`. Historical release
workflows remain frozen.

## Selected Action

The selected action is the latest reviewed stable v4 release at integration
time:

- repository: `actions/attest`
- release: `v4.2.0`
- immutable commit: `f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6`
- runtime: Node 24

Mutable tags are not workflow identities. A tag can be moved after review, so
the workflow invokes the exact commit and retains `# v4.2.0` only as a
human-readable release annotation. The current-CI action pin inventory records
the repository, stable release, commit, runtime, and `action.yml` blob.

## Handoff Boundary

The producer job creates and uploads the synthetic bundle with read-only
repository permission. The `release-bundle-handoff` job:

1. downloads the artifact by immutable artifact ID;
2. validates the producer digest;
3. extracts it with the existing fail-closed archive controls;
4. verifies the release-bundle structure, manifest, checksum inventory, roles,
   blockers, and final synthetic bytes;
5. prepares the exact attestation subject inventory;
6. creates provenance and SBOM attestations;
7. verifies both online and from their generated bundles.

No attestation step runs before secure extraction and semantic verification.
No byte-changing step runs after the verified subject inventory is created.

## Subject Contract

The provenance attestation contains exactly these six basenames:

- `Youtube.Downloaderbs.exe`
- `Youtube-Downloaderbs-v0.0.0-ci.zip`
- `Youtube-Downloaderbs-v0.0.0-ci-legal.zip`
- `Youtube-Downloaderbs-v0.0.0-ci.spdx.json`
- `SHA256SUMS.txt`
- `RELEASE_MANIFEST.json`

The workflow independently validates that every subject is a regular file
inside the extracted bundle, every name is safe and unique, and every digest is
lowercase SHA-256 matching the current file bytes. It writes a dedicated
checksums input because the release bundle's distribution checksum file also
describes source archives and is not the exact provenance subject set.

Source-kit placeholders, workflow logs, and the GitHub artifact ZIP wrapper are
not attested.

## SBOM Relationship

The native `sbom-path` mode creates an SPDX 2.3 SBOM attestation:

- subject: `Youtube-Downloaderbs-v0.0.0-ci.zip`
- SBOM: `Youtube-Downloaderbs-v0.0.0-ci.spdx.json`
- predicate type: `https://spdx.dev/Document/v2.3`

The SPDX file is also one of the six provenance subjects. No custom predicate
replaces native provenance or native SBOM mode.

## Permissions And Event Behavior

Only `release-bundle-handoff` receives:

- `contents: read`
- `id-token: write`
- `attestations: write`

The producer retains only `contents: read`. The workflow does not grant
`artifact-metadata: write` because file artifacts are not pushed to a registry
and both action invocations set `create-storage-record: false`. It does not
grant `contents: write`, `packages: write`, `actions: write`,
`deployments: write`, or `security-events: write`.

Attestation steps run on main-branch pushes and same-repository pull requests.
Fork pull requests still run ordinary validation and the secure handoff, then
emit an explicit skip message. They do not request OIDC or invoke
`actions/attest`.

## Verification

The online verifier records `gh --version`, checks that the installed CLI
supports the required attestation flags, and verifies exact subject bytes
against:

- repository `sangbuidinh/s9h-YoutubeDownloader`;
- signer workflow
  `sangbuidinh/s9h-YoutubeDownloader/.github/workflows/ci.yml`;
- source digest `${{ github.sha }}`;
- provenance predicate `https://slsa.dev/provenance/v1`;
- SBOM predicate `https://spdx.dev/Document/v2.3`.

The offline path obtains the official trusted root while online, then verifies
the same subject bytes with each action's `bundle-path` output and
`--custom-trusted-root`. Tokens are removed and network proxy variables are
set to a closed local endpoint during verification. A missing bundle, trusted
root, subject, repository identity, predicate match, or successful CLI exit
stops the job. There is no unsigned parser or online fallback.

The online step uses only the job-scoped built-in `${{ github.token }}`. No
external secret or credential is provisioned.

## Failure Handling

- Missing, extra, duplicate, unsafe, malformed, uppercase, stale, or mismatched
  subject records fail before attestation.
- An unsupported GitHub CLI fails the integration.
- An action or verification failure fails the handoff job.
- A changed final subject invalidates downstream checksums and attestations.
- A fork pull request is a documented skip, not an attestation success.
- No failure permits a production, readiness, or publishing claim.

## Non-Claims

This checkpoint does not claim a production attestation, production signed
bytes, Authenticode, release readiness, SLSA level attainment,
reproducibility, vulnerability absence, security certification, legal
compliance, complete source correspondence, or publishing authorization.

## Phase 7C Remote Validation

Phase 7C-R2 validated the synthetic integration in a same-repository pull
request:

1. push the reviewed local commit without force;
2. create a draft pull request;
3. observe a same-repository synthetic CI run;
4. confirm both action invocations and online/offline verification succeed;
5. verify the fork-PR condition remains fail-closed by static review;
6. reconcile CI evidence and the pull request body without making production
   claims.

The remote run passed on its first attempt, so
`ci_integration_validated_remotely` is true. This records synthetic CI
evidence only. Production provenance readiness, production SBOM attestation,
and combined release-assurance readiness remain false.
