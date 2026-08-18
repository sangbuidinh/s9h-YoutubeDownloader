from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Callable

import smoke_workflow_supply_chain as supply


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "authenticode-sandbox.yml"
INSTALLER_VERIFIER_PATH = REPO_ROOT / "scripts" / "verify_esigner_cka_installer.ps1"
PROVIDER_POLICY_PATH = REPO_ROOT / "legal" / "authenticode-provider.json"
SIGNING_DOCUMENT_PATH = REPO_ROOT / "docs" / "authenticode-signing.md"
READINESS_DOCUMENT_PATH = REPO_ROOT / "docs" / "release-assurance-readiness.md"

UPSTREAM_REPOSITORY = "sangbuidinh/s9h-YoutubeDownloader"
EXPECTED_STEPS = [
    "Check execution boundary and checkout exact source",
    "Validate protected sandbox authorization",
    "Create isolated runner state",
    "Download exact pinned CKA installer",
    "Verify exact CKA installer identity before execution",
    "Install verified CKA in controlled location",
    "Configure CKA for Sandbox automated signing",
    "Load and select the exact sandbox certificate",
    "Prepare deterministic synthetic signing subject",
    "Discover deterministic x64 SignTool",
    "Sign and verify the synthetic subject",
    "Remove sandbox provider and signing state",
]
EXPECTED_SECRETS = {
    "ESIGNER_SANDBOX_PASSWORD",
    "ESIGNER_SANDBOX_TOTP_SECRET",
    "ESIGNER_SANDBOX_USERNAME",
}
EXPECTED_VARIABLES = {
    "ESIGNER_SANDBOX_CERTIFICATE_CLASS",
    "ESIGNER_SANDBOX_EXPECTED_PUBLISHER",
    "ESIGNER_SANDBOX_EXPECTED_THUMBPRINT",
    "ESIGNER_SANDBOX_SIGNING_AUTHORIZED",
}
EXPECTED_INSTALLER_IDENTITY = {
    "architecture": "x86",
    "authenticode_status_required": "Valid",
    "byte_size": 16103264,
    "file_version": "1.1.2",
    "product_name": "SSL.COM eSigner Cloud Key Adapter",
    "product_version": "1.1.2",
    "release_page_label": "SSL-COM-eSigner-CKA_1-1-2_build_20260062",
    "resource_display_filename": "SSL.COM eSigner CKA_1.1.2_build_202600624.exe",
    "sha256": "3f088403139505ddfb0ed3b56b72893f92c865f98b382753a1e1c695a5cece35",
    "signer_issuer": (
        "CN=SSL.com EV Code Signing Intermediate CA RSA R3, O=SSL Corp, "
        "L=Houston, S=Texas, C=US"
    ),
    "signer_serial": "03987FF7E46C81A6B4343A575FA0F8F3",
    "signer_simple_name": "SSL Corp",
    "signer_thumbprint": "B40BDE1B8DBA07DEC2D1E7EDFADD9B1BC51F922D",
}


class SandboxWorkflowContractError(AssertionError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SandboxWorkflowContractError(message)


def _replace_once(value: str, old: str, new: str) -> str:
    _require(value.count(old) == 1, f"mutation anchor count differs: {old}")
    return value.replace(old, new, 1)


def _require_markers(source: str, markers: tuple[str, ...], label: str) -> None:
    for marker in markers:
        _require(marker in source, f"{label} is missing: {marker}")


def _step_text(steps: list[list[str]], name: str) -> str:
    return "\n".join(supply._named_step(steps, name))


def _validate_installer_verifier(source: str) -> None:
    _require_markers(
        source,
        (
            "Assert-InstallerPath",
            "Get-PeArchitecture",
            "Get-FileHash -LiteralPath $Authorized.FullName -Algorithm SHA256",
            "Get-AuthenticodeSignature -LiteralPath $Authorized.FullName",
            "authenticode_status_required",
            "resource_display_filename",
            "byte_size",
            "signer_simple_name",
            "signer_serial",
            "signer_thumbprint",
            "signer_issuer",
            "FileVersion",
            "ProductVersion",
            "ProductName",
            "TimeStamperCertificate",
            "timestamp_certificate_required",
            "[IO.FileAttributes]::ReparsePoint",
            "StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)",
        ),
        "installer verifier",
    )
    for forbidden in (
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "Start-BitsTransfer",
        "Start-Process",
        "Invoke-Expression",
        "msiexec",
        "curl ",
        "wget ",
    ):
        _require(
            forbidden.casefold() not in source.casefold(),
            f"installer verifier must remain network-free and non-executing: {forbidden}",
        )
    _require(
        source.index("Assert-InstallerPath")
        < source.index("Get-FileHash -LiteralPath $Authorized.FullName -Algorithm SHA256")
        < source.index("Get-AuthenticodeSignature -LiteralPath $Authorized.FullName"),
        "installer verifier path, digest, and signature ordering is invalid",
    )
    result_match = re.search(
        r"(?ms)^\$Result\s*=\s*\[ordered\]@\{(?P<body>.*?)^\}\s*$",
        source,
    )
    _require(result_match is not None, "installer verifier sanitized result is missing")
    result = result_match.group("body")
    for forbidden in ("serial", "thumbprint", "issuer", "InstallerPath", "AllowedRoot"):
        _require(
            forbidden.casefold() not in result.casefold(),
            f"installer verifier result exposes sensitive identity or path data: {forbidden}",
        )


def _validate_policy(policy: dict) -> None:
    cka = policy["official_evidence"]["cka"]
    identity = cka["package_identity"]
    _require(cka["cka_package_integrated"] is True, "CKA integration state is false")
    _require(
        cka["immutable_package_identity_established"] is True,
        "immutable CKA identity state is false",
    )
    _require(
        cka["immutable_digest"] == EXPECTED_INSTALLER_IDENTITY["sha256"],
        "top-level CKA digest is not exact",
    )
    for key, expected in EXPECTED_INSTALLER_IDENTITY.items():
        _require(
            identity.get(key) == expected
            and type(identity.get(key)) is type(expected),
            f"pinned CKA identity differs: {key}",
        )
    _require(
        identity["filename_matches_release_label_exactly"] is False
        and identity["publisher_filename_discrepancy_documented"] is True,
        "official CKA filename discrepancy is not represented exactly",
    )
    _require(
        identity["timestamp_certificate_required"] is True,
        "CKA timestamp certificate requirement is false",
    )

    sandbox = policy["sandbox"]
    _require(sandbox["account_type"] == "sandbox", "sandbox account type changed")
    _require(
        sandbox["environment"] == "authenticode-sandbox",
        "protected sandbox environment changed",
    )
    _require(
        sandbox["automated_certificate_classes"] == ["OV", "EV"],
        "sandbox automated certificate classes changed",
    )
    _require(
        set(sandbox["expected_secret_names"]) == EXPECTED_SECRETS,
        "sandbox secret-name contract changed",
    )
    _require(
        set(sandbox["expected_variable_names"]) == EXPECTED_VARIABLES,
        "sandbox variable-name contract changed",
    )
    for key in (
        "certificate_class_selected_for_production",
        "production_certificate_provisioned",
        "workflow_live_validation_completed",
    ):
        _require(sandbox[key] is False, f"production or live sandbox claim is true: {key}")
    state = policy["state"]
    _require(state["sandbox_workflow_implemented"] is True, "sandbox workflow state is false")
    for key in (
        "account_provisioned",
        "certificate_provisioned",
        "credential_source_configured",
        "production_signing_authorized",
        "production_signing_completed",
        "publishing_allowed",
        "release_ready",
        "remote_signing_validated",
        "synthetic_signing_authorized",
        "timestamp_authority_approved",
    ):
        _require(state[key] is False, f"provisioning or production claim is true: {key}")


def _validate_documentation(signing: str, readiness: str) -> None:
    for marker in (
        "Phase 7D-R2-F1",
        "protected `authenticode-sandbox` environment",
        "actionless",
        "manual-only",
        "synthetic / v0.0.0-ci / non-production",
        "3f088403139505ddfb0ed3b56b72893f92c865f98b382753a1e1c695a5cece35",
        "16103264",
        "SSL.COM eSigner CKA_1.1.2_build_202600624.exe",
        "not represented as an SSL.com-published checksum",
        "filename discrepancy",
        "does not claim",
        "No protected workflow dispatch has occurred",
        "No production file is signed",
    ):
        _require(marker in signing, f"signing documentation is missing: {marker}")
    for marker in (
        "Phase 7D-R2-F1",
        "immutable CKA installer identity",
        "protected sandbox workflow scaffold",
        "workflow live validation remains false",
        "production signing remains blocked",
    ):
        _require(marker in readiness, f"readiness documentation is missing: {marker}")


def validate_sandbox_contract(
    workflow: str,
    installer_verifier: str,
    policy: dict,
    signing_documentation: str,
    readiness_documentation: str,
) -> None:
    workflow = supply._normalize_newlines(workflow)
    _validate_installer_verifier(installer_verifier)
    _validate_policy(policy)
    _validate_documentation(signing_documentation, readiness_documentation)

    lines = workflow.splitlines()
    triggers = supply._mapping_block(lines, "on", 0)
    _require(
        supply._direct_mapping_keys(triggers, 2) == ["workflow_dispatch"],
        "sandbox workflow must remain manual-only",
    )
    _require(
        supply._direct_mapping_pairs(
            supply._mapping_block(lines, "permissions", 0),
            2,
        )
        == [("contents", "read")],
        "sandbox workflow-global permissions are not read-only",
    )
    jobs = supply._mapping_block(lines, "jobs", 0)
    _require(
        supply._direct_mapping_keys(jobs, 2) == ["sandbox-signing"],
        "sandbox workflow job inventory differs",
    )
    job = supply._mapping_block(jobs, "sandbox-signing", 2)
    _require(
        supply._scalar_value(job, "environment", 4) == "authenticode-sandbox",
        "sandbox job does not use the protected environment",
    )
    _require(
        supply._direct_mapping_pairs(
            supply._mapping_block(job, "permissions", 4),
            6,
        )
        == [("contents", "read")],
        "sandbox job permissions are not read-only",
    )
    _require(
        supply._scalar_value(job, "runs-on", 4) == "windows-2022",
        "sandbox runner is not windows-2022",
    )
    _require(
        supply._scalar_value(job, "timeout-minutes", 4) == "30",
        "sandbox timeout is not exactly 30 minutes",
    )
    _require("uses:" not in workflow, "sandbox workflow must remain actionless")
    for forbidden in (
        "continue-on-error",
        "softprops/action-gh-release",
        "gh release",
        "git push",
        "upload-artifact",
        "id-token: write",
        "attestations: write",
        "contents: write",
        "Start-Process",
        "-Verb RunAs",
        "ESIGNER_DEMO",
    ):
        _require(
            forbidden.casefold() not in workflow.casefold(),
            f"sandbox workflow contains a publication, bypass, or elevation path: {forbidden}",
        )

    referenced_secrets = set(
        re.findall(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}", workflow)
    )
    referenced_variables = set(
        re.findall(r"\$\{\{\s*vars\.([A-Z0-9_]+)\s*\}\}", workflow)
    )
    _require(referenced_secrets == EXPECTED_SECRETS, "workflow secret references differ")
    _require(referenced_variables == EXPECTED_VARIABLES, "workflow variable references differ")

    steps = supply._step_blocks(job)
    names = []
    for step in steps:
        match = re.fullmatch(r"\s*-\s+name:\s*(.+?)\s*", step[0])
        _require(match is not None, "sandbox workflow contains an unnamed step")
        names.append(match.group(1))
    _require(names == EXPECTED_STEPS, "sandbox workflow step inventory or ordering differs")

    boundary = _step_text(steps, EXPECTED_STEPS[0])
    _require_markers(
        boundary,
        (
            '$env:GITHUB_EVENT_NAME -cne "workflow_dispatch"',
            '$env:GITHUB_REF -cne "refs/heads/main"',
            f'$env:GITHUB_REPOSITORY -cne "{UPSTREAM_REPOSITORY}"',
            '$env:GITHUB_SHA -cnotmatch "^[0-9a-f]{40}$"',
            "git init .",
            f"git remote add origin https://github.com/{UPSTREAM_REPOSITORY}.git",
            "git -c protocol.version=2 fetch --no-tags --depth=1 origin $env:GITHUB_SHA",
            "git checkout --detach FETCH_HEAD",
            "$CheckedOut -cne $env:GITHUB_SHA",
        ),
        "execution-boundary step",
    )
    _require(
        boundary.index('$env:GITHUB_REF -cne "refs/heads/main"')
        < boundary.index("git -c protocol.version=2 fetch"),
        "main-branch guard does not precede source fetch",
    )

    authorization_step = supply._named_step(steps, EXPECTED_STEPS[1])
    _require(
        supply._direct_mapping_pairs(
            supply._mapping_block(authorization_step, "env", 8),
            10,
        )
        == [
            ("SANDBOX_AUTHORIZED", "${{ vars.ESIGNER_SANDBOX_SIGNING_AUTHORIZED }}"),
            ("EXPECTED_CERTIFICATE_CLASS", "${{ vars.ESIGNER_SANDBOX_CERTIFICATE_CLASS }}"),
            ("EXPECTED_PUBLISHER", "${{ vars.ESIGNER_SANDBOX_EXPECTED_PUBLISHER }}"),
            ("EXPECTED_THUMBPRINT", "${{ vars.ESIGNER_SANDBOX_EXPECTED_THUMBPRINT }}"),
        ],
        "protected sandbox authorization inputs differ",
    )
    authorization = "\n".join(authorization_step)
    _require_markers(
        authorization,
        (
            '$env:SANDBOX_AUTHORIZED -cne "true"',
            '$env:EXPECTED_CERTIFICATE_CLASS -notin @("OV", "EV")',
            "IsNullOrWhiteSpace($env:EXPECTED_PUBLISHER)",
            '$NormalizedThumbprint -cnotmatch "^[0-9A-F]{40}$"',
        ),
        "protected sandbox authorization step",
    )

    isolation = _step_text(steps, EXPECTED_STEPS[2])
    _require_markers(
        isolation,
        (
            "$env:RUNNER_TEMP",
            "s9h-authenticode-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT",
            'Join-Path $SandboxRoot "download"',
            'Join-Path $SandboxRoot "cka"',
            'Join-Path $SandboxRoot "synthetic-signing"',
            'Join-Path $SandboxRoot "synthetic-build"',
            'Join-Path $SandboxRoot "master.key"',
            "icacls.exe $SandboxRoot /inheritance:r /grant:r",
        ),
        "isolated runner-state step",
    )

    download = _step_text(steps, EXPECTED_STEPS[3])
    _require_markers(
        download,
        (
            "$Policy.official_evidence.cka.package_identity",
            "$Identity.resource_display_filename",
            "Invoke-WebRequest -Uri $Identity.official_resource_url -OutFile $Installer",
            '"CKA_INSTALLER_PATH=$Installer"',
        ),
        "pinned installer download step",
    )

    verification = _step_text(steps, EXPECTED_STEPS[4])
    _require_markers(
        verification,
        (
            "& .\\scripts\\verify_esigner_cka_installer.ps1",
            "-InstallerPath $env:CKA_INSTALLER_PATH",
            "-AllowedRoot $env:CKA_DOWNLOAD_ROOT",
            '-ProviderPolicyPath "legal/authenticode-provider.json"',
            '$Verified.authenticode_status -cne "Valid"',
            '$Verified.architecture -cne "x86"',
            "$Verified.timestamp_certificate_present -ne $true",
        ),
        "installer verification step",
    )

    install = _step_text(steps, EXPECTED_STEPS[5])
    _require_markers(
        install,
        (
            "& $env:CKA_INSTALLER_PATH",
            "/CURRENTUSER",
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            '"/DIR=$env:CKA_INSTALL_ROOT"',
            'Get-ChildItem -LiteralPath $env:CKA_INSTALL_ROOT -Recurse -File -Filter "eSignerCKATool.exe"',
            "$Tools.Count -ne 1",
        ),
        "controlled CKA installation step",
    )
    _require(
        "& $env:CKA_INSTALLER_PATH"
        not in "\n".join("\n".join(step) for step in steps[:5]),
        "CKA installer executes before identity verification",
    )

    configuration_step = supply._named_step(steps, EXPECTED_STEPS[6])
    _require(
        supply._direct_mapping_pairs(
            supply._mapping_block(configuration_step, "env", 8),
            10,
        )
        == [
            ("CKA_USERNAME", "${{ secrets.ESIGNER_SANDBOX_USERNAME }}"),
            ("CKA_PASSWORD", "${{ secrets.ESIGNER_SANDBOX_PASSWORD }}"),
            ("CKA_TOTP_SECRET", "${{ secrets.ESIGNER_SANDBOX_TOTP_SECRET }}"),
        ],
        "protected CKA secret mapping differs",
    )
    configuration = "\n".join(configuration_step)
    _require_markers(
        configuration,
        (
            "IsNullOrWhiteSpace($Value)",
            "& $env:ESIGNER_CKA_TOOL config",
            "-mode sandbox",
            "-key $env:CKA_MASTER_KEY_PATH",
            "-r *> $null",
        ),
        "sandbox CKA configuration step",
    )

    certificate = _step_text(steps, EXPECTED_STEPS[7])
    _require_markers(
        certificate,
        (
            "Cert:\\CurrentUser\\My -CodeSigningCert",
            "$ActualThumbprint -ceq $env:SANDBOX_EXPECTED_THUMBPRINT",
            "$ActualPublisher -ceq $env:SANDBOX_EXPECTED_PUBLISHER",
            "$_.HasPrivateKey",
            "$Matches.Count -ne 1",
        ),
        "sandbox certificate-selection step",
    )

    subject = _step_text(steps, EXPECTED_STEPS[8])
    _require_markers(
        subject,
        (
            "<TargetFramework>net8.0</TargetFramework>",
            "<RuntimeIdentifier>win-x64</RuntimeIdentifier>",
            "<SelfContained>false</SelfContained>",
            "<AssemblyName>Youtube.Downloaderbs</AssemblyName>",
            "<Version>0.0.0-ci</Version>",
            "<Deterministic>true</Deterministic>",
            "<ContinuousIntegrationBuild>true</ContinuousIntegrationBuild>",
            "<PathMap>$(MSBuildProjectDirectory)=/_/synthetic</PathMap>",
            "dotnet publish $Project",
            "$FirstHash -cne $SecondHash",
            '$Targets[0].Name -cne "Youtube.Downloaderbs.exe"',
            "[Management.Automation.SignatureStatus]::NotSigned",
        ),
        "deterministic synthetic-subject step",
    )
    _require(subject.count("dotnet publish $Project") == 2, "synthetic build count differs")

    signtool = _step_text(steps, EXPECTED_STEPS[9])
    _require_markers(
        signtool,
        (
            '[Environment]::GetFolderPath("ProgramFilesX86")',
            '"Windows Kits\\10"',
            'Where-Object { $_.FullName -match "\\\\x64\\\\signtool\\.exe$" }',
            "Sort-Object @{Expression = { $_.Version }; Descending = $true}",
            "$Candidates.Count -eq 0",
        ),
        "deterministic SignTool-discovery step",
    )

    signing = _step_text(steps, EXPECTED_STEPS[10])
    _require_markers(
        signing,
        (
            "$Policy.state.synthetic_signing_authorized = $true",
            "$Policy.state.timestamp_authority_approved = $true",
            "$Policy.timestamp.authority_approved = $true",
            "& .\\scripts\\sign_authenticode.ps1",
            "-SigningPurpose synthetic",
            '-TimestampUrl "http://ts.ssl.com"',
            "$SignedHash -ceq $UnsignedHash",
            '$Verification.target -cne "Youtube.Downloaderbs.exe"',
            "$Verification.sha256 -cne $SignedHash",
            '$Verification.verification_policy -cne "Default Authenticode /pa"',
            '$Verification.timestamp_protocol -cne "RFC3161"',
            '$Verification.timestamp_digest -cne "SHA256"',
            "$Verification.timestamp_verified -ne $true",
            "$Verification.publisher -cne $env:SANDBOX_EXPECTED_PUBLISHER",
            "$Verification.downstream_packaging_allowed_for_recorded_hash -ne $true",
            "synthetic / v0.0.0-ci / non-production",
            "Unsigned SHA-256",
            "Signed SHA-256",
        ),
        "synthetic signing and verification step",
    )
    for forbidden in (
        "$Policy.state.production_signing_authorized = $true",
        "$Policy.state.production_signing_completed = $true",
        "$Policy.state.remote_signing_validated = $true",
        "$Policy.state.publishing_allowed = $true",
        "$Policy.state.release_ready = $true",
        "-SigningPurpose production",
    ):
        _require(forbidden not in signing, f"synthetic workflow enables production state: {forbidden}")

    cleanup_step = supply._named_step(steps, EXPECTED_STEPS[11])
    _require(
        supply._scalar_value(cleanup_step, "if", 8) == "always()",
        "sandbox cleanup is not unconditional",
    )
    cleanup = "\n".join(cleanup_step)
    _require_markers(
        cleanup,
        (
            "& $env:ESIGNER_CKA_TOOL unload",
            'Get-ChildItem -LiteralPath $env:CKA_INSTALL_ROOT -File -Filter "unins*.exe"',
            "$CkaInstalled -and $Uninstallers.Count -ne 1",
            "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
            '$CleanupFailures.Add("CKA unload failed")',
            '$CleanupFailures.Add("CKA uninstaller failed")',
            "$SandboxRoot.StartsWith(",
            "$RunnerRoot + [IO.Path]::DirectorySeparatorChar",
            "Remove-Item -LiteralPath $SandboxRoot -Recurse -Force -ErrorAction Stop",
            "$CleanupFailures.Count -ne 0",
        ),
        "sandbox cleanup step",
    )


Mutation = Callable[[dict], None]


def _workflow_replace(old: str, new: str) -> Mutation:
    def mutate(state: dict) -> None:
        state["workflow"] = _replace_once(state["workflow"], old, new)

    return mutate


def _installer_replace(old: str, new: str) -> Mutation:
    def mutate(state: dict) -> None:
        state["installer_verifier"] = _replace_once(
            state["installer_verifier"],
            old,
            new,
        )

    return mutate


def _policy_set(path: tuple[str, ...], value: object) -> Mutation:
    def mutate(state: dict) -> None:
        target = state["policy"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _remove_document_marker(document: str, marker: str) -> Mutation:
    def mutate(state: dict) -> None:
        state[document] = _replace_once(state[document], marker, "removed-marker")

    return mutate


def _run_negative(
    label: str,
    mutation: Mutation,
    expected: str,
    baseline: dict,
) -> None:
    state = copy.deepcopy(baseline)
    mutation(state)
    try:
        validate_sandbox_contract(
            state["workflow"],
            state["installer_verifier"],
            state["policy"],
            state["signing_documentation"],
            state["readiness_documentation"],
        )
    except SandboxWorkflowContractError as exc:
        _require(
            expected.casefold() in str(exc).casefold(),
            f"{label}: expected {expected!r}, got {exc!r}",
        )
    else:
        raise AssertionError(f"{label}: mutation unexpectedly passed")
    print(f"PASS negative: {label}")


def main() -> int:
    baseline = {
        "workflow": WORKFLOW_PATH.read_text(encoding="utf-8"),
        "installer_verifier": INSTALLER_VERIFIER_PATH.read_text(encoding="utf-8"),
        "policy": json.loads(PROVIDER_POLICY_PATH.read_text(encoding="utf-8")),
        "signing_documentation": SIGNING_DOCUMENT_PATH.read_text(encoding="utf-8"),
        "readiness_documentation": READINESS_DOCUMENT_PATH.read_text(encoding="utf-8"),
    }
    validate_sandbox_contract(
        baseline["workflow"],
        baseline["installer_verifier"],
        baseline["policy"],
        baseline["signing_documentation"],
        baseline["readiness_documentation"],
    )
    positive_labels = [
        "manual protected environment",
        "actionless exact-source checkout",
        "read-only permissions",
        "pinned CKA package identity",
        "network-free installer verifier",
        "verify-before-execute ordering",
        "protected sandbox credentials",
        "unique exact certificate selection",
        "deterministic synthetic subject",
        "synthetic-only signing purpose",
        "post-sign verification evidence",
        "unconditional contained cleanup",
        "production non-claims",
    ]
    for label in positive_labels:
        print(f"PASS positive: {label}")

    cases: list[tuple[str, Mutation, str]] = [
        (
            "automatic push trigger",
            _workflow_replace("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n"),
            "manual-only",
        ),
        (
            "unprotected job",
            _workflow_replace("    environment: authenticode-sandbox", "    environment: production"),
            "protected environment",
        ),
        (
            "workflow write permission",
            _workflow_replace("permissions:\n  contents: read", "permissions:\n  contents: write"),
            "workflow-global permissions",
        ),
        (
            "job write permission",
            _workflow_replace("    permissions:\n      contents: read", "    permissions:\n      contents: write"),
            "job permissions",
        ),
        (
            "third-party action added",
            _workflow_replace("    steps:\n", "    steps:\n      - uses: actions/checkout@v4\n"),
            "actionless",
        ),
        (
            "main guard relaxed",
            _workflow_replace('if ($env:GITHUB_REF -cne "refs/heads/main")', 'if ($false)'),
            "execution-boundary",
        ),
        (
            "upstream guard relaxed",
            _workflow_replace(
                f'if ($env:GITHUB_REPOSITORY -cne "{UPSTREAM_REPOSITORY}")',
                'if ($false)',
            ),
            "execution-boundary",
        ),
        (
            "sandbox authorization relaxed",
            _workflow_replace('if ($env:SANDBOX_AUTHORIZED -cne "true")', 'if ($false)'),
            "authorization step",
        ),
        (
            "IV automation accepted",
            _workflow_replace('@("OV", "EV")', '@("IV", "OV", "EV")'),
            "authorization step",
        ),
        (
            "unexpected credential reference",
            _workflow_replace(
                "secrets.ESIGNER_SANDBOX_PASSWORD",
                "secrets.ESIGNER_DEMO_PASSWORD",
            ),
            "publication, bypass, or elevation",
        ),
        (
            "installer verification bypass",
            _workflow_replace(
                "      - name: Verify exact CKA installer identity before execution\n",
                "      - name: Verify exact CKA installer identity before execution\n        continue-on-error: true\n",
            ),
            "publication, bypass, or elevation",
        ),
        (
            "installer elevation added",
            _workflow_replace(
                "& $env:CKA_INSTALLER_PATH `",
                "Start-Process -Verb RunAs $env:CKA_INSTALLER_PATH\n          & $env:CKA_INSTALLER_PATH `",
            ),
            "publication, bypass, or elevation",
        ),
        (
            "certificate private-key gate removed",
            _workflow_replace("                          $_.HasPrivateKey", "                          $true"),
            "certificate-selection",
        ),
        (
            "certificate uniqueness relaxed",
            _workflow_replace("if ($Matches.Count -ne 1)", "if ($Matches.Count -eq 0)"),
            "certificate-selection",
        ),
        (
            "deterministic flag removed",
            _workflow_replace(
                "<Deterministic>true</Deterministic>",
                "<Deterministic>false</Deterministic>",
            ),
            "synthetic-subject",
        ),
        (
            "two-build hash comparison removed",
            _workflow_replace("if ($FirstHash -cne $SecondHash)", "if ($false)"),
            "synthetic-subject",
        ),
        (
            "production signing purpose substituted",
            _workflow_replace("-SigningPurpose synthetic", "-SigningPurpose production"),
            "signing and verification",
        ),
        (
            "timestamp verification relaxed",
            _workflow_replace(
                "$Verification.timestamp_verified -ne $true",
                "$Verification.timestamp_verified -eq $false",
            ),
            "signing and verification",
        ),
        (
            "cleanup no longer unconditional",
            _workflow_replace("        if: always()", "        if: success()"),
            "cleanup is not unconditional",
        ),
        (
            "installer digest verification removed",
            _installer_replace(
                "Get-FileHash -LiteralPath $Authorized.FullName -Algorithm SHA256",
                "Get-Item -LiteralPath $Authorized.FullName",
            ),
            "installer verifier",
        ),
        (
            "installer signature verification removed",
            _installer_replace(
                "Get-AuthenticodeSignature -LiteralPath $Authorized.FullName",
                "Get-Item -LiteralPath $Authorized.FullName",
            ),
            "installer verifier",
        ),
        (
            "installer verifier network access",
            _installer_replace(
                '$ErrorActionPreference = "Stop"',
                '$ErrorActionPreference = "Stop"\nInvoke-WebRequest "https://example.invalid"',
            ),
            "network-free",
        ),
        (
            "signer thumbprint gate removed",
            _installer_replace("signer_thumbprint", "removed_thumbprint_gate"),
            "installer verifier",
        ),
        (
            "timestamp certificate gate removed",
            _installer_replace("TimeStamperCertificate", "RemovedTimestampGate"),
            "installer verifier",
        ),
        (
            "CKA digest changed",
            _policy_set(("official_evidence", "cka", "package_identity", "sha256"), "0" * 64),
            "pinned CKA identity",
        ),
        (
            "filename discrepancy hidden",
            _policy_set(
                (
                    "official_evidence",
                    "cka",
                    "package_identity",
                    "publisher_filename_discrepancy_documented",
                ),
                False,
            ),
            "filename discrepancy",
        ),
        (
            "sandbox live validation claimed",
            _policy_set(("sandbox", "workflow_live_validation_completed"), True),
            "production or live sandbox claim",
        ),
        (
            "production signing claimed",
            _policy_set(("state", "production_signing_completed"), True),
            "provisioning or production claim",
        ),
        (
            "signing non-claim documentation removed",
            _remove_document_marker("signing_documentation", "No production file is signed"),
            "signing documentation",
        ),
        (
            "readiness blocker documentation removed",
            _remove_document_marker("readiness_documentation", "production signing remains blocked"),
            "readiness documentation",
        ),
    ]
    for label, mutation, expected in cases:
        _run_negative(label, mutation, expected, baseline)

    print(
        "Authenticode sandbox workflow smoke passed: "
        f"{len(positive_labels)} positive contracts, {len(cases)} negative mutations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
