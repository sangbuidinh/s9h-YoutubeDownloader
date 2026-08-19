# Authenticode Signing

## Scope

Phase 7D-R2-F1 integrates an exact SSL.com eSigner CKA installer identity gate and a protected, manual-only synthetic Authenticode workflow scaffold. It preserves the selected provider and custody model, keeps synthetic and production signing gates separate, and retains exact signer-certificate identity verification. It does not purchase or provision a certificate, create credentials, install provider software locally, sign a local or production file, dispatch the protected workflow, or authorize a release.

The machine-readable provider contract is `legal/authenticode-provider.json`. Production and release claims remain owned by `legal/release-assurance-policy.json` and remain false.

## Provider And Custody Decision

The selected technical provider is SSL.com eSigner:

- provider ID: `ssl-com-esigner`;
- service: `SSL.com eSigner`;
- custody: provider-managed cloud HSM;
- private-key export: forbidden;
- repository PFX or private key: forbidden;
- account provisioned: false;
- procurement authorized: false;
- production signing authorized: false;
- certificate class selected: false;
- operator entity type: `UNRESOLVED`;
- preferred class: unset.

SSL.com states that eSigner for Code stores private keys in an SSL.com-managed FIPS 140-2 Level 3 cloud HSM and that private keys never leave that HSM. eSigner CKA exposes the service through Windows CNG/KSP so standard Windows tools such as SignTool can use the cloud-held key.

This is a technical integration choice only. Provider enrollment, identity validation, billing, certificate issuance, credential configuration, and signing require later authorization.

## Alternatives Not Selected

Microsoft Artifact Signing Public Trust is not selected because current geographic eligibility is not a safe default for a Vietnam-based operator.

SignPath Foundation is not selected because the repository project license remains `not-selected`. Public repository availability is not treated as open-source-program eligibility.

An exportable PFX in GitHub secrets is rejected. Extraction of the PFX or compromise of a signing runner would create a materially weaker custody boundary than a non-exportable provider-managed key.

No project-license file or status is changed by this decision.

## Official Provider Revalidation

Only official SSL.com sources were used for the provider evidence, and the exact linked installer bytes were revalidated for R2-F1 on `2026-08-18`.

The official download page identifies the current release as:

- version: `1.1.2`;
- release label: `SSL-COM-eSigner-CKA_1-1-2_build_20260062`;
- official linked display filename: `SSL.COM eSigner CKA_1.1.2_build_202600624.exe`.

The official label ends in build `20260062`, while the linked display filename contains `202600624`. That filename discrepancy is preserved explicitly and is not normalized away. The repository calculated the SHA-256 from the exact official linked bytes after revalidating their Windows Authenticode identity. The digest is not represented as an SSL.com-published checksum.

The accepted package identity is:

- size: `16103264` bytes;
- SHA-256: `3f088403139505ddfb0ed3b56b72893f92c865f98b382753a1e1c695a5cece35`;
- PE architecture: `x86`;
- ProductVersion and FileVersion: `1.1.2`;
- ProductName: `SSL.COM eSigner Cloud Key Adapter`;
- Authenticode status: `Valid`;
- signer SimpleName: `SSL Corp`;
- signer serial: `03987FF7E46C81A6B4343A575FA0F8F3`;
- signer thumbprint: `B40BDE1B8DBA07DEC2D1E7EDFADD9B1BC51F922D`;
- signer issuer: `SSL.com EV Code Signing Intermediate CA RSA R3`;
- timestamp certificate: present.

`scripts/verify_esigner_cka_installer.ps1` verifies the allowed-root and reparse-point boundary, exact filename, byte size, SHA-256, PE architecture, Authenticode status, signer identity, version metadata, product name, and timestamp-certificate presence before the workflow can execute the installer. The verifier performs no download and does not execute the installer. `cka_package_integrated=true` means this exact identity gate and its protected workflow integration are implemented; it does not mean an account, certificate, credential, timestamp authority, or production signing path is provisioned or approved.

The official CI guidance documents a silent installer form:

```text
eSigner_CKA_Installer.exe /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /DIR=<INSTALL_DIR>
```

The protected workflow may execute the verified installer only after the exact identity gate passes, only with `/CURRENTUSER`, only under the runner temporary root, and only for an authorized synthetic sandbox run. No local installer execution occurred in this phase.

## Authentication And Non-Interactive Constraints

Official installation guidance describes two modes:

- manual mode uses provider account authentication and a per-signing OTP;
- automated mode uses protected provider authentication material and a master-key file and is documented for OV or EV certificates.

Current official SSL.com material is not specific enough to select a certificate class for unattended use. General eSigner product material advertises IV, OV, and EV code-signing certificates with eSigner, while the CKA installation and CI/CD guidance identify OV or EV for automated mode. The machine-readable policy therefore records `automated_certificate_classes=["OV","EV"]`, leaves `preferred_class=null`, and requires provider confirmation before IV automation could be accepted.

R2-F1 stores no account name, password, OTP, TOTP material, master key, certificate identifier, or other credential. It does not select IV, OV, or EV for production. The scaffold references only protected environment secrets `ESIGNER_SANDBOX_USERNAME`, `ESIGNER_SANDBOX_PASSWORD`, and `ESIGNER_SANDBOX_TOTP_SECRET`, plus explicit protected variables for synthetic authorization, certificate class, expected publisher, and expected thumbprint. Missing values fail closed; public demo credentials are not a fallback.

CKA loads enrolled certificates into the Windows Current User Personal certificate store and identifies them by common name and serial number. The provider examples select the first code-signing certificate. This project instead requires an explicit thumbprint after provisioning and fails closed when selection is absent or ambiguous. The thumbprint is a selector, not a signing digest; `/fd SHA256` remains mandatory.

## Protected Synthetic Sandbox Workflow

`.github/workflows/authenticode-sandbox.yml` is actionless, manual-only, and bound to the protected `authenticode-sandbox` environment. It requests only `contents: read`, accepts only `workflow_dispatch` on upstream `main`, validates the exact GitHub-supplied commit, and performs an actionless detached checkout of that commit. It has no release, artifact-upload, push, attestation, or other publishing path.

The workflow requires explicit `ESIGNER_SANDBOX_SIGNING_AUTHORIZED=true`, accepts only an OV or EV sandbox certificate for automated mode, and selects exactly one Current User code-signing certificate by exact normalized thumbprint, exact publisher SimpleName, and private-key presence. It builds the deterministic subject twice and requires identical unsigned hashes before copying exactly one unsigned `Youtube.Downloaderbs.exe` into the signing root. The subject identifies itself as `synthetic / v0.0.0-ci / non-production`.

Only a runtime copy of the provider policy is enabled for the authorized synthetic operation. Production authorization, remote-validation state, release readiness, and publishing remain false. After signing, the workflow requires a changed digest, exact signed SHA-256 reconciliation, exact publisher identity, Default Authenticode `/pa` verification, and an RFC 3161 SHA-256 timestamp. The summary contains sanitized evidence and no credential or certificate identifier. An `always()` cleanup unloads CKA, invokes the unique controlled-root uninstaller when present, and removes the contained runner-temporary state.

No protected workflow dispatch has occurred. No installer was executed by this implementation work, no signing occurred, and no protected environment, account, certificate, secret, or variable provisioning is claimed.

## Timestamp Candidate

The official SSL.com SignTool guidance uses:

```text
http://ts.ssl.com
```

with `/tr` and `/td SHA256`. This endpoint is recorded only as an RFC 3161 candidate. `timestamp_authority_approved=false` remains mandatory until operator review and remote validation are complete.

The legacy timestamp mode and legacy endpoint are not approved by this contract.

## Signing Boundary

The only first-party signing target is:

```text
Youtube.Downloaderbs.exe
```

The signing wrapper rejects:

- `yt-dlp.exe`;
- `ffmpeg.exe`;
- `ffprobe.exe`;
- `aria2c.exe`;
- `deno.exe`;
- every other vendor-supplied executable or DLL;
- arbitrary executables;
- directories, reparse points, and paths outside the authorized release root.

Vendor binaries retain their upstream identity. This project does not replace or add a first-party signature to vendor-supplied bytes.

## Signing Contract

The canonical SignTool contract is:

```text
signtool.exe sign /fd SHA256 /tr <approved-rfc3161-url> /td SHA256 /sha1 <certificate-selector> Youtube.Downloaderbs.exe
```

The certificate selector is required only after provisioning and must never be logged. Real signing requires an explicit purpose with exactly two accepted values: `synthetic` or `production`. `PlanOnly` remains non-signing and does not require a real signing purpose.

Synthetic signing requires provider account provisioning, certificate provisioning, configured credentials, timestamp approval, explicit synthetic authorization, exact expected publisher identity, and exact expected certificate thumbprint. It deliberately does not require procurement authorization, production signing authorization, prior remote validation, release readiness, or publishing authorization.

Production signing requires every synthetic prerequisite, explicit production purpose, production signing authorization, prior successful remote synthetic validation, and all independent production release gates identified in the provider contract. Production signing remains blocked in this checkpoint.

`scripts/sign_authenticode.ps1`:

- accepts one target;
- validates the canonical target and unsigned PE structure;
- reads an explicit provider configuration;
- accepts only `synthetic` or `production` as real signing purposes;
- reads the release-assurance policy only for production signing;
- has no password, OTP, token, PFX, or private-key parameter;
- never downloads or installs provider software;
- provides a deterministic `-PlanOnly` mode;
- invokes the separate verifier after a successful real signing command;
- has no unsigned fallback.

Example fixture-only plan:

```powershell
.\scripts\sign_authenticode.ps1 `
  -Target <release-root>\Youtube.Downloaderbs.exe `
  -ReleaseRoot <release-root> `
  -ProviderConfigPath .\legal\authenticode-provider.json `
  -TimestampUrl http://ts.ssl.com `
  -PlanOnly
```

The output replaces paths and certificate selection with canonical placeholders and does not invoke SignTool.

## Verification Contract

`scripts/verify_authenticode_signature.ps1` performs a separate fail-closed operation:

```text
signtool.exe verify /pa /all /v /tw Youtube.Downloaderbs.exe
```

It requires:

- successful SignTool exit status;
- Default Authenticode policy through `/pa`;
- successful verification text;
- an RFC 3161 timestamp;
- SHA-256 evidence;
- the expected publisher in SignTool as supporting evidence;
- an exact expected publisher display identity from the signer certificate;
- an exact normalized signer-certificate thumbprint match;
- a valid PowerShell Authenticode result;
- a timestamp certificate;
- a SHA-256 hash recorded only after verification.

Missing, invalid, untrusted, untimestamped, unexpected, or otherwise unverifiable signatures stop the process. Downstream packaging is allowed only for the byte-identical signed EXE matching the recorded post-verification SHA-256.

Thumbprints are normalized for hexadecimal case and whitespace before exact comparison. Neither the signing wrapper nor the verification result prints the configured or observed thumbprint. Publisher substring matching is not accepted as the primary identity control.

## Final-Byte Ordering

The required order is:

1. validate unsigned first-party EXE structure;
2. sign only the standalone first-party EXE;
3. obtain an RFC 3161 timestamp;
4. verify Authenticode under `/pa`;
5. verify timestamp presence and validity;
6. verify expected publisher identity;
7. use the byte-identical verified signed EXE for portable-package assembly;
8. calculate checksums and downstream assurance artifacts.

No byte-changing operation may affect the EXE after signature verification. A checksum, package, SBOM, manifest, or attestation produced before signing describes stale bytes and is rejected.

## Non-Claims

R2-F1 does not claim:

- certificate-class selection;
- provider or certificate provisioning;
- configured credentials;
- approved timestamp authority;
- remote signing validation;
- synthetic signing authorization;
- production signing authorization;
- a signed, timestamped, or verified production EXE;
- a production SBOM or provenance attestation;
- release-assurance readiness;
- release or publishing authorization.

No production file is signed, timestamped, or signature-verified by this phase. The implemented workflow scaffold and exact installer identity gate are static controls, not live signing evidence.

## Remaining Blockers

- certificate class decision;
- provider account and identity validation;
- certificate issuance;
- protected credential and environment configuration;
- protected remote signing environment;
- timestamp authority approval;
- remote synthetic signing validation;
- production signing workflow integration;
- production final bytes;
- production signature and timestamp verification.

## Official Sources

- [SSL.com eSigner for Code](https://www.ssl.com/products/software-integrity/signing-service/)
- [SSL.com downloads](https://www.ssl.com/downloads/)
- [Install eSigner CKA](https://www.ssl.com/how-to/how-to-install-ssl-com-esigner-cloud-key-adapter-cka/)
- [Use eSigner CKA with SignTool](https://www.ssl.com/how-to/automate-ev-code-signing-with-signtool-or-certutil-esigner/)
- [Integrate eSigner CKA with CI/CD](https://www.ssl.com/how-to/how-to-integrate-esigner-cka-with-ci-cd-tools-for-automated-code-signing/)
- [Code-signing certificate and eSigner overview](https://www.ssl.com/how-to/getting-started-with-your-code-signing-certificate-installation-configuration-and-your-first-signing-operation/)
