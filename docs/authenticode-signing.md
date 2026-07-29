# Authenticode Signing

## Scope

Phase 7D-R1 selects a technical provider and custody model and adds fail-closed local command scaffolding. It does not purchase or provision a certificate, create credentials, install provider software, sign a file, modify a workflow, build the application, or authorize a release.

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
- certificate class: `IV_OR_OV_OPERATOR_DECISION_PENDING`.

SSL.com states that eSigner for Code stores private keys in an SSL.com-managed FIPS 140-2 Level 3 cloud HSM and that private keys never leave that HSM. eSigner CKA exposes the service through Windows CNG/KSP so standard Windows tools such as SignTool can use the cloud-held key.

This is a technical integration choice only. Provider enrollment, identity validation, billing, certificate issuance, credential configuration, and signing require later authorization.

## Alternatives Not Selected

Microsoft Artifact Signing Public Trust is not selected because current geographic eligibility is not a safe default for a Vietnam-based operator.

SignPath Foundation is not selected because the repository project license remains `not-selected`. Public repository availability is not treated as open-source-program eligibility.

An exportable PFX in GitHub secrets is rejected. Extraction of the PFX or compromise of a signing runner would create a materially weaker custody boundary than a non-exportable provider-managed key.

No project-license file or status is changed by this decision.

## Official Provider Revalidation

Only official SSL.com sources were used on `2026-07-29`.

The official download page identifies the current release as:

- version: `1.1.2`;
- release label: `SSL-COM-eSigner-CKA_1-1-2_build_20260062`;
- observed Windows package name: `SSL.COM eSigner CKA_1.1.2_build_202600624.exe`.

The package link did not provide an official SHA-256 or another independently verifiable immutable package identity. The release label and observed package filename also use different build suffixes. The official current-release page does not state the installer architecture, and a current official removal command was not established.

Therefore:

- `cka_package_integrated=false`;
- no CKA download is accepted by the repository;
- no CKA installer is downloaded or installed;
- no install or removal command is executed;
- immutable installer identity remains a blocker.

The official CI guidance documents a silent installer form:

```text
eSigner_CKA_Installer.exe /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /DIR=<INSTALL_DIR>
```

That command is evidence only. It is not approved for execution without a versioned package and verified immutable digest.

## Authentication And Non-Interactive Constraints

Official installation guidance describes two modes:

- manual mode uses provider account authentication and a per-signing OTP;
- automated mode uses protected provider authentication material and a master-key file and is documented for OV or EV certificates.

This creates a material IV-versus-OV decision. IV remains a candidate certificate class, but it must not be assumed to support unattended CI through the documented CKA automated mode. R1 stores no account name, password, OTP, TOTP material, master key, certificate identifier, or other credential.

CKA loads enrolled certificates into the Windows Current User Personal certificate store and identifies them by common name and serial number. The provider examples select the first code-signing certificate. This project instead requires an explicit thumbprint after provisioning and fails closed when selection is absent or ambiguous. The thumbprint is a selector, not a signing digest; `/fd SHA256` remains mandatory.

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

The certificate selector is required only after provisioning and must never be logged. Signing fails before invoking SignTool while any provider, certificate, credential, timestamp, remote-validation, or production authorization state is false.

`scripts/sign_authenticode.ps1`:

- accepts one target;
- validates the canonical target and unsigned PE structure;
- reads an explicit provider configuration;
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
- the expected publisher in SignTool and PowerShell Authenticode inspection;
- a valid PowerShell Authenticode result;
- a timestamp certificate;
- a SHA-256 hash recorded only after verification.

Missing, invalid, untrusted, untimestamped, unexpected, or otherwise unverifiable signatures stop the process. Downstream packaging is allowed only for the byte-identical signed EXE matching the recorded post-verification SHA-256.

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

R1 does not claim:

- certificate-class selection;
- provider or certificate provisioning;
- configured credentials;
- approved timestamp authority;
- remote signing validation;
- a signed, timestamped, or verified production EXE;
- a production SBOM or provenance attestation;
- release-assurance readiness;
- release or publishing authorization.

## Remaining Blockers

- IV versus OV operator decision;
- provider account and identity validation;
- certificate issuance;
- protected credential and environment configuration;
- approved timestamp endpoint;
- immutable CKA package identity;
- remote synthetic signing validation;
- production final bytes;
- production signature and timestamp verification.

## Official Sources

- [SSL.com eSigner for Code](https://www.ssl.com/products/software-integrity/signing-service/)
- [SSL.com downloads](https://www.ssl.com/downloads/)
- [Install eSigner CKA](https://www.ssl.com/how-to/how-to-install-ssl-com-esigner-cloud-key-adapter-cka/)
- [Use eSigner CKA with SignTool](https://www.ssl.com/how-to/automate-ev-code-signing-with-signtool-or-certutil-esigner/)
- [Integrate eSigner CKA with CI/CD](https://www.ssl.com/how-to/how-to-integrate-esigner-cka-with-ci-cd-tools-for-automated-code-signing/)
- [Code-signing certificate and eSigner overview](https://www.ssl.com/how-to/getting-started-with-your-code-signing-certificate-installation-configuration-and-your-first-signing-operation/)
