from __future__ import annotations

import copy
import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from verify_authenticode_policy import (
    COMPONENTS_PATH,
    PROVIDER_POLICY_PATH,
    RELEASE_POLICY_PATH,
    SANDBOX_WORKFLOW_PATH,
    SIGN_SCRIPT_PATH,
    INSTALLER_VERIFY_SCRIPT_PATH,
    VERIFY_SCRIPT_PATH,
    WINDOWS_CHECKOUT_PROJECTION_PATHS,
    PolicyError,
    verify_authenticode_policy,
)


Mutation = Callable[[dict[str, Any]], None]
RawMutation = Callable[[bytes], bytes]


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _set(path: tuple[str, ...], value: Any) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: Any = policy
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _add(path: tuple[str, ...], key: str, value: Any) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: Any = policy
        for part in path:
            target = target[part]
        target[key] = value

    return mutate


def _insert_sorted(path: tuple[str, ...], value: str) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: Any = policy
        for part in path:
            target = target[part]
        target.append(value)
        target.sort()

    return mutate


def _delete_sequence_item(value: str) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        policy["signing_contract"]["sequence"].remove(value)

    return mutate


def _swap_sequence(first: str, second: str) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        sequence = policy["signing_contract"]["sequence"]
        first_index = sequence.index(first)
        second_index = sequence.index(second)
        sequence[first_index], sequence[second_index] = sequence[second_index], sequence[first_index]

    return mutate


def _replace(old: bytes, new: bytes) -> RawMutation:
    def mutate(raw: bytes) -> bytes:
        if raw.count(old) != 1:
            raise AssertionError(f"expected one script marker, found {raw.count(old)}: {old!r}")
        return raw.replace(old, new)

    return mutate


def _load_json(root: Path, relative: Path) -> dict[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _copy_fixture_root(repository_root: Path, destination: Path) -> None:
    for relative in (
        PROVIDER_POLICY_PATH,
        RELEASE_POLICY_PATH,
        COMPONENTS_PATH,
        SIGN_SCRIPT_PATH,
        INSTALLER_VERIFY_SCRIPT_PATH,
        VERIFY_SCRIPT_PATH,
        SANDBOX_WORKFLOW_PATH,
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repository_root / relative).read_bytes())


def _run_positive(name: str, root: Path) -> None:
    verify_authenticode_policy(root)
    print(f"PASS positive: {name}")


def _run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed for EOL regression: {result.stderr.strip()}"
        )
    return result


def _initialize_git_fixture(root: Path) -> None:
    _run_git(root, "init", "--quiet")
    _run_git(root, "config", "core.autocrlf", "false")
    _run_git(root, "add", "--all")
    _run_git(
        root,
        "-c",
        "user.name=Phase 7D EOL Regression",
        "-c",
        "user.email=phase-7d-eol@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "EOL regression fixture",
    )


def _read_git_blob(root: Path, relative: Path) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"HEAD:{relative.as_posix()}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"could not read committed fixture blob: {relative.as_posix()}")
    return result.stdout


def _expect_eol_rejection(name: str, root: Path, required_text: str | None = None) -> None:
    try:
        verify_authenticode_policy(root)
    except PolicyError as exc:
        if exc.category != "script-hygiene":
            raise AssertionError(
                f"{name}: expected script-hygiene, got {exc.category}: {exc}"
            ) from exc
        if required_text is not None and required_text not in str(exc):
            raise AssertionError(f"{name}: unexpected rejection text: {exc}") from exc
    else:
        raise AssertionError(f"{name}: invalid worktree representation unexpectedly passed")
    print(f"PASS negative: {name} [script-hygiene]")


def _run_checkout_eol_regressions(repository_root: Path) -> int:
    projection_paths = (
        SIGN_SCRIPT_PATH,
        VERIFY_SCRIPT_PATH,
        INSTALLER_VERIFY_SCRIPT_PATH,
        SANDBOX_WORKFLOW_PATH,
    )
    if frozenset(projection_paths) != WINDOWS_CHECKOUT_PROJECTION_PATHS:
        raise AssertionError("Windows checkout projection path ownership changed")

    with tempfile.TemporaryDirectory(prefix="s9h-authenticode-policy-eol-") as temporary:
        root = Path(temporary)
        _copy_fixture_root(repository_root, root)
        _initialize_git_fixture(root)
        canonical = {relative: (root / relative).read_bytes() for relative in projection_paths}
        for relative, raw in canonical.items():
            if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
                raise AssertionError(f"{relative.as_posix()} committed fixture is not LF-only")
            if raw != _read_git_blob(root, relative):
                raise AssertionError(f"{relative.as_posix()} LF worktree differs from its Git blob")
        _run_positive("exact LF worktree equals committed blobs", root)

        powershell_paths = (
            SIGN_SCRIPT_PATH,
            VERIFY_SCRIPT_PATH,
            INSTALLER_VERIFY_SCRIPT_PATH,
        )
        for relative in powershell_paths:
            (root / relative).write_bytes(canonical[relative].replace(b"\n", b"\r\n"))
        _run_positive("proven PowerShell CRLF checkout projections", root)

        workflow_path = root / SANDBOX_WORKFLOW_PATH
        workflow_lf = canonical[SANDBOX_WORKFLOW_PATH]
        workflow_crlf = workflow_lf.replace(b"\n", b"\r\n")
        workflow_path.write_bytes(workflow_crlf)
        _run_positive("proven sandbox workflow CRLF checkout projection", root)

        sign_path = root / SIGN_SCRIPT_PATH
        arbitrary_sign = sign_path.read_bytes().replace(
            b"Set-StrictMode -Version Latest\r\n",
            b"Set-StrictMode -Version 1\r\n",
            1,
        )
        if arbitrary_sign == sign_path.read_bytes():
            raise AssertionError("could not create arbitrary PowerShell CRLF regression fixture")
        sign_path.write_bytes(arbitrary_sign)
        _expect_eol_rejection("arbitrary PowerShell CRLF content", root, "full CRLF projection")
        sign_path.write_bytes(canonical[SIGN_SCRIPT_PATH].replace(b"\n", b"\r\n"))

        workflow_cases = (
            ("arbitrary workflow CRLF content", b"not the sandbox workflow\r\n"),
            (
                "one-byte workflow mutation followed by CRLF conversion",
                workflow_lf.replace(b"timeout-minutes: 30", b"timeout-minutes: 31", 1).replace(
                    b"\n", b"\r\n"
                ),
            ),
            ("mixed workflow LF and CRLF", workflow_lf.replace(b"\n", b"\r\n", 1)),
            ("workflow lone CR", workflow_lf.replace(b"\n", b"\r", 1)),
            ("workflow UTF-8 BOM", b"\xef\xbb\xbf" + workflow_crlf),
            (
                "valid but uncommitted workflow YAML",
                (b"# valid YAML but not the committed blob\n" + workflow_lf).replace(
                    b"\n", b"\r\n"
                ),
            ),
        )
        for name, invalid in workflow_cases:
            if invalid == workflow_crlf:
                raise AssertionError(f"{name}: fixture did not differ from the valid projection")
            workflow_path.write_bytes(invalid)
            _expect_eol_rejection(name, root, "full CRLF projection")

    with tempfile.TemporaryDirectory(prefix="s9h-authenticode-policy-eol-no-git-") as temporary:
        root = Path(temporary)
        _copy_fixture_root(repository_root, root)
        workflow_path = root / SANDBOX_WORKFLOW_PATH
        workflow_path.write_bytes(workflow_path.read_bytes().replace(b"\n", b"\r\n"))
        _expect_eol_rejection(
            "workflow authoritative Git blob unavailable",
            root,
            "could not be proven against Git HEAD",
        )
    return 8


def _run_negative(
    name: str,
    expected_category: str,
    repository_root: Path,
    *,
    provider_mutation: Mutation | None = None,
    release_mutation: Mutation | None = None,
    components_mutation: Mutation | None = None,
    script_path: Path | None = None,
    script_mutation: RawMutation | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-authenticode-policy-negative-") as temporary:
        root = Path(temporary)
        _copy_fixture_root(repository_root, root)
        for relative, mutation in (
            (PROVIDER_POLICY_PATH, provider_mutation),
            (RELEASE_POLICY_PATH, release_mutation),
            (COMPONENTS_PATH, components_mutation),
        ):
            if mutation is None:
                continue
            value = _load_json(root, relative)
            mutation(value)
            (root / relative).write_bytes(_canonical_bytes(value))
        if script_path is not None and script_mutation is not None:
            path = root / script_path
            path.write_bytes(script_mutation(path.read_bytes()))
        _initialize_git_fixture(root)
        try:
            verify_authenticode_policy(root)
        except PolicyError as exc:
            if exc.category != expected_category:
                raise AssertionError(
                    f"{name}: expected {expected_category}, got {exc.category}: {exc}"
                ) from exc
        else:
            raise AssertionError(f"{name}: mutation unexpectedly passed")
    print(f"PASS negative: {name} [{expected_category}]")


def _write_unsigned_pe(path: Path) -> None:
    data = bytearray(512)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x84, 0x8664)
    struct.pack_into("<H", data, 0x86, 1)
    struct.pack_into("<H", data, 0x94, 0xF0)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 108, 16)
    path.write_bytes(data)


def _powershell_command() -> str:
    for candidate in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise AssertionError("PowerShell is unavailable")


def _invoke_sign(
    powershell: str,
    repository_root: Path,
    release_root: Path,
    target: Path,
    *,
    sign_script: Path | None = None,
    provider_config: Path | None = None,
    signing_purpose: str | None = None,
    release_policy: Path | None = None,
    expected_publisher: str | None = None,
    certificate_thumbprint: str | None = None,
    signtool: Path | None = None,
    plan_only: bool = True,
) -> subprocess.CompletedProcess[str]:
    script = sign_script or repository_root / SIGN_SCRIPT_PATH
    config = provider_config or repository_root / PROVIDER_POLICY_PATH
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Target",
        str(target),
        "-ReleaseRoot",
        str(release_root),
        "-ProviderConfigPath",
        str(config),
        "-TimestampUrl",
        "http://ts.ssl.com",
    ]
    if plan_only:
        command.append("-PlanOnly")
    if signing_purpose is not None:
        command.extend(["-SigningPurpose", signing_purpose])
    if release_policy is not None:
        command.extend(["-ReleaseAssurancePolicyPath", str(release_policy)])
    if expected_publisher is not None:
        command.extend(["-ExpectedPublisher", expected_publisher])
    if certificate_thumbprint is not None:
        command.extend(["-CertificateThumbprint", certificate_thumbprint])
    if signtool is not None:
        command.extend(["-SignToolPath", str(signtool)])
    return subprocess.run(
        command,
        cwd=repository_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "NO_COLOR": "1"},
    )


def _write_fake_signtool(path: Path) -> None:
    path.write_text(
        "@echo off\n"
        "echo Successfully verified\n"
        "echo Timestamp Verified by:\n"
        "echo RFC3161 SHA256\n"
        "echo Example Publisher LLC\n"
        "exit /b 0\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_signing_fixture_scripts(repository_root: Path, root: Path) -> tuple[Path, Path]:
    scripts = root / "scripts"
    scripts.mkdir()
    sign_script = scripts / SIGN_SCRIPT_PATH.name
    sign_script.write_bytes((repository_root / SIGN_SCRIPT_PATH).read_bytes())
    verify_script = scripts / VERIFY_SCRIPT_PATH.name
    verify_script.write_text(
        "param(\n"
        "    [string]$Target,\n"
        "    [string]$ReleaseRoot,\n"
        "    [string]$SignToolPath,\n"
        "    [string]$ExpectedPublisher,\n"
        "    [string]$CertificateThumbprint\n"
        ")\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    signtool = root / "signtool.cmd"
    _write_fake_signtool(signtool)
    return sign_script, signtool


def _provisioned_synthetic_policy(repository_root: Path, destination: Path) -> dict[str, Any]:
    provider = _load_json(repository_root, PROVIDER_POLICY_PATH)
    for key in (
        "account_provisioned",
        "certificate_provisioned",
        "credential_source_configured",
        "synthetic_signing_authorized",
        "timestamp_authority_approved",
    ):
        provider["state"][key] = True
    provider["timestamp"]["authority_approved"] = True
    provider["certificate"]["expected_publisher"] = "Example Publisher LLC"
    provider["certificate"]["expected_thumbprint"] = "A1" * 20
    destination.write_bytes(_canonical_bytes(provider))
    return provider


def _write_verification_harness(
    path: Path,
    verify_script: Path,
    target: Path,
    release_root: Path,
    signtool: Path,
) -> None:
    def quote(value: Path) -> str:
        return str(value).replace("'", "''")

    actual_thumbprint = " ".join(["A1"] * 20)
    path.write_text(
        "param([string]$ExpectedPublisher, [string]$CertificateThumbprint)\n"
        "$ErrorActionPreference = 'Stop'\n"
        f"$Certificate = [PSCustomObject]@{{ Thumbprint = '{actual_thumbprint}' }}\n"
        "$Certificate | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value {\n"
        "    param($NameType, $ForIssuer)\n"
        "    return 'Example Publisher LLC'\n"
        "}\n"
        "$global:FixtureSignature = [PSCustomObject]@{\n"
        "    Status = [System.Management.Automation.SignatureStatus]::Valid\n"
        "    SignerCertificate = $Certificate\n"
        "    TimeStamperCertificate = [PSCustomObject]@{}\n"
        "}\n"
        "function Get-AuthenticodeSignature { param([string]$LiteralPath) return $global:FixtureSignature }\n"
        "function Get-FileHash { param([string]$LiteralPath, [string]$Algorithm) "
        "return [PSCustomObject]@{ Hash = ('00' * 32) } }\n"
        f"& '{quote(verify_script)}' -Target '{quote(target)}' -ReleaseRoot '{quote(release_root)}' "
        f"-SignToolPath '{quote(signtool)}' -ExpectedPublisher $ExpectedPublisher "
        "-CertificateThumbprint $CertificateThumbprint\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
        newline="\n",
    )


def _invoke_verification_harness(
    powershell: str,
    harness: Path,
    expected_publisher: str,
    certificate_thumbprint: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ExpectedPublisher",
            expected_publisher,
            "-CertificateThumbprint",
            certificate_thumbprint,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "NO_COLOR": "1"},
    )


def _run_wrapper_fixtures(repository_root: Path) -> int:
    powershell = _powershell_command()
    fixtures = 0
    with tempfile.TemporaryDirectory(prefix="s9h-authenticode-wrapper-") as temporary:
        temporary_root = Path(temporary)
        release_root = temporary_root / "release"
        release_root.mkdir()
        target = release_root / "Youtube.Downloaderbs.exe"
        _write_unsigned_pe(target)

        first = _invoke_sign(powershell, repository_root, release_root, target)
        second = _invoke_sign(powershell, repository_root, release_root, target)
        if first.returncode != 0 or second.returncode != 0:
            raise AssertionError(f"PlanOnly failed: {first.stderr or second.stderr}")
        if first.stdout != second.stdout:
            raise AssertionError("PlanOnly output is not deterministic")
        plan = json.loads(first.stdout.strip())
        expected = {
            "mode": "plan-only",
            "provider": "ssl-com-esigner",
            "target": "Youtube.Downloaderbs.exe",
            "sign_command": (
                "signtool.exe sign /fd SHA256 /tr <approved-rfc3161-url> "
                "/td SHA256 /sha1 <redacted-certificate-selector> Youtube.Downloaderbs.exe"
            ),
            "verify_command": "signtool.exe verify /pa /all /v /tw Youtube.Downloaderbs.exe",
            "expected_publisher": "<required-after-provisioning>",
            "invokes_signtool": False,
        }
        if plan != expected:
            raise AssertionError(f"PlanOnly output changed: {plan!r}")
        combined = first.stdout + first.stderr
        if str(temporary_root) in combined or str(repository_root) in combined:
            raise AssertionError("PlanOnly output exposed an absolute path")
        for forbidden in ("password", "totp", "private key", "thumbprint"):
            if forbidden in combined.casefold():
                raise AssertionError(f"PlanOnly output exposed forbidden text: {forbidden}")
        print("PASS wrapper fixture: deterministic sanitized PlanOnly")
        fixtures += 1

        real_attempt = _invoke_sign(
            powershell,
            repository_root,
            release_root,
            target,
            signing_purpose="synthetic",
            plan_only=False,
        )
        if real_attempt.returncode == 0 or "Synthetic signing prerequisites" not in real_attempt.stderr:
            raise AssertionError("real signing did not fail closed on R1 readiness")
        print("PASS wrapper fixture: real signing blocked before SignTool")
        fixtures += 1

        missing_purpose = _invoke_sign(
            powershell,
            repository_root,
            release_root,
            target,
            plan_only=False,
        )
        if missing_purpose.returncode == 0 or "real signing purpose" not in missing_purpose.stderr:
            raise AssertionError("real signing accepted an implicit purpose")
        print("PASS wrapper fixture: real signing requires explicit purpose")
        fixtures += 1

        signing_root = temporary_root / "synthetic-fixture"
        signing_root.mkdir()
        fixture_release = signing_root / "release"
        fixture_release.mkdir()
        fixture_target = fixture_release / "Youtube.Downloaderbs.exe"
        _write_unsigned_pe(fixture_target)
        sign_script, fake_signtool = _write_signing_fixture_scripts(repository_root, signing_root)
        provider_path = signing_root / "provider.json"
        provider = _provisioned_synthetic_policy(repository_root, provider_path)
        normalized_input = " ".join(["a1"] * 20)
        synthetic = _invoke_sign(
            powershell,
            repository_root,
            fixture_release,
            fixture_target,
            sign_script=sign_script,
            provider_config=provider_path,
            signing_purpose="synthetic",
            expected_publisher="Example Publisher LLC",
            certificate_thumbprint=normalized_input,
            signtool=fake_signtool,
            plan_only=False,
        )
        if synthetic.returncode != 0:
            raise AssertionError(f"synthetic signing fixture failed: {synthetic.stderr}")
        if provider["state"]["production_signing_authorized"] or provider["state"]["remote_signing_validated"]:
            raise AssertionError("synthetic signing fixture enabled a production-only gate")
        print("PASS wrapper fixture: synthetic mode excludes production authorization and prior remote validation")
        fixtures += 1

        provider["state"]["production_signing_authorized"] = True
        provider["state"]["remote_signing_validated"] = False
        provider_path.write_bytes(_canonical_bytes(provider))
        missing_remote = _invoke_sign(
            powershell,
            repository_root,
            fixture_release,
            fixture_target,
            sign_script=sign_script,
            provider_config=provider_path,
            signing_purpose="production",
            expected_publisher="Example Publisher LLC",
            certificate_thumbprint=normalized_input,
            signtool=fake_signtool,
            plan_only=False,
        )
        if missing_remote.returncode == 0 or "Remote synthetic signing validation" not in missing_remote.stderr:
            raise AssertionError("production mode did not require prior remote validation")
        print("PASS wrapper fixture: production mode requires remote validation")
        fixtures += 1

        provider["state"]["production_signing_authorized"] = False
        provider["state"]["remote_signing_validated"] = True
        provider_path.write_bytes(_canonical_bytes(provider))
        missing_authorization = _invoke_sign(
            powershell,
            repository_root,
            fixture_release,
            fixture_target,
            sign_script=sign_script,
            provider_config=provider_path,
            signing_purpose="production",
            expected_publisher="Example Publisher LLC",
            certificate_thumbprint=normalized_input,
            signtool=fake_signtool,
            plan_only=False,
        )
        if missing_authorization.returncode == 0 or "Production signing authorization" not in missing_authorization.stderr:
            raise AssertionError("production mode did not require production authorization")
        print("PASS wrapper fixture: production mode requires production authorization")
        fixtures += 1

        provider["state"]["production_signing_authorized"] = True
        provider["state"]["remote_signing_validated"] = True
        provider_path.write_bytes(_canonical_bytes(provider))
        missing_release_policy = _invoke_sign(
            powershell,
            repository_root,
            fixture_release,
            fixture_target,
            sign_script=sign_script,
            provider_config=provider_path,
            signing_purpose="production",
            expected_publisher="Example Publisher LLC",
            certificate_thumbprint=normalized_input,
            signtool=fake_signtool,
            plan_only=False,
        )
        if missing_release_policy.returncode == 0 or "explicit release-assurance policy" not in missing_release_policy.stderr:
            raise AssertionError("production mode accepted a missing release-assurance policy")
        print("PASS wrapper fixture: production mode requires explicit release policy")
        fixtures += 1

        blocked_release_gate = _invoke_sign(
            powershell,
            repository_root,
            fixture_release,
            fixture_target,
            sign_script=sign_script,
            provider_config=provider_path,
            signing_purpose="production",
            release_policy=repository_root / RELEASE_POLICY_PATH,
            expected_publisher="Example Publisher LLC",
            certificate_thumbprint=normalized_input,
            signtool=fake_signtool,
            plan_only=False,
        )
        if blocked_release_gate.returncode == 0 or "independent production release gate" not in blocked_release_gate.stderr:
            raise AssertionError("production mode accepted incomplete independent release gates")
        print("PASS wrapper fixture: production mode requires independent release gates")
        fixtures += 1

        for name in ("yt-dlp.exe", "arbitrary.exe"):
            rejected = release_root / name
            _write_unsigned_pe(rejected)
            result = _invoke_sign(powershell, repository_root, release_root, rejected)
            if result.returncode == 0:
                raise AssertionError(f"unauthorized target passed PlanOnly: {name}")
            print(f"PASS wrapper fixture: rejected {name}")
            fixtures += 1

        outside = temporary_root / "Youtube.Downloaderbs.exe"
        _write_unsigned_pe(outside)
        outside_result = _invoke_sign(powershell, repository_root, release_root, outside)
        if outside_result.returncode == 0:
            raise AssertionError("outside-root target passed PlanOnly")
        print("PASS wrapper fixture: rejected outside-root target")
        fixtures += 1

        verify_root = temporary_root / "verification-fixture"
        verify_root.mkdir()
        verify_release = verify_root / "release"
        verify_release.mkdir()
        verify_target = verify_release / "Youtube.Downloaderbs.exe"
        _write_unsigned_pe(verify_target)
        verify_signtool = verify_root / "signtool.cmd"
        _write_fake_signtool(verify_signtool)
        harness = verify_root / "verify-harness.ps1"
        _write_verification_harness(
            harness,
            repository_root / VERIFY_SCRIPT_PATH,
            verify_target,
            verify_release,
            verify_signtool,
        )
        expected_thumbprint = " ".join(["a1"] * 20)
        verified = _invoke_verification_harness(
            powershell,
            harness,
            "Example Publisher LLC",
            expected_thumbprint,
        )
        if verified.returncode != 0:
            raise AssertionError(f"exact identity verification fixture failed: {verified.stderr}")
        if "a1a1a1" in (verified.stdout + verified.stderr).casefold() or "thumbprint" in verified.stdout.casefold():
            raise AssertionError("verification output exposed the certificate selector")
        print("PASS wrapper fixture: exact signer thumbprint and publisher identity")
        fixtures += 1

        mismatched_thumbprint = _invoke_verification_harness(
            powershell,
            harness,
            "Example Publisher LLC",
            "B2" * 20,
        )
        if mismatched_thumbprint.returncode == 0 or "expected identity" not in mismatched_thumbprint.stderr:
            raise AssertionError("mismatched signer thumbprint was accepted")
        print("PASS wrapper fixture: rejected mismatched signer thumbprint")
        fixtures += 1

        substring_publisher = _invoke_verification_harness(
            powershell,
            harness,
            "Example Publisher",
            expected_thumbprint,
        )
        if substring_publisher.returncode == 0 or "exactly match" not in substring_publisher.stderr:
            raise AssertionError("publisher substring-only match was accepted")
        print("PASS wrapper fixture: rejected publisher substring-only match")
        fixtures += 1

    return fixtures


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    _run_positive("repository Authenticode policy and scripts", root)
    with tempfile.TemporaryDirectory(prefix="s9h-authenticode-policy-positive-") as temporary:
        copied_root = Path(temporary)
        _copy_fixture_root(root, copied_root)
        provider = _load_json(copied_root, PROVIDER_POLICY_PATH)
        (copied_root / PROVIDER_POLICY_PATH).write_bytes(_canonical_bytes(provider))
        _initialize_git_fixture(copied_root)
        _run_positive("canonical round-trip fixture", copied_root)
    eol_negative_count = _run_checkout_eol_regressions(root)

    cases: list[tuple[str, str, dict[str, Any]]] = [
        (
            "exportable PFX custody",
            "custody",
            {"provider_mutation": _set(("custody", "private_key_export_allowed"), True)},
        ),
        (
            "PFX repository path",
            "secret-material",
            {"provider_mutation": _add(("custody",), "pfx_path", "fixtures/signing.pfx")},
        ),
        (
            "mutable provider installer label",
            "provider-package",
            {"provider_mutation": _set(("official_evidence", "cka", "current_release_label"), "main")},
        ),
        (
            "installer integration removed",
            "provider-package",
            {"provider_mutation": _set(("official_evidence", "cka", "cka_package_integrated"), False)},
        ),
        (
            "immutable installer identity removed",
            "provider-package",
            {
                "provider_mutation": _set(
                    ("official_evidence", "cka", "immutable_package_identity_established"),
                    False,
                )
            },
        ),
        (
            "pinned installer digest changed",
            "provider-package",
            {
                "provider_mutation": _set(
                    ("official_evidence", "cka", "package_identity", "sha256"),
                    "0" * 64,
                )
            },
        ),
        (
            "pinned installer byte size changed",
            "provider-package",
            {
                "provider_mutation": _set(
                    ("official_evidence", "cka", "package_identity", "byte_size"),
                    1,
                )
            },
        ),
        (
            "pinned installer signer changed",
            "provider-package",
            {
                "provider_mutation": _set(
                    ("official_evidence", "cka", "package_identity", "signer_simple_name"),
                    "Other Publisher",
                )
            },
        ),
        (
            "pinned installer thumbprint changed",
            "provider-package",
            {
                "provider_mutation": _set(
                    ("official_evidence", "cka", "package_identity", "signer_thumbprint"),
                    "0" * 40,
                )
            },
        ),
        (
            "filename discrepancy hidden",
            "provider-package",
            {
                "provider_mutation": _set(
                    (
                        "official_evidence",
                        "cka",
                        "package_identity",
                        "publisher_filename_discrepancy_documented",
                    ),
                    False,
                )
            },
        ),
        (
            "sandbox account changed to production",
            "sandbox",
            {"provider_mutation": _set(("sandbox", "account_type"), "production")},
        ),
        (
            "sandbox automation accepts IV",
            "certificate-class",
            {
                "provider_mutation": _set(
                    ("sandbox", "automated_certificate_classes"),
                    ["IV", "OV", "EV"],
                )
            },
        ),
        (
            "sandbox workflow live validation claimed",
            "readiness",
            {
                "provider_mutation": _set(
                    ("sandbox", "workflow_live_validation_completed"),
                    True,
                )
            },
        ),
        (
            "vendor binary target",
            "target",
            {"provider_mutation": _set(("signing_contract", "first_party_targets"), ["yt-dlp.exe"])},
        ),
        (
            "arbitrary executable target",
            "target",
            {"provider_mutation": _set(("signing_contract", "first_party_targets"), ["other.exe"])},
        ),
        (
            "multiple signing targets",
            "target",
            {
                "provider_mutation": _set(
                    ("signing_contract", "first_party_targets"),
                    ["Youtube.Downloaderbs.exe", "other.exe"],
                )
            },
        ),
        (
            "missing file digest",
            "signing-contract",
            {"provider_mutation": _set(("signing_contract", "file_digest"), "")},
        ),
        (
            "SHA1 file digest",
            "signing-contract",
            {"provider_mutation": _set(("signing_contract", "file_digest"), "SHA1")},
        ),
        (
            "missing RFC3161 URL",
            "timestamp",
            {"provider_mutation": _set(("timestamp", "candidate_url"), "")},
        ),
        (
            "non-RFC3161 timestamp protocol",
            "timestamp",
            {"provider_mutation": _set(("timestamp", "protocol"), "legacy")},
        ),
        (
            "missing timestamp digest",
            "signing-contract",
            {"provider_mutation": _set(("signing_contract", "timestamp_digest"), "")},
        ),
        (
            "verification without /pa",
            "verification-contract",
            {"provider_mutation": _set(("signing_contract", "verification_switch"), "/a")},
        ),
        (
            "publisher check omitted",
            "publisher",
            {
                "provider_mutation": _set(
                    ("official_evidence", "certificate_discovery", "repository_selection"),
                    "first certificate",
                )
            },
        ),
        (
            "timestamp check omitted",
            "ordering",
            {
                "provider_mutation": _delete_sequence_item(
                    "verify timestamp presence and validity"
                )
            },
        ),
        (
            "signing before unsigned validation",
            "ordering",
            {
                "provider_mutation": _swap_sequence(
                    "validate unsigned first-party EXE structure",
                    "sign only the standalone first-party EXE",
                )
            },
        ),
        (
            "packaging before signature verification",
            "ordering",
            {
                "provider_mutation": _swap_sequence(
                    "verify expected publisher identity",
                    "assemble the portable package from the byte-identical verified signed EXE",
                )
            },
        ),
        (
            "checksum before signing",
            "ordering",
            {
                "provider_mutation": _swap_sequence(
                    "sign only the standalone first-party EXE",
                    "calculate checksums and downstream assurance artifacts",
                )
            },
        ),
        (
            "logging password variable",
            "secret-logging",
            {
                "script_path": SIGN_SCRIPT_PATH,
                "script_mutation": lambda raw: raw + b'Write-Host $Password\n',
            },
        ),
        (
            "unsigned fallback enabled",
            "unsigned-fallback",
            {
                "provider_mutation": _set(
                    ("signing_contract", "unsigned_fallback_representation"),
                    "allowed",
                )
            },
        ),
        (
            "provider account provisioned",
            "readiness",
            {"provider_mutation": _set(("state", "account_provisioned"), True)},
        ),
        (
            "certificate provisioned",
            "readiness",
            {"provider_mutation": _set(("state", "certificate_provisioned"), True)},
        ),
        (
            "credential source configured",
            "readiness",
            {"provider_mutation": _set(("state", "credential_source_configured"), True)},
        ),
        (
            "timestamp authority approved",
            "timestamp",
            {"provider_mutation": _set(("timestamp", "authority_approved"), True)},
        ),
        (
            "remote validation claimed before a successful signing result",
            "readiness",
            {"provider_mutation": _set(("state", "remote_signing_validated"), True)},
        ),
        (
            "synthetic signing authorized",
            "readiness",
            {"provider_mutation": _set(("state", "synthetic_signing_authorized"), True)},
        ),
        (
            "production signing authorized",
            "readiness",
            {"provider_mutation": _set(("state", "production_signing_authorized"), True)},
        ),
        (
            "production signing completed",
            "readiness",
            {"provider_mutation": _set(("state", "production_signing_completed"), True)},
        ),
        (
            "provider selection removed",
            "readiness",
            {"provider_mutation": _set(("state", "provider_selected"), False)},
        ),
        (
            "custody selection removed",
            "readiness",
            {"provider_mutation": _set(("state", "custody_model_selected"), False)},
        ),
        (
            "signing scaffold removed",
            "readiness",
            {"provider_mutation": _set(("state", "signing_scaffold_implemented"), False)},
        ),
        (
            "verification scaffold removed",
            "readiness",
            {"provider_mutation": _set(("state", "verification_scaffold_implemented"), False)},
        ),
        (
            "sandbox workflow implementation removed",
            "readiness",
            {"provider_mutation": _set(("state", "sandbox_workflow_implemented"), False)},
        ),
        (
            "release readiness enabled",
            "readiness",
            {"provider_mutation": _set(("state", "release_ready"), True)},
        ),
        (
            "publishing enabled",
            "readiness",
            {"provider_mutation": _set(("state", "publishing_allowed"), True)},
        ),
        (
            "IV treated as preferred automation class",
            "certificate-class",
            {"provider_mutation": _set(("certificate", "preferred_class"), "IV")},
        ),
        (
            "IV treated as confirmed automated class",
            "certificate-class",
            {
                "provider_mutation": _set(
                    ("certificate", "automated_certificate_classes"),
                    ["IV", "OV", "EV"],
                )
            },
        ),
        (
            "stale provider-not-selected blocker",
            "stale-blocker",
            {
                "provider_mutation": _insert_sorted(
                    ("blockers",),
                    "code-signing certificate provider not selected",
                )
            },
        ),
        (
            "stale custody-not-selected blocker",
            "stale-blocker",
            {
                "release_mutation": _insert_sorted(
                    ("authenticode", "blockers"),
                    "private-key custody model not approved",
                )
            },
        ),
        (
            "stale scaffold-not-implemented blocker",
            "stale-blocker",
            {
                "provider_mutation": _insert_sorted(
                    ("blockers",),
                    "signing and verification scaffold not implemented",
                )
            },
        ),
        (
            "project license status changed",
            "project-license",
            {
                "provider_mutation": _set(
                    ("repository_constraints", "project_license_status_change_authorized"),
                    True,
                )
            },
        ),
        (
            "release claim enabled",
            "nonclaims",
            {
                "release_mutation": _set(
                    ("claims", "authenticode_signed"),
                    True,
                )
            },
        ),
        (
            "script missing /fd SHA256",
            "signing-script",
            {
                "script_path": SIGN_SCRIPT_PATH,
                "script_mutation": _replace(
                    b'"sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/sha1"',
                    b'"sign", "/tr", $TimestampUrl, "/td", "SHA256", "/sha1"',
                ),
            },
        ),
        (
            "synthetic mode requires production authorization",
            "signing-purpose",
            {
                "script_path": SIGN_SCRIPT_PATH,
                "script_mutation": _replace(
                    b"if ($Config.state.account_provisioned -ne $true -or",
                    b"if ($Config.state.production_signing_authorized -ne $true -or\n"
                    b"    $Config.state.account_provisioned -ne $true -or",
                ),
            },
        ),
        (
            "synthetic mode requires prior remote validation",
            "signing-purpose",
            {
                "script_path": SIGN_SCRIPT_PATH,
                "script_mutation": _replace(
                    b"if ($Config.state.account_provisioned -ne $true -or",
                    b"if ($Config.state.remote_signing_validated -ne $true -or\n"
                    b"    $Config.state.account_provisioned -ne $true -or",
                ),
            },
        ),
        (
            "verification script missing /pa",
            "verification-script",
            {
                "script_path": VERIFY_SCRIPT_PATH,
                "script_mutation": _replace(
                    b'"verify", "/pa", "/all", "/v", "/tw"',
                    b'"verify", "/all", "/v", "/tw"',
                ),
            },
        ),
        (
            "verification timestamp marker omitted",
            "verification-script",
            {
                "script_path": VERIFY_SCRIPT_PATH,
                "script_mutation": _replace(
                    b"Timestamp Verified by",
                    b"Timestamp evidence",
                ),
            },
        ),
        (
            "verification thumbprint control omitted",
            "verification-script",
            {
                "script_path": VERIFY_SCRIPT_PATH,
                "script_mutation": _replace(
                    b"SignerCertificate.Thumbprint",
                    b"SignerCertificate.SerialNumber",
                ),
            },
        ),
        (
            "publisher changed to substring-only match",
            "publisher",
            {
                "script_path": VERIFY_SCRIPT_PATH,
                "script_mutation": _replace(
                    b"if (-not [string]::Equals($SignerPublisher, $ExpectedPublisher, [StringComparison]::Ordinal)) {",
                    b"if ($SignerPublisher.IndexOf($ExpectedPublisher, [StringComparison]::OrdinalIgnoreCase) -lt 0) {",
                ),
            },
        ),
        (
            "script attempts provider download",
            "download-install",
            {
                "script_path": SIGN_SCRIPT_PATH,
                "script_mutation": lambda raw: raw + b'Invoke-WebRequest \"https://example.invalid\"\n',
            },
        ),
        (
            "installer verifier attempts network access",
            "installer-verifier",
            {
                "script_path": INSTALLER_VERIFY_SCRIPT_PATH,
                "script_mutation": lambda raw: raw + b'Invoke-WebRequest "https://example.invalid"\n',
            },
        ),
        (
            "sandbox installer executes before verification",
            "ordering",
            {
                "script_path": SANDBOX_WORKFLOW_PATH,
                "script_mutation": lambda raw: raw.replace(
                    b"& .\\scripts\\verify_esigner_cka_installer.ps1",
                    b"# verifier delayed\n          & $env:CKA_INSTALLER_PATH /? | Out-Null\n          & .\\scripts\\verify_esigner_cka_installer.ps1",
                    1,
                ),
            },
        ),
    ]

    for name, category, options in cases:
        _run_negative(name, category, root, **options)

    fixture_count = _run_wrapper_fixtures(root)
    print(
        "Authenticode policy smoke passed: "
        f"5 policy positive, {len(cases) + eol_negative_count} policy/script negative, "
        f"{fixture_count} wrapper fixtures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
