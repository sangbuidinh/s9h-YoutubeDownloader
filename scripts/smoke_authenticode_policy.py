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
    SIGN_SCRIPT_PATH,
    VERIFY_SCRIPT_PATH,
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
        VERIFY_SCRIPT_PATH,
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repository_root / relative).read_bytes())


def _run_positive(name: str, root: Path) -> None:
    verify_authenticode_policy(root)
    print(f"PASS positive: {name}")


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


def _invoke_plan(
    powershell: str,
    repository_root: Path,
    release_root: Path,
    target: Path,
    *,
    plan_only: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(repository_root / SIGN_SCRIPT_PATH),
        "-Target",
        str(target),
        "-ReleaseRoot",
        str(release_root),
        "-ProviderConfigPath",
        str(repository_root / PROVIDER_POLICY_PATH),
        "-TimestampUrl",
        "http://ts.ssl.com",
    ]
    if plan_only:
        command.append("-PlanOnly")
    return subprocess.run(
        command,
        cwd=repository_root,
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

        first = _invoke_plan(powershell, repository_root, release_root, target)
        second = _invoke_plan(powershell, repository_root, release_root, target)
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

        real_attempt = _invoke_plan(
            powershell,
            repository_root,
            release_root,
            target,
            plan_only=False,
        )
        if real_attempt.returncode == 0 or "readiness gates are not satisfied" not in real_attempt.stderr:
            raise AssertionError("real signing did not fail closed on R1 readiness")
        print("PASS wrapper fixture: real signing blocked before SignTool")
        fixtures += 1

        for name in ("yt-dlp.exe", "arbitrary.exe"):
            rejected = release_root / name
            _write_unsigned_pe(rejected)
            result = _invoke_plan(powershell, repository_root, release_root, rejected)
            if result.returncode == 0:
                raise AssertionError(f"unauthorized target passed PlanOnly: {name}")
            print(f"PASS wrapper fixture: rejected {name}")
            fixtures += 1

        outside = temporary_root / "Youtube.Downloaderbs.exe"
        _write_unsigned_pe(outside)
        outside_result = _invoke_plan(powershell, repository_root, release_root, outside)
        if outside_result.returncode == 0:
            raise AssertionError("outside-root target passed PlanOnly")
        print("PASS wrapper fixture: rejected outside-root target")
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
        _run_positive("canonical round-trip fixture", copied_root)

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
            "installer integrated without immutable digest",
            "provider-package",
            {"provider_mutation": _set(("official_evidence", "cka", "cka_package_integrated"), True)},
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
            {"provider_mutation": _set(("provider", "account_provisioned"), True)},
        ),
        (
            "certificate provisioned",
            "readiness",
            {"provider_mutation": _set(("certificate", "provisioned"), True)},
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
            "remote signing validated",
            "readiness",
            {"provider_mutation": _set(("state", "remote_signing_validated"), True)},
        ),
        (
            "production signing completed",
            "readiness",
            {"provider_mutation": _set(("state", "production_signing_completed"), True)},
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
            "script attempts provider download",
            "download-install",
            {
                "script_path": SIGN_SCRIPT_PATH,
                "script_mutation": lambda raw: raw + b'Invoke-WebRequest \"https://example.invalid\"\n',
            },
        ),
    ]

    for name, category, options in cases:
        _run_negative(name, category, root, **options)

    fixture_count = _run_wrapper_fixtures(root)
    print(
        "Authenticode policy smoke passed: "
        f"2 policy positive, {len(cases)} policy/script negative, "
        f"{fixture_count} wrapper fixtures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
