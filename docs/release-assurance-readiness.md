# Release Assurance Readiness

## Scope

Phase 7D-R1 selects the SSL.com eSigner technical provider and a provider-managed cloud-HSM custody model and adds fail-closed local Authenticode signing and verification scaffolding. It does not purchase or provision a certificate, create or store credentials, install provider software, sign a file, generate a production attestation, modify a workflow, or authorize publishing.

The provider-specific machine-readable owner is `legal/authenticode-provider.json`. Combined release states remain owned by `legal/release-assurance-policy.json`. Their readiness fields and claims are fail-closed. Existing legal, source-availability, source-kit, release, and publishing gates remain independent and unchanged.

## Current Baseline

- Repository: `sangbuidinh/s9h-YoutubeDownloader`
- Phase 7D assessment baseline: `45ecd30aaa95661778956b6724aaccd98bfe66c1`
- Product: `Youtube Downloaderbs`
- Version: `1.3.1`
- Planning target: a future signed first-party executable, deterministic SPDX 2.3 JSON SBOM, and final-byte GitHub attestations.
- Implemented state: deterministic synthetic SBOM and release-bundle controls, synthetic attestation integration, and local fail-closed Authenticode command scaffolding.
- Verified state: provider/custody, policy, target, command, ordering, secret-hygiene, workflow, subject, permission, and verification contracts can be checked locally without signing, downloading CKA, invoking `actions/attest`, or requesting OIDC.
- Blocked state: certificate class, provider provisioning, CKA package integration, real Authenticode signing, production SBOM, provenance, and combined release assurance are not ready.
- Future authorization: each implementation stage and any publishing action require separate approval.

## Non-Claims

Phase 7D-R1 does not claim that a production executable is signed, timestamped, or signature-verified. It does not claim that a production SBOM or production provenance attestation exists. The Phase 7C synthetic CI integration passed same-repository remote CI, but that result is not production attestation evidence. Plan-only Authenticode output is a sanitized command contract, not a signature.

An eventual attestation would link subject bytes to build identity and provenance evidence. It would not certify security, absence of vulnerabilities, legal compliance, complete source correspondence, reproducibility, or release readiness. Existing `legal_compliance_certified`, `source_availability_certified`, `source_assets_created`, `source_kits_ready`, `assembly_authorized`, `release_gate_reconsideration_allowed`, `release_ready`, and `publishing_allowed` states remain false.

## Release Artifact Inventory

The current release path handles these artifact classes:

| Artifact | Current production point | Current state | Future assurance treatment |
| --- | --- | --- | --- |
| `Youtube.Downloaderbs.exe` | `scripts/package_windows.py`, then `scripts/build_release_v1_3_1.ps1` | First-party PE copied as standalone and into the portable tree | Only Authenticode signing candidate; attest final signed bytes |
| `Youtube-Downloaderbs-v{version}.zip` | `scripts/build_release_v1_3_1.ps1`, then rewritten by `scripts/prepare_release_legal_payload.py` | Portable package containing the application and bundled runtimes plus injected legal payload | Build from signed EXE; checksum and attest only after final rewrite |
| `Youtube-Downloaderbs-v{version}-legal.zip` | `scripts/prepare_release_legal_payload.py` | Deterministic legal payload ZIP | Final checksum and provenance subject |
| `SHA256SUMS.txt` | `scripts/prepare_release_bundle.py` | Deterministic checksum list for current release assets | Synchronize after final assets and SBOM; attest final bytes |
| `RELEASE_NOTES.md` | build script, then legal-payload checksum rewrite | User-facing notes with portable checksum | Finalize before attestation and bundle handoff |
| `RELEASE_MANIFEST.json` | `scripts/prepare_release_bundle.py` | Canonical release-bundle v2 manifest | Synchronize after final subjects; attest final bytes |
| GitHub Actions uploaded bundle | release workflow `actions/upload-artifact` step | Synthetic CI handoff artifact, not an artifact attestation | Never present as a release attestation |
| aria2 and FFmpeg source ZIP placeholders | required by release bundle v2 | Blocked and not assembled | Not mandatory provenance subjects until separately authorized |

The portable package also distributes vendor executables `yt-dlp.exe`, `ffmpeg.exe`, `ffprobe.exe`, `aria2c.exe`, and `deno.exe`. They belong in future SBOM coverage but are outside project re-signing authority.

## Authenticode Design

Only `Youtube.Downloaderbs.exe` is a first-party signing candidate. A future signing command must use SHA-256 as the file digest, an RFC 3161 timestamp request with SHA-256 as the timestamp digest, and fail closed when signing or timestamping fails.

Signature verification is a separate mandatory operation. The planning contract requires the Windows Default Authenticode verification policy, represented by SignTool `verify /pa`, plus explicit timestamp verification. A successful signing command alone is not release evidence.

The selected technical provider is SSL.com eSigner with a provider-managed cloud HSM and a non-exportable provider key. The certificate class remains `IV_OR_OV_OPERATOR_DECISION_PENDING`; no account, certificate, expected publisher, certificate thumbprint, credential source, or timestamp authority is provisioned or approved. The official SSL.com timestamp URL is recorded only as a candidate.

## Authenticode Credential Boundary

A code-signing private key is release infrastructure, not ordinary source configuration. It must never be stored in the repository, policy JSON, build logs, review ZIPs, or normal application settings.

The selected custody model is SSL.com-managed cloud HSM. Private-key export, repository PFX storage, repository private-key storage, and logging of account credentials, OTP/TOTP material, master keys, or certificate identifiers are forbidden.

This technical selection is not procurement or production approval. No provider account or certificate has been purchased, provisioned, or configured. Certificate class, identity validation, expected publisher, credential protection, rotation, revocation, timestamp approval, incident response, and remote signing validation remain separate gates.

Microsoft Artifact Signing Public Trust is not selected because current geographic eligibility is not a safe default for a Vietnam-based operator. SignPath Foundation eligibility is not assumed because the repository project license remains `not-selected`. An exportable PFX in GitHub secrets is rejected because key extraction and runner compromise materially weaken custody.

## Signing and Verification Sequence

The first-party executable must be fully built and structurally validated before signing. Signing changes PE bytes, so the signed executable must replace both the standalone asset and the copy used to assemble the portable package.

The required local sequence is:

1. validate the unsigned first-party executable structure;
2. sign only `Youtube.Downloaderbs.exe` using SHA-256;
3. request an approved RFC 3161 timestamp using SHA-256;
4. verify the signature under the Default Authenticode policy;
5. verify that the timestamp is present and valid;
6. assemble downstream artifacts only from those verified signed bytes.

Checksum or ZIP creation before signing would describe stale bytes. Timestamping and signature verification remain separate checks because certificate validity evidence and content trust validation are different release conditions.

## Third-Party Binary Boundary

This project must not re-sign `yt-dlp.exe`, `ffmpeg.exe`, `ffprobe.exe`, `deno.exe`, `aria2c.exe`, or any other vendor-supplied binary. Re-signing would replace or add a project signature to bytes the project did not produce and could misrepresent authorship, provenance, or vendor assurance.

Vendor binaries remain subject to the repository's checksum-pinned runtime inventory, legal notices, and future SBOM coverage. Their upstream signatures, when present, may be inspected as separate evidence, but are not replaced by this project.

## SBOM Format Decision

The planning target is `SPDX-2.3-json` with the SBOM attestation predicate type `https://spdx.dev/Document/v2.3`. SPDX 2.3 has an official specification, supports JSON serialization, document creation identity, namespaces, packages, files, relationships, checksums, supplier/origin data, package download locations, and explicit `NOASSERTION` handling.

The required filename template is `Youtube-Downloaderbs-v{version}.spdx.json`. JSON must be deterministic, schema-valid, semantically reconciled with the final package, release manifest, and checksums, and generated by an identified tool at a pinned version.

This is a format decision, not a generator decision. `generator_selected` remains false.

## SBOM Coverage Model

The future SBOM must describe the software actually distributed in the final portable package, not only Python dependency declarations. Coverage includes:

- the first-party application;
- Python interpreter and runtime components included by packaging;
- Python packages actually distributed by PyInstaller;
- yt-dlp;
- FFmpeg and ffprobe;
- Deno;
- aria2;
- every other executable, DLL, package, or distributable dependency in the final package.

Records require stable package and relationship ordering, package versions, SHA-256 package or file checksums where applicable, supplier or origin when known, authoritative declared-license evidence, Package URLs where authoritative, a release-unique document namespace, source and control commits, release tag, and association with final artifacts. Download location must follow SPDX rules and use `NOASSERTION` when the required determination has not been made. A concluded license must not be fabricated.

## SBOM Generator Options

| Option | PyInstaller awareness | Bundled executable awareness | Determinism and offline validation | License/checksum evidence | Maintenance and supply-chain risk |
| --- | --- | --- | --- | --- | --- |
| Deterministic project-owned generation | Can be designed around the actual PyInstaller analysis and release tree | Can use the final package inventory and existing pinned runtime records | Strongest control over ordering and offline schema/semantic checks | Can require authoritative evidence and final SHA-256 values | Highest implementation maintenance; avoids a new scanner binary but custom logic needs extensive tests |
| Pinned external scanner | Tool-dependent; frozen Python extraction must be proven | Often broad, but exact PE/DLL and nested package behavior must be tested | Version pinning helps, but output stability and offline schema support require evidence | Potentially broad detection; license conclusions may need correction and provenance | Adds a privileged build dependency and its own supply-chain/update burden |
| Python or package-manager-only generation | Describes declared Python packages, not necessarily frozen runtime contents | Does not cover bundled vendor executables and DLLs adequately | Usually simple and stable, but validates only a partial inventory | Weak final-byte checksum and non-Python license coverage | Lowest setup burden but structurally insufficient for this product |

Phases 7A2 and 7B selected and integrated the deterministic project-owned SPDX generator for synthetic CI evidence. No production SBOM has been generated or reconciled against final PyInstaller, Python, runtime, portable-package, and release-artifact inventories.

## Provenance and Attestation Design

The selected provider is GitHub artifact attestations using `actions/attest`. Official-source revalidation for Phase 7C selected stable release `v4.2.0`, major `v4`, at immutable commit `f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6`.

The exact `action.yml` at that commit accepts mutually exclusive subject path, digest, or checksum inputs, supports JSON SPDX or CycloneDX through `sbom-path`, supports custom predicates, defaults registry push to false, and runs on Node 24. Its `create-storage-record` input defaults true but requires registry push. Current CI uses file artifacts, does not push to a registry, and explicitly sets `create-storage-record: false`, so `artifact-metadata: write` is not granted. Mutable release tags remain rejected as workflow identities.

Future release subjects are the final standalone EXE, portable ZIP, legal ZIP, SPDX JSON, `SHA256SUMS.txt`, and `RELEASE_MANIFEST.json`. Source-kit archives are excluded from mandatory current subjects while source-kit assembly remains blocked. A later authorization may add them after final bytes exist.

Current CI attests only the verified `v0.0.0-ci` bundle described in `docs/release-attestations.md`. Public verification identifies `sangbuidinh/s9h-YoutubeDownloader`, verifies exact subject bytes and signer workflow, and distinguishes native provenance from SPDX 2.3 SBOM attestations. Both online and generated-bundle offline commands passed same-repository remote CI. All production readiness remains false.

## Workflow Permission Boundary

The current-CI `release-bundle-handoff` job uses this reviewed job-level permission set:

- `contents: read` for repository context;
- `id-token: write` for OIDC identity;
- `attestations: write` for artifact attestation storage;
- no `artifact-metadata: write` because storage-record creation and registry push are disabled;
- no `contents: write`, `packages: write`, `actions: write`, `deployments: write`, or `security-events: write`.

The producer remains `contents: read` only, and there is no workflow-global write permission. Main pushes and same-repository pull requests run the attestation steps. Fork pull requests run ordinary validation and secure handoff verification, then explicitly skip attestation because write and OIDC permissions are unavailable. This synthetic permission integration does not authorize equivalent production workflow permissions.

## Final-Byte Ordering

The current release scripts perform these byte-changing operations:

1. PyInstaller builds the first-party PE.
2. The build script copies the PE and vendor runtimes into the portable tree.
3. `Compress-Archive` creates the initial portable ZIP.
4. The build script appends initial EXE and ZIP checksums to release notes.
5. The legal-payload builder creates the legal ZIP, rewrites the portable ZIP to inject legal material, recomputes the portable checksum, and rewrites release notes.
6. The bundle builder copies final assets, writes `SHA256SUMS.txt`, and writes canonical `RELEASE_MANIFEST.json`.
7. GitHub artifact upload creates a separate synthetic handoff archive; it does not change the release subject files.

Future ordering must therefore be exactly:

1. build first-party executable;
2. validate unsigned build structure;
3. Authenticode-sign first-party executable;
4. verify Authenticode signature and timestamp;
5. assemble the portable package using the signed executable;
6. calculate final artifact checksums;
7. generate and validate the final SBOM;
8. synchronize checksums, release notes and release manifest;
9. perform final release-bundle validation;
10. generate provenance and SBOM attestations over final immutable subjects;
11. verify attestations;
12. hand off immutable release bundle;
13. allow publishing only when all independent release gates pass.

No byte-changing operation may follow checksum, manifest, or attestation finalization for an affected subject.

## Verification Strategy

Phase 7D-R1 local verification remains non-signing and non-attesting. The Authenticode verifier enforces file hygiene, strict canonical JSON, duplicate-key rejection, exact provider and custody identities, CKA non-integration, false provisioning/readiness/production states, exact targets and vendor exclusions, signing and verification switches, final-byte ordering, project-license invariants, and rejection of secret, certificate, key, or local-profile material. The existing release-assurance verifier continues to enforce combined non-claims and all Phase 7B/7C controls.

Later implementation must add independent evidence gates:

- SignTool signing exit status and RFC 3161 timestamp success;
- `signtool verify /pa` plus timestamp inspection for the standalone EXE and the byte-identical EXE inside the portable ZIP;
- SPDX JSON schema validation plus semantic reconciliation with the extracted final package, checksums, and release manifest;
- GitHub CLI verification of provenance and SBOM attestations against this exact repository and final subject digests;
- fail-closed bundle validation before immutable handoff and separately authorized publishing.

## Failure Modes

- Certificate or signing identity unavailable: stop; do not publish an unsigned artifact as signed.
- Signing command failure: stop before portable package assembly.
- RFC 3161 timestamp failure: stop even if the PE contains a signature.
- Signature or timestamp verification failure: stop and preserve no readiness claim.
- Vendor binary selected for project re-signing: reject the release plan.
- SBOM misses PyInstaller, Python, executable, DLL, or runtime content: fail semantic validation.
- SBOM ordering or serialization changes without input changes: treat determinism as unproven.
- License evidence is absent: use SPDX-compliant unknown handling; do not fabricate a conclusion.
- Any final subject changes after checksum or attestation: invalidate and regenerate downstream evidence.
- Mutable action tag used without immutable resolution: reject workflow integration.
- Attestation permissions widened globally or mixed with unnecessary publishing authority: reject permission review.
- Synthetic CI artifact represented as release provenance: reject the claim.
- Existing legal/source-kit gate changes as a side effect: reject the phase.

## Implementation Stages

- Phase 7A2: deterministic SBOM generator selection and prototype contract.
- Phase 7B: production SBOM generation and release-bundle integration.
- Phase 7C: synthetic GitHub provenance and SBOM attestation integration, followed by separately authorized remote CI validation.
- Phase 7D-R1: SSL.com eSigner provider/custody selection and fail-closed local signing/verification scaffold.
- Phase 7D-R2: certificate-class decision, provider provisioning, and real synthetic signing CI, only after separate authorization.
- Phase 7E: end-to-end signed release-assurance rehearsal.

Each stage requires separate review. Actual publishing remains separately authorized after all existing legal, source-kit, release, and new assurance gates pass.

## Official Evidence

Phase 7A2/7C records below were retrieved on `2026-07-17T03:10:40Z`. SSL.com records were revalidated on `2026-07-29`. Findings are paraphrased; no external document is copied into the repository.

| Source | Owner and revision | Purpose and finding | Weight |
| --- | --- | --- | --- |
| [SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool) | Microsoft; current page at retrieval | Defines signing, RFC 3161 `/tr`, required digest switches `/fd` and `/td`, SHA-256 recommendation, `/pa` verification policy, and fail-signaling exit codes. | Normative tool contract |
| [Using SignTool to sign a file](https://learn.microsoft.com/en-us/windows/win32/seccrypto/using-signtool-to-sign-a-file) | Microsoft; page revision observed at retrieval | Establishes that signing binds publisher identity and file integrity; examples expose multiple credential custody paths without selecting one for this project. | Normative implementation guidance |
| [Using SignTool to verify a file signature](https://learn.microsoft.com/en-us/windows/win32/seccrypto/using-signtool-to-verify-a-file-signature) | Microsoft; page revision observed at retrieval | Documents `signtool verify /pa` and verification of file integrity and trust under Authenticode policy. | Normative verification guidance |
| [Artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) | GitHub; current docs at retrieval | Explains provenance/integrity linkage, Sigstore-backed attestations, and the requirement to verify attestations before security benefit is realized. | Normative service model |
| [Using artifact attestations to establish provenance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations) | GitHub; current docs at retrieval | Defines job permissions, subject inputs, GitHub CLI verification, and the SPDX 2.3 predicate type. | Normative implementation guidance |
| [Verifying attestations offline](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/verify-attestations-offline) | GitHub; current docs at retrieval | Defines downloading bundles while online and verifying them later in a disconnected environment. | Normative verification guidance |
| [`actions/attest` release `v4.2.0`](https://github.com/actions/attest/releases/tag/v4.2.0), [immutable `action.yml`](https://github.com/actions/attest/blob/f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6/action.yml), and [immutable `README.md`](https://github.com/actions/attest/blob/f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6/README.md) | GitHub; release published `2026-07-16`, commit `f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6` | Exact selected action inputs, outputs, runtime, defaults, subject limits, and SBOM constraints came from `action.yml`; permission purpose and artifact storage-record behavior came from the README. The action file itself does not declare job permissions. | Normative selected action contract |
| [eSigner for Code](https://www.ssl.com/products/software-integrity/signing-service/) | SSL.com; current page at retrieval | Establishes SSL.com-managed FIPS cloud-HSM custody, non-exportable private keys, CKA/SignTool integration, and headless signing capability. | Selected provider and custody evidence |
| [SSL.com downloads](https://www.ssl.com/downloads/) | SSL.com; current page at retrieval | Identifies eSigner CKA `1.1.2` build label `20260062` and the observed Windows package. It does not publish an accepted immutable package digest. | Current package evidence and blocker |
| [Install eSigner CKA](https://www.ssl.com/how-to/how-to-install-ssl-com-esigner-cloud-key-adapter-cka/) and [CI/CD integration](https://www.ssl.com/how-to/how-to-integrate-esigner-cka-with-ci-cd-tools-for-automated-code-signing/) | SSL.com; current pages at retrieval | Defines Windows CNG/KSP integration, manual and automated authentication modes, Windows certificate-store behavior, silent-install syntax, and official example certificate discovery. Automated mode is documented for OV or EV certificates. | Integration and authentication evidence |
| [eSigner CKA with SignTool](https://www.ssl.com/how-to/automate-ev-code-signing-with-signtool-or-certutil-esigner/) | SSL.com; current page at retrieval | Documents `/fd sha256`, RFC 3161 `/tr http://ts.ssl.com`, `/td sha256`, certificate-thumbprint selection, and manual/automated signing behavior. | Candidate command and timestamp evidence |
| [SPDX Specification 2.3.0](https://spdx.github.io/spdx-spec/v2.3/) and [conformance](https://spdx.github.io/spdx-spec/v2.3/conformance/) | SPDX Project / Linux Foundation; version 2.3.0 | Defines SPDX 2.3 and supported machine-readable JSON serialization with schema validation. | Normative format specification |
| [SPDX document composition](https://spdx.github.io/spdx-spec/v2.3/composition-of-an-SPDX-document/) and [package information](https://spdx.github.io/spdx-spec/v2.3/package-information/) | SPDX Project / Linux Foundation; version 2.3.0 | Defines creation information, packages, files, relationships, supplier/origin, download location, checksums, and license fields including `NOASSERTION`. | Normative data-model specification |
| [SPDX relationships](https://spdx.github.io/spdx-spec/v2.3/relationships-between-SPDX-elements/) | SPDX Project / Linux Foundation; version 2.3.0 | Defines package/file/document relationships and unknown relationship handling. | Normative data-model specification |

The exact action commit is implementation-specific evidence. Both `action.yml` and `README.md` were examined at that immutable commit. GitHub documentation is the source for repository-context permission guidance because `action.yml` does not declare permissions. No mutable tag alone is accepted as a future workflow pin.

## Current Decision

The SSL.com eSigner provider and provider-managed cloud-HSM custody model are selected. Signing and verification scaffolds are implemented locally, but Authenticode production implementation and readiness remain false. The current CKA installer has no accepted immutable digest, so `cka_package_integrated=false`. Certificate class, account, certificate, credential source, expected publisher, timestamp authority, and remote signing validation remain unresolved.

No production final signed bytes, provenance attestation, SBOM attestation, or production attestation verification exists. Combined release-assurance readiness and every assurance claim remain false. The next checkpoint is a separately authorized Phase 7D-R2 certificate-class and provider-provisioning decision; no purchase, credential provisioning, production signing, release, or publishing action is authorized by this document.
