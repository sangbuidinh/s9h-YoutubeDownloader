import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
IMMUTABLE_ACTION_REF = re.compile(r"[0-9a-f]{40}\Z")


class WorkflowContractError(AssertionError):
    pass


def verify_workflow_file(root: Path) -> None:
    resolved_root = Path(root).resolve(strict=False)
    _require(resolved_root.is_dir(), "repository root is not a directory")
    workflow_path = resolved_root / ".github" / "workflows" / "ci.yml"
    _require(not workflow_path.is_symlink(), "CI workflow file must not be a symlink")
    _require(workflow_path.is_file(), "CI workflow file is missing")
    try:
        workflow_path.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise WorkflowContractError("CI workflow file escapes the repository root") from exc
    raw = workflow_path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), "CI workflow file must not use UTF-8 BOM")
    _require(b"\x00" not in raw, "CI workflow file must not contain NUL")
    validate_workflow(raw.decode("utf-8"))


def main() -> int:
    verify_workflow_file(REPO_ROOT)
    workflow = _normalize_newlines(WORKFLOW_PATH.read_text(encoding="utf-8"))
    _test_negative_mutations(workflow)
    _test_immutable_action_policy(workflow)
    validate_workflow(workflow + "\n# Harmless trailing comment.\n")
    print("CI workflow smoke tests passed")
    return 0


def validate_workflow(workflow: str) -> None:
    workflow = _normalize_newlines(workflow)
    code = _without_comment_lines(workflow)
    lines = code.splitlines()

    _require(
        re.search(r"(?m)^name:\s*CI\s*$", code) is not None,
        "workflow name must be CI",
    )

    trigger_block = _mapping_block(lines, "on", 0)
    trigger_keys = _direct_mapping_keys(trigger_block, 2)
    _require(
        trigger_keys == ["pull_request", "push"],
        "workflow triggers must be pull_request and push only",
    )
    for trigger in ("pull_request", "push"):
        event_block = _mapping_block(trigger_block, trigger, 2)
        _require(
            _branch_values(event_block, 4) == ["main"],
            f"{trigger} must target main only",
        )
    _require("workflow_dispatch" not in code, "workflow_dispatch must be absent")
    _require(
        re.search(r"(?m)^\s*schedule\s*:", code) is None,
        "schedule must be absent",
    )
    _require("pull_request_target" not in code, "pull_request_target must be absent")

    permission_block = _mapping_block(lines, "permissions", 0)
    permissions = _direct_mapping_pairs(permission_block, 2)
    _require(
        permissions == [("contents", "read")],
        "top-level permissions must contain only contents: read",
    )
    for permission in ("contents", "actions", "packages", "id-token", "pull-requests"):
        _require(
            re.search(rf"(?m)^\s*{re.escape(permission)}\s*:\s*write\s*$", code)
            is None,
            f"{permission}: write must be absent",
        )

    concurrency = _mapping_block(lines, "concurrency", 0)
    _require(
        _scalar_value(concurrency, "group", 2)
        == "ci-${{ github.workflow }}-${{ github.ref }}",
        "concurrency group must separate workflow and ref",
    )
    _require(
        _scalar_value(concurrency, "cancel-in-progress", 2) == "true",
        "concurrency must cancel older in-progress runs",
    )

    jobs = _mapping_block(lines, "jobs", 0)
    _require(
        _direct_mapping_keys(jobs, 2)
        == ["windows-smoke", "release-bundle-handoff"],
        "CI must contain exactly the producer and handoff jobs",
    )
    job = _mapping_block(lines, "windows-smoke", 2)
    _require(
        _direct_mapping_pairs(_mapping_block(job, "permissions", 4), 6)
        == [("contents", "read")],
        "windows-smoke permissions must contain only contents: read",
    )
    _require(
        _scalar_value(job, "runs-on", 4) == "windows-2022",
        "runner must be windows-2022",
    )
    timeout = _scalar_value(job, "timeout-minutes", 4)
    _require(timeout is not None and timeout.isdigit(), "job timeout is missing")
    _require(int(timeout) <= 30, "job timeout must not exceed 30 minutes")

    steps = _step_blocks(job)
    checkout = _action_step(steps, "actions/checkout")
    checkout_ref = _action_ref(checkout, "actions/checkout")
    _require_safe_action_ref("actions/checkout", checkout_ref)
    _require(
        _scalar_value(checkout, "fetch-depth", 10) == "0",
        "checkout action must use fetch-depth: 0 for full history",
    )
    _require(
        _scalar_value(checkout, "ref", 10) is None,
        "checkout must not use an arbitrary ref",
    )

    setup_python = _action_step(steps, "actions/setup-python")
    setup_ref = _action_ref(setup_python, "actions/setup-python")
    _require_safe_action_ref("actions/setup-python", setup_ref)
    python_version = _scalar_value(setup_python, "python-version", 10)
    _require(
        _unquote(python_version) == "3.11.9",
        "setup-python must use Python 3.11.9",
    )
    _verify_exact_python_version(code)

    _verify_canonical_temp(code)
    _require(
        "python -m compileall app.py core ui scripts" in code,
        "compile command is missing",
    )
    _require(
        "python scripts/package_windows.py --preflight-only" in code,
        "package preflight command is missing",
    )
    _require(
        'git ls-files "scripts/smoke_*.py"' in code,
        "full tracked smoke discovery is missing",
    )
    _require("Sort-Object" in code, "smoke discovery must be sorted")
    _require("python $Test" in code, "every smoke test must run with Python")
    _require(
        re.search(
            r'if\s*\(\$LASTEXITCODE\s*-ne\s*0\)\s*\{\s*'
            r'throw\s+"Smoke test failed:\s*\$Test"',
            code,
            re.DOTALL,
        )
        is not None,
        "nonzero smoke exit must fail with the test path",
    )
    _require(
        re.search(r"\$SmokeTests\.Count\s*-eq\s*0", code) is not None,
        "empty smoke discovery must fail",
    )

    smoke_step = _named_step(steps, "Run tracked smoke suite")
    installer_step = _named_step(steps, "Validate locked build dependencies")
    _require(
        steps.index(installer_step) < steps.index(smoke_step),
        "locked build dependencies must be installed before the tracked smoke suite",
    )
    installer_text = "\n".join(installer_step)
    for required in (
        "python scripts/install_build_dependencies.py",
        '$env:RUNNER_TEMP\\s9h-build-lock-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT',
        '--github-env "$env:GITHUB_ENV"',
        '--github-path "$env:GITHUB_PATH"',
    ):
        _require(required in installer_text, f"CI locked installer is missing: {required}")
    _require(
        installer_text.count("python scripts/install_build_dependencies.py") == 1,
        "CI must invoke the locked installer exactly once",
    )

    create_step = _named_step(steps, "Create and verify synthetic release bundle")
    upload_step = _action_step(steps, "actions/upload-artifact")
    report_step = _named_step(steps, "Report synthetic release bundle handoff")
    _require(
        steps.index(installer_step)
        < steps.index(create_step)
        < steps.index(upload_step)
        < steps.index(report_step),
        "synthetic bundle handoff must follow locked dependency validation",
    )
    outputs = _direct_mapping_pairs(_mapping_block(job, "outputs", 4), 6)
    _require(
        outputs
        == [
            ("artifact-id", "${{ steps.upload-release-bundle.outputs.artifact-id }}"),
            ("artifact-digest", "${{ steps.upload-release-bundle.outputs.artifact-digest }}"),
        ],
        "windows-smoke artifact outputs are invalid",
    )
    create_text = "\n".join(create_step)
    for required in (
        '$env:RUNNER_TEMP',
        'b"MZ synthetic CI fixture; this file is not executable.\\n"',
        '"data/bin/aria2c.exe"',
        '"data/bin/deno.exe"',
        '"data/bin/ffmpeg.exe"',
        '"data/bin/ffprobe.exe"',
        '"data/bin/yt-dlp.exe"',
        '"native/_tkinter.pyd"',
        '"python311.dll"',
        "import hashlib",
        "portable_hash = hashlib.sha256(archive.read_bytes()).hexdigest()",
        '"# Synthetic CI release\\n\\n"',
        'f"- `{archive.name}`: `{portable_hash}`\\n"',
        "python scripts/prepare_release_legal_payload.py create",
        "python scripts/prepare_release_legal_payload.py verify",
        "--release-notes $SyntheticReleaseNotes",
        "$PreInjectionHash",
        "$PostInjectionHash",
        "$SyntheticNotesText.Contains($PostInjectionHash)",
        "$SyntheticNotesText.Contains($PreInjectionHash)",
        "python scripts/create_synthetic_release_sbom_input.py",
        "$SbomInput",
        '"SYNTHETIC_SOURCE_FIXTURE.txt"',
        '"SOURCE_MANIFEST.json"',
        'b"synthetic fixture\\n"',
        'b"not a real source kit\\n"',
        'b"not for distribution\\n"',
        "python scripts/prepare_release_bundle.py create",
        "python scripts/prepare_release_bundle.py verify",
        "--tag v0.0.0-ci",
        "--source-commit $Commit",
        "--control-commit $Commit",
        "--prerelease true",
        "--policy legal/release-policy.json",
        "--asset-contract legal/release-assets-v3.json",
        "--legal-payload",
        "--source-assets-root",
        "--sbom-input $SbomInput",
        "--require-release-ready false",
        "--require-release-ready true 2>&1",
        "$PSNativeCommandUseErrorActionPreference = $false",
        "$PSNativeCommandUseErrorActionPreference = $PreviousNativePreference",
        "$PublishReadyExitCode = $LASTEXITCODE",
        "$PublishReadyExitCode -ne 1",
        "release bundle is not approved for publishing",
        'Write-Host "Synthetic publish-ready rejection verified"',
        "$global:LASTEXITCODE = 0",
    ):
        _require(required in create_text, f"synthetic bundle producer is missing: {required}")
    publish_invocation = "--require-release-ready true 2>&1"
    capture_line = "$PublishReadyExitCode = $LASTEXITCODE"
    restore_line = "$PSNativeCommandUseErrorActionPreference = $PreviousNativePreference"
    guard_line = (
        'if ($PublishReadyExitCode -ne 1 -or ($PublishReadyOutput | Out-String) '
        '-notmatch "release bundle is not approved for publishing") {'
    )
    success_line = 'Write-Host "Synthetic publish-ready rejection verified"'
    normalization_line = "$global:LASTEXITCODE = 0"
    for exact, label in (
        (capture_line, "publish-ready exit capture"),
        (guard_line, "publish-ready rejection guard"),
        (success_line, "publish-ready rejection success message"),
        (normalization_line, "controlled native exit normalization"),
    ):
        _require(create_text.count(exact) == 1, f"{label} must appear exactly once")
    publish_index = create_text.rindex(publish_invocation)
    capture_index = create_text.index(capture_line)
    finally_index = create_text.index("finally {")
    restore_index = create_text.index(restore_line)
    guard_index = create_text.index(guard_line)
    success_index = create_text.index(success_line)
    normalization_index = create_text.index(normalization_line)
    _require(
        publish_index
        < capture_index
        < finally_index
        < restore_index
        < guard_index
        < success_index
        < normalization_index,
        "publish-ready exit normalization ordering is invalid",
    )
    final_executable = next(
        (line.strip() for line in reversed(create_step) if line.strip()),
        "",
    )
    _require(
        final_executable == normalization_line,
        "controlled normalization must be the final executable statement",
    )
    _require(
        re.search(r"(?m)^\s*exit\s+0\s*$", create_text) is None,
        "exit 0 must not replace controlled normalization",
    )
    _require(
        create_text.count("--release-notes $SyntheticReleaseNotes") == 2,
        "synthetic legal payload commands must use release notes exactly twice",
    )
    _require(
        create_text.count("python scripts/prepare_release_bundle.py verify") == 2,
        "synthetic bundle must run structural and publish-ready verification",
    )
    upload_ref = _action_ref(upload_step, "actions/upload-artifact")
    _require_safe_action_ref("actions/upload-artifact", upload_ref)
    for required in (
        "id: upload-release-bundle",
        "ci-release-bundle-${{ github.run_id }}-${{ github.run_attempt }}",
        "if-no-files-found: error",
        "compression-level: 0",
        "overwrite: false",
        "include-hidden-files: false",
        "retention-days: 1",
    ):
        _require(required in "\n".join(upload_step), f"synthetic upload is missing: {required}")
    report_text = "\n".join(report_step)
    _require("^[0-9a-f]{64}$" in report_text, "producer artifact digest validation is missing")

    handoff = _mapping_block(jobs, "release-bundle-handoff", 2)
    _require(
        _scalar_value(handoff, "needs", 4) == "windows-smoke",
        "handoff job must need windows-smoke",
    )
    _require(
        _direct_mapping_pairs(_mapping_block(handoff, "permissions", 4), 6)
        == [("contents", "read")],
        "handoff permissions must contain only contents: read",
    )
    _require(
        _scalar_value(handoff, "runs-on", 4) == "windows-2022",
        "handoff runner must be windows-2022",
    )
    handoff_steps = _step_blocks(handoff)
    _require(len(handoff_steps) == 3, "handoff job must contain exactly three steps")
    output_check = _named_step(handoff_steps, "Validate immutable synthetic bundle outputs")
    download = _action_step(handoff_steps, "actions/download-artifact")
    verifier = _named_step(handoff_steps, "Verify synthetic release bundle handoff")
    _require(
        handoff_steps == [output_check, download, verifier],
        "handoff job step order is invalid",
    )
    output_text = "\n".join(output_check)
    for required in (
        "${{ needs.windows-smoke.outputs.artifact-id }}",
        "${{ needs.windows-smoke.outputs.artifact-digest }}",
        "^[0-9a-f]{64}$",
    ):
        _require(required in output_text, f"handoff output validation is missing: {required}")
    download_ref = _action_ref(download, "actions/download-artifact")
    _require_safe_action_ref("actions/download-artifact", download_ref)
    download_text = "\n".join(download)
    download_with = _mapping_block(download, "with", 8)
    _require(
        _scalar_value(download_with, "merge-multiple", 10) == "true",
        "handoff download merge-multiple must be the YAML boolean true",
    )
    _require(
        _direct_mapping_pairs(download_with, 10)
        == [
            ("artifact-ids", "${{ needs.windows-smoke.outputs.artifact-id }}"),
            ("path", "release-bundle"),
            ("merge-multiple", "true"),
        ],
        "handoff download must use only the producer artifact ID",
    )
    verifier_text = "\n".join(verifier)
    for required in (
        "Synthetic release bundle v3 handoff verified",
        "v0.0.0-ci",
        "${{ github.sha }}",
        "Get-FileHash",
        "ConvertFrom-Json",
        "s9h-release-bundle-v3",
        "release_ready",
        "legal_compliance_certified",
        "source_availability_certified",
        "legal-payload",
        "aria2-source",
        "ffmpeg-source",
        "release-sbom",
        "assets/$env:EXPECTED_LEGAL",
        "assets/$env:EXPECTED_ARIA2_SOURCE",
        "assets/$env:EXPECTED_FFMPEG_SOURCE",
        "assets/$env:EXPECTED_SBOM",
        "SHA256SUMS.txt",
        "ReparsePoint",
    ):
        _require(required in verifier_text, f"handoff verifier is missing: {required}")
    for forbidden in (
        "actions/checkout@",
        "actions/setup-python@",
        "python ",
        "Invoke-Expression",
        "Start-Process",
        "&",
        ".ps1",
        ".cmd",
        ".bat",
    ):
        _require(forbidden not in verifier_text, f"handoff verifier may execute downloaded code: {forbidden}")
    handoff_text = "\n".join(handoff)
    _require("actions/checkout@" not in handoff_text, "handoff job must not checkout source")
    _require("actions/setup-python@" not in handoff_text, "handoff job must not setup Python")

    _require("continue-on-error" not in code, "continue-on-error must be absent")
    _forbid(
        code,
        r"(?im)^\s*(?:run:\s*)?(?:python\s+-m\s+)?pip\s+(?:install|upgrade)\b",
        "pip install or upgrade must be absent",
    )
    _forbid(code, r"(?i)\bPyInstaller\b", "PyInstaller invocation must be absent")
    _forbid(
        code,
        r"(?i)(?:action-gh-release|create-release|release-action)",
        "release publishing action must be absent",
    )
    _forbid(code, r"(?im)^\s*gh\s+release\b", "gh release command must be absent")
    _forbid(
        code,
        r"(?i)(?:secrets\s*\.|GH_TOKEN|github\.token)",
        "secret references must be absent",
    )
    _forbid(
        code,
        r"\$\{\{\s*github\.event\.",
        "untrusted event interpolation must be absent",
    )
    _verify_no_sensitive_literals(code)


def _verify_canonical_temp(code: str) -> None:
    for required in (
        "$env:RUNNER_TEMP",
        "$env:GITHUB_RUN_ID",
        "$env:GITHUB_RUN_ATTEMPT",
        "Path(sys.argv[1]).resolve(strict=False)",
        "TemporaryDirectory()",
        "raw_path.resolve(strict=False)",
    ):
        _require(required in code, f"canonical TEMP behavior is missing: {required}")
    _require(
        "IsNullOrWhiteSpace([string]$resolvedTemp)" in code,
        "empty canonical TEMP resolution must be rejected",
    )
    for variable in ("TEMP", "TMP", "TMPDIR"):
        pattern = (
            rf'"{variable}=\$resolvedTemp"\s*\|\s*'
            r"Out-File\s+-FilePath\s+\$env:GITHUB_ENV"
        )
        _require(
            re.search(pattern, code, re.DOTALL) is not None,
            f"{variable} must be exported through GITHUB_ENV",
        )
    _require(
        "if raw_path != canonical_path:" in code,
        "temporary-directory probe must reject non-canonical paths",
    )


def _verify_exact_python_version(code: str) -> None:
    for required in (
        "python --version",
        '$VersionOutput = (& python --version 2>&1 | Out-String).Trim()',
        'if ($VersionOutput -ne "Python 3.11.9")',
        'Write-Host "Pinned Python verified: $VersionOutput"',
    ):
        _require(required in code, f"exact Python version verification is missing: {required}")


def _verify_no_sensitive_literals(code: str) -> None:
    patterns = (
        (r"AIza[0-9A-Za-z_-]{30,}", "YouTube API key"),
        (r"(?:ghp_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})", "GitHub token"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private-key header"),
        (r"(?im)^\s*(?:SID|SAPISID|HSID)\s*=", "cookie assignment"),
        (r"https?://[^\s]+googlevideo\.com[^\s]*", "signed media URL"),
        (r"(?i)[A-Z]:\\Users\\[^\\\r\n]+", "local developer path"),
    )
    for pattern, label in patterns:
        _forbid(code, pattern, f"workflow contains {label}")


def _mapping_block(lines: list[str], key: str, indent: int) -> list[str]:
    prefix = " " * indent
    marker = re.compile(rf"^{re.escape(prefix + key)}\s*:\s*$")
    start = next((index for index, line in enumerate(lines) if marker.match(line)), None)
    _require(start is not None, f"mapping is missing: {key}")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= indent:
            break
        block.append(line)
    return block


def _direct_mapping_keys(lines: list[str], indent: int) -> list[str]:
    pattern = re.compile(rf"^{' ' * indent}([A-Za-z0-9_-]+)\s*:")
    return [match.group(1) for line in lines if (match := pattern.match(line))]


def _direct_mapping_pairs(lines: list[str], indent: int) -> list[tuple[str, str]]:
    pattern = re.compile(rf"^{' ' * indent}([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$")
    return [
        (match.group(1), _unquote(match.group(2)))
        for line in lines
        if (match := pattern.match(line))
    ]


def _scalar_value(lines: list[str], key: str, indent: int) -> str | None:
    pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:\s*(.*?)\s*$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def _branch_values(event_block: list[str], indent: int) -> list[str]:
    branches = _mapping_block(event_block, "branches", indent)
    inline = _scalar_value(event_block, "branches", indent)
    if inline:
        if inline.startswith("[") and inline.endswith("]"):
            return [
                _unquote(value.strip())
                for value in inline[1:-1].split(",")
                if value.strip()
            ]
        return [_unquote(inline)]
    pattern = re.compile(rf"^{' ' * (indent + 2)}-\s*(.*?)\s*$")
    return [
        _unquote(match.group(1))
        for line in branches
        if (match := pattern.match(line))
    ]


def _step_blocks(job: list[str]) -> list[list[str]]:
    starts = [
        index
        for index, line in enumerate(job)
        if _indent(line) == 6 and line.lstrip().startswith("- ")
    ]
    blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(job)
        blocks.append(job[start:end])
    _require(bool(blocks), "workflow job has no steps")
    return blocks


def _action_step(steps: list[list[str]], action: str) -> list[str]:
    pattern = re.compile(rf"\buses:\s*{re.escape(action)}@")
    matches = [step for step in steps if pattern.search("\n".join(step))]
    _require(len(matches) == 1, f"{action} step must appear exactly once")
    return matches[0]


def _named_step(steps: list[list[str]], name: str) -> list[str]:
    pattern = re.compile(rf"^\s*-\s+name:\s*{re.escape(name)}\s*$")
    matches = [step for step in steps if any(pattern.match(line) for line in step)]
    _require(len(matches) == 1, f"workflow step must appear exactly once: {name}")
    return matches[0]


def _action_ref(step: list[str], action: str) -> str:
    match = re.search(rf"\buses:\s*{re.escape(action)}@([^\s#]+)", "\n".join(step))
    _require(match is not None and bool(match.group(1)), f"{action} ref is missing")
    return match.group(1)


def _require_safe_action_ref(action: str, ref: str) -> None:
    _require(
        IMMUTABLE_ACTION_REF.fullmatch(ref) is not None,
        f"{action} ref must be a lowercase 40-character commit SHA",
    )


def _test_negative_mutations(workflow: str) -> None:
    mutations = (
        (
            "contents write",
            _replace_once(
                workflow,
                "permissions:\n  contents: read",
                "permissions:\n  contents: write",
            ),
            "permissions",
        ),
        (
            "missing fetch depth",
            _replace_once(workflow, "          fetch-depth: 0\n", ""),
            "fetch-depth",
        ),
        (
            "workflow dispatch",
            _replace_once(
                workflow,
                "  push:\n",
                "  workflow_dispatch:\n  push:\n",
            ),
            "triggers",
        ),
        (
            "continue on error",
            _replace_once(
                workflow,
                "    runs-on: windows-2022\n    timeout-minutes: 30\n",
                "    runs-on: windows-2022\n    timeout-minutes: 30\n    continue-on-error: true\n",
            ),
            "continue-on-error",
        ),
        (
            "missing native exit normalization",
            _replace_once(workflow, "          $global:LASTEXITCODE = 0\n", ""),
            "",
        ),
        (
            "normalization before publish-ready guard",
            _replace_once(
                workflow,
                "          if ($PublishReadyExitCode -ne 1 -or ($PublishReadyOutput | Out-String) -notmatch \"release bundle is not approved for publishing\") {\n"
                "              throw \"Synthetic publish-ready verification did not remain fail-closed\"\n"
                "          }\n"
                "          Write-Host \"Synthetic publish-ready rejection verified\"\n"
                "          $global:LASTEXITCODE = 0",
                "          $global:LASTEXITCODE = 0\n"
                "          if ($PublishReadyExitCode -ne 1 -or ($PublishReadyOutput | Out-String) -notmatch \"release bundle is not approved for publishing\") {\n"
                "              throw \"Synthetic publish-ready verification did not remain fail-closed\"\n"
                "          }\n"
                "          Write-Host \"Synthetic publish-ready rejection verified\"",
            ),
            "",
        ),
        (
            "normalization changed to one",
            _replace_once(
                workflow,
                "          $global:LASTEXITCODE = 0",
                "          $global:LASTEXITCODE = 1",
            ),
            "",
        ),
        (
            "duplicate native exit normalization",
            _replace_once(
                workflow,
                "          $global:LASTEXITCODE = 0",
                "          $global:LASTEXITCODE = 0\n          $global:LASTEXITCODE = 0",
            ),
            "",
        ),
        (
            "missing publish-ready success message",
            _replace_once(
                workflow,
                "          Write-Host \"Synthetic publish-ready rejection verified\"\n",
                "",
            ),
            "",
        ),
        (
            "publish-ready success message before guard",
            _replace_once(
                workflow,
                "          if ($PublishReadyExitCode -ne 1 -or ($PublishReadyOutput | Out-String) -notmatch \"release bundle is not approved for publishing\") {\n"
                "              throw \"Synthetic publish-ready verification did not remain fail-closed\"\n"
                "          }\n"
                "          Write-Host \"Synthetic publish-ready rejection verified\"",
                "          Write-Host \"Synthetic publish-ready rejection verified\"\n"
                "          if ($PublishReadyExitCode -ne 1 -or ($PublishReadyOutput | Out-String) -notmatch \"release bundle is not approved for publishing\") {\n"
                "              throw \"Synthetic publish-ready verification did not remain fail-closed\"\n"
                "          }",
            ),
            "",
        ),
        (
            "publish-ready expected exit changed to zero",
            _replace_once(
                workflow,
                "$PublishReadyExitCode -ne 1",
                "$PublishReadyExitCode -ne 0",
            ),
            "",
        ),
        (
            "publish-ready expected message removed",
            _replace_once(
                workflow,
                "release bundle is not approved for publishing",
                "publish-ready rejection text removed",
            ),
            "",
        ),
        (
            "publish-ready guard made unconditional success",
            _replace_once(
                workflow,
                "          if ($PublishReadyExitCode -ne 1 -or ($PublishReadyOutput | Out-String) -notmatch \"release bundle is not approved for publishing\") {",
                "          if ($false) {",
            ),
            "",
        ),
        (
            "exit zero replaces controlled normalization",
            _replace_once(
                workflow,
                "          $global:LASTEXITCODE = 0",
                "          exit 0",
            ),
            "",
        ),
        (
            "native command after controlled normalization",
            _replace_once(
                workflow,
                "          $global:LASTEXITCODE = 0",
                "          $global:LASTEXITCODE = 0\n          python --version",
            ),
            "",
        ),
        (
            "unsafe artifact retention",
            _replace_once(workflow, "          retention-days: 1", "          retention-days: 2"),
            "retention-days",
        ),
        (
            "pip install",
            _replace_once(
                workflow,
                "      - name: Create and verify synthetic release bundle",
                "      - name: Unsafe dependency install\n"
                "        run: python -m pip install pyinstaller\n"
                "      - name: Create and verify synthetic release bundle",
            ),
            "pip install",
        ),
        (
            "missing legal payload generation",
            _replace_once(
                workflow,
                "python scripts/prepare_release_legal_payload.py create",
                "python scripts/prepare_release_legal_payload.py verify",
            ),
            "synthetic bundle producer",
        ),
        (
            "missing legal payload verification",
            _replace_once(
                workflow,
                "python scripts/prepare_release_legal_payload.py verify",
                "python scripts/prepare_release_legal_payload.py inspect",
            ),
            "synthetic bundle producer",
        ),
        (
            "missing release-notes legal input",
            _replace_once(
                workflow,
                "            --output-zip $LegalPayload `\n"
                "            --release-notes $SyntheticReleaseNotes `\n",
                "            --output-zip $LegalPayload `\n",
            ),
            "release notes exactly twice",
        ),
        (
            "publish-ready verifier disabled",
            _replace_once(
                workflow,
                "              --require-release-ready true 2>&1",
                "              --require-release-ready false 2>&1",
            ),
            "synthetic bundle producer",
        ),
        (
            "missing synthetic source fixture",
            _replace_once(workflow, '"SYNTHETIC_SOURCE_FIXTURE.txt"', '"SOURCE.txt"'),
            "synthetic bundle producer",
        ),
        (
            "missing structural readiness flag",
            _replace_once(workflow, "            --require-release-ready false\n", ""),
            "require-release-ready",
        ),
        (
            "bundle v1 consumer",
            _replace_once(workflow, "s9h-release-bundle-v3", "s9h-release-bundle-v1"),
            "handoff verifier",
        ),
        (
            "consumer omits aria2 source",
            _replace_once(workflow, '              "assets/$env:EXPECTED_ARIA2_SOURCE",\n', ""),
            "handoff verifier",
        ),
        (
            "shallow checkout",
            _replace_once(workflow, "fetch-depth: 0", "fetch-depth: 1"),
            "fetch-depth",
        ),
        (
            "missing smoke discovery",
            _replace_once(
                workflow,
                'git ls-files "scripts/smoke_*.py"',
                'Get-ChildItem "scripts/smoke_*.py"',
            ),
            "smoke discovery",
        ),
        (
            "missing github env export",
            _replace_once(
                workflow,
                '            --github-env "$env:GITHUB_ENV" `\n',
                "",
            ),
            "github-env",
        ),
        (
            "missing github path export",
            _replace_once(
                workflow,
                '            --github-path "$env:GITHUB_PATH"\n',
                "",
            ),
            "github-path",
        ),
        (
            "repository-contained build venv",
            _replace_once(
                workflow,
                '            --venv "$env:RUNNER_TEMP\\s9h-build-lock-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT" `\n',
                '            --venv ".build-venv" `\n',
            ),
            "RUNNER_TEMP",
        ),
        (
            "smoke suite before installer",
            _move_named_step_before(
                workflow,
                "Run tracked smoke suite",
                "Validate locked build dependencies",
            ),
            "installed before the tracked smoke suite",
        ),
        (
            "missing installer step",
            _remove_named_step(workflow, "Validate locked build dependencies"),
            "Validate locked build dependencies",
        ),
        (
            "missing handoff dependency",
            _replace_once(workflow, "    needs: windows-smoke\n", ""),
            "need windows-smoke",
        ),
        (
            "handoff write permission",
            _replace_once(
                workflow,
                "  release-bundle-handoff:\n    name: Release bundle artifact handoff\n    needs: windows-smoke\n    permissions:\n      contents: read",
                "  release-bundle-handoff:\n    name: Release bundle artifact handoff\n    needs: windows-smoke\n    permissions:\n      contents: write",
            ),
            "contents: write",
        ),
        (
            "missing merge-multiple",
            _replace_once(workflow, "          merge-multiple: true\n", ""),
            "merge-multiple",
        ),
        (
            "false merge-multiple",
            _replace_once(
                workflow,
                "          merge-multiple: true",
                "          merge-multiple: false",
            ),
            "merge-multiple",
        ),
        (
            "quoted true merge-multiple",
            _replace_once(
                workflow,
                "          merge-multiple: true",
                '          merge-multiple: "true"',
            ),
            "merge-multiple",
        ),
        (
            "quoted false merge-multiple",
            _replace_once(
                workflow,
                "          merge-multiple: true",
                '          merge-multiple: "false"',
            ),
            "merge-multiple",
        ),
        (
            "multiple artifact IDs",
            _replace_once(
                workflow,
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}",
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}, 123",
            ),
            "artifact ID",
        ),
        (
            "nested artifact destination",
            _replace_once(
                workflow,
                "          path: release-bundle",
                "          path: release-bundle/nested",
            ),
            "artifact ID",
        ),
        (
            "artifact name input",
            _replace_once(
                workflow,
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}",
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}\n"
                "          name: synthetic-release-bundle",
            ),
            "artifact ID",
        ),
        (
            "artifact pattern input",
            _replace_once(
                workflow,
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}",
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}\n"
                "          pattern: synthetic-*",
            ),
            "artifact ID",
        ),
        (
            "missing handoff digest validation",
            _replace_once(
                workflow,
                "          ARTIFACT_DIGEST: ${{ needs.windows-smoke.outputs.artifact-digest }}",
                "          ARTIFACT_DIGEST: invalid",
            ),
            "artifact-digest",
        ),
        (
            "Python in handoff verifier",
            _replace_once(
                workflow,
                "      - name: Verify synthetic release bundle handoff\n        shell: pwsh",
                "      - name: Verify synthetic release bundle handoff\n        run: python downloaded.py\n        shell: pwsh",
            ),
            "execute downloaded code",
        ),
    )
    for label, mutated, expected in mutations:
        _expect_contract_failure(label, mutated, expected)


def _test_immutable_action_policy(workflow: str) -> None:
    checkout_ref = _workflow_action_ref(workflow, "actions/checkout")
    setup_ref = _workflow_action_ref(workflow, "actions/setup-python")
    upload_ref = _workflow_action_ref(workflow, "actions/upload-artifact")
    download_ref = _workflow_action_ref(workflow, "actions/download-artifact")
    generic_pins = _replace_action_ref(
        _replace_action_ref(
            _replace_action_ref(
                _replace_action_ref(workflow, "actions/checkout", "a" * 40),
                "actions/setup-python",
                "b" * 40,
            ),
            "actions/upload-artifact",
            "c" * 40,
        ),
        "actions/download-artifact",
        "d" * 40,
    )
    validate_workflow(generic_pins)

    mutations = (
        (
            "checkout major tag",
            _replace_action_ref(workflow, "actions/checkout", "v4"),
        ),
        (
            "setup-python major tag",
            _replace_action_ref(workflow, "actions/setup-python", "v5"),
        ),
        (
            "upload-artifact major tag",
            _replace_action_ref(workflow, "actions/upload-artifact", "v4"),
        ),
        (
            "download-artifact major tag",
            _replace_action_ref(workflow, "actions/download-artifact", "v4"),
        ),
        (
            "seven-character SHA",
            _replace_action_ref(workflow, "actions/checkout", checkout_ref[:7]),
        ),
        (
            "39-character SHA",
            _replace_action_ref(workflow, "actions/checkout", "a" * 39),
        ),
        (
            "41-character SHA",
            _replace_action_ref(workflow, "actions/checkout", "a" * 41),
        ),
        (
            "nonhex 40-character ref",
            _replace_action_ref(workflow, "actions/checkout", "g" * 40),
        ),
        (
            "mutable runner",
            _replace_once(
                workflow,
                "    runs-on: windows-2022\n    timeout-minutes: 30",
                "    runs-on: windows-latest\n    timeout-minutes: 30",
            ),
        ),
        (
            "broad Python selector",
            _replace_once(
                workflow,
                'python-version: "3.11.9"',
                'python-version: "3.11"',
            ),
        ),
        (
            "different Python patch",
            _replace_once(
                workflow,
                'python-version: "3.11.9"',
                'python-version: "3.11.10"',
            ),
        ),
        (
            "missing exact version verification",
            _replace_once(
                workflow,
                'if ($VersionOutput -ne "Python 3.11.9")',
                'if ($VersionOutput -ne "Python 3.11")',
            ),
        ),
        (
            "CI job contents write",
            _replace_once(
                workflow,
                "  windows-smoke:\n    name: Windows compile, preflight and smoke\n    permissions:\n      contents: read",
                "  windows-smoke:\n    name: Windows compile, preflight and smoke\n    permissions:\n      contents: write",
            ),
        ),
    )
    for label, mutated in mutations:
        _expect_contract_failure(label, mutated, "")

    _require(
        len({checkout_ref, setup_ref, upload_ref, download_ref}) == 4,
        "action refs unexpectedly share one commit",
    )


def _workflow_action_ref(workflow: str, action: str) -> str:
    match = re.search(rf"\buses:\s*{re.escape(action)}@([^\s#]+)", workflow)
    _require(match is not None, f"{action} invocation is missing")
    return match.group(1)


def _replace_action_ref(workflow: str, action: str, replacement: str) -> str:
    pattern = rf"(\buses:\s*{re.escape(action)}@)[^\s#]+"
    mutated, count = re.subn(pattern, rf"\g<1>{replacement}", workflow)
    _require(count == 1, f"{action} mutation target count is {count}")
    return mutated


def _remove_named_step(workflow: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    mutated, count = pattern.subn("", _normalize_newlines(workflow), count=1)
    _require(count == 1, f"step mutation target count is {count}: {name}")
    return mutated


def _move_named_step_before(workflow: str, name: str, target: str) -> str:
    normalized = _normalize_newlines(workflow)
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    match = pattern.search(normalized)
    _require(match is not None, f"step mutation target is missing: {name}")
    block = match.group(0).rstrip("\n") + "\n"
    without = normalized[: match.start()] + normalized[match.end() :]
    target_pattern = re.compile(rf"(?m)^      - name:\s*{re.escape(target)}\s*$")
    target_match = target_pattern.search(without)
    _require(target_match is not None, f"step move target is missing: {target}")
    return without[: target_match.start()] + block + without[target_match.start() :]


def _expect_contract_failure(label: str, workflow: str, expected: str) -> None:
    try:
        validate_workflow(workflow)
    except WorkflowContractError as exc:
        if expected:
            _require(
                expected.casefold() in str(exc).casefold(),
                f"{label} raised unexpected contract error: {exc}",
            )
    else:
        raise WorkflowContractError(f"negative mutation was accepted: {label}")


def _replace_once(value: str, old: str, new: str) -> str:
    _require(value.count(old) == 1, f"mutation source is not unique: {old!r}")
    return value.replace(old, new, 1)


def _without_comment_lines(value: str) -> str:
    return "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in value.splitlines()
    )


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _unquote(value: str | None) -> str:
    if value is None:
        return ""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _indent(value: str) -> int:
    return len(value) - len(value.lstrip(" "))


def _forbid(value: str, pattern: str, message: str) -> None:
    _require(re.search(pattern, value) is None, message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowContractError(message)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
