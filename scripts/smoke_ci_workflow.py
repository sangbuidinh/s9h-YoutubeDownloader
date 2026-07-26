import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
IMMUTABLE_ACTION_REF = re.compile(r"[0-9a-f]{40}\Z")
CURRENT_PROFILE = "current_ci"
RAW_EXTRACTION_STEP = "Extract and validate raw synthetic artifact archive"
RAW_EXTRACTION_SUCCESS = "Secure raw synthetic artifact extraction verified"
MAX_EXTRACTION_ENTRY_COUNT = 128
MAX_EXTRACTION_BYTES = 16777216
UPSTREAM_DOWNLOAD_ARTIFACT_WARNING_CONTRACT = {
    "release": "v8.0.1",
    "commit": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "issue": "actions/download-artifact#484",
    "warning_path": "upstream extraction path",
    "supported_input": "skip-decompress",
    "digest_check": "remains inside actions/download-artifact",
    "repository_extraction": "independently fail-closed",
    "node_options": "--throw-deprecation",
}
EXPECTED_CURRENT_ACTIONS = {
    "actions/checkout": {
        "release_tag": "v6.1.0",
        "commit": "d23441a48e516b6c34aea4fa41551a30e30af803",
        "action_yml_blob": "5b0524f730db83f9513c18ab31a6c086c7239076",
    },
    "actions/setup-python": {
        "release_tag": "v6.3.0",
        "commit": "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "action_yml_blob": "7a9a7b634ec348b35b882f1f14fcaa4d41836a8e",
    },
    "actions/upload-artifact": {
        "release_tag": "v7.0.1",
        "commit": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "action_yml_blob": "7cb4d1e81db55320b41217e1a78a1a46e3d2baef",
    },
    "actions/download-artifact": {
        "release_tag": "v8.0.1",
        "commit": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "action_yml_blob": "8b8c65029ccad20750a29fecb438eca5a607fc57",
    },
}


class WorkflowContractError(AssertionError):
    pass


def _load_current_ci_policy(root: Path) -> dict:
    inventory_path = root / ".github" / "actions-pins.json"
    _require(inventory_path.is_file(), "action pin inventory is missing")
    _require(not inventory_path.is_symlink(), "action pin inventory must not be a symlink")
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowContractError(f"action pin inventory is invalid JSON: {exc}") from exc
    _require(inventory.get("schema_version") == 2, "action pin inventory schema must be 2")
    workflow_profiles = inventory.get("workflow_profiles")
    _require(
        isinstance(workflow_profiles, dict)
        and workflow_profiles.get(".github/workflows/ci.yml") == CURRENT_PROFILE,
        "CI workflow must map explicitly to current_ci",
    )
    profiles = inventory.get("profiles")
    _require(isinstance(profiles, dict), "action pin profiles are missing")
    profile = profiles.get(CURRENT_PROFILE)
    _require(isinstance(profile, dict), "current_ci action pin profile is missing")
    _require(profile.get("lifecycle") == "current", "current_ci lifecycle must be current")
    _require(
        profile.get("recommended_for_new_workflows") is True,
        "current_ci must be the recommended workflow profile",
    )
    actions = profile.get("actions")
    _require(
        isinstance(actions, dict) and set(actions) == set(EXPECTED_CURRENT_ACTIONS),
        "current_ci action set differs",
    )
    expected_fields = {
        "repository",
        "release_tag",
        "workflow_comment",
        "commit",
        "declared_runtime",
        "official_repository",
        "action_yml_blob",
        "lifecycle",
        "occurrence_count",
    }
    for repository, expected in EXPECTED_CURRENT_ACTIONS.items():
        entry = actions[repository]
        _require(
            isinstance(entry, dict) and set(entry) == expected_fields,
            f"{repository} current pin fields differ",
        )
        _require(entry["repository"] == repository, f"{repository} repository identity differs")
        _require(
            entry["release_tag"] == expected["release_tag"]
            and entry["workflow_comment"] == expected["release_tag"],
            f"{repository} exact release tag differs",
        )
        _require(entry["commit"] == expected["commit"], f"{repository} selected commit differs")
        _require(
            entry["action_yml_blob"] == expected["action_yml_blob"],
            f"{repository} action.yml blob identity differs",
        )
        _require(
            entry["declared_runtime"] == "node24",
            f"{repository} current runtime must be node24",
        )
        _require(
            entry["official_repository"] is True,
            f"{repository} must be recorded as an official repository",
        )
        _require(entry["lifecycle"] == "current", f"{repository} lifecycle must be current")
        _require(entry["occurrence_count"] == 1, f"{repository} occurrence count must be one")
        _require(
            IMMUTABLE_ACTION_REF.fullmatch(entry["commit"]) is not None,
            f"{repository} selected commit must be a full lowercase SHA",
        )
        _require(
            IMMUTABLE_ACTION_REF.fullmatch(entry["action_yml_blob"]) is not None,
            f"{repository} action.yml blob must be a full lowercase SHA",
        )
        _require(
            re.fullmatch(r"v\d+\.\d+\.\d+", entry["release_tag"]) is not None,
            f"{repository} selected tag must be a stable semantic version",
        )
    return actions


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
    validate_workflow(raw.decode("utf-8"), _load_current_ci_policy(resolved_root))


def main() -> int:
    verify_workflow_file(REPO_ROOT)
    workflow = _normalize_newlines(WORKFLOW_PATH.read_text(encoding="utf-8"))
    current_policy = _load_current_ci_policy(REPO_ROOT)
    _test_negative_mutations(workflow)
    _test_immutable_action_policy(workflow)
    positive_count, negative_count = _test_secure_extraction_fixtures(workflow)
    validate_workflow(workflow + "\n# Harmless trailing comment.\n", current_policy)
    _verify_upstream_download_artifact_warning_contract()
    print(
        "CI workflow smoke tests passed: exact Node 24 pins, raw artifact controls, "
        f"{positive_count} secure extraction positive fixture and "
        f"{negative_count} secure extraction negative fixtures"
    )
    return 0


def validate_workflow(workflow: str, current_policy: dict | None = None) -> None:
    if current_policy is None:
        current_policy = _load_current_ci_policy(REPO_ROOT)
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
    _require_current_action(checkout, "actions/checkout", current_policy)
    checkout_with = _mapping_block(checkout, "with", 8)
    _require(
        _direct_mapping_pairs(checkout_with, 10)
        == [("fetch-depth", "0"), ("persist-credentials", "false")],
        "checkout inputs must retain fetch-depth and disable persisted credentials",
    )
    _require(
        _scalar_value(checkout_with, "fetch-depth", 10) == "0",
        "checkout action must use fetch-depth: 0 for full history",
    )
    _require(
        _scalar_value(checkout_with, "persist-credentials", 10) == "false",
        "checkout action must use persist-credentials: false",
    )
    _require(
        _scalar_value(checkout, "ref", 10) is None,
        "checkout must not use an arbitrary ref",
    )

    setup_python = _action_step(steps, "actions/setup-python")
    setup_ref = _action_ref(setup_python, "actions/setup-python")
    _require_safe_action_ref("actions/setup-python", setup_ref)
    _require_current_action(setup_python, "actions/setup-python", current_policy)
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
    _require_current_action(upload_step, "actions/upload-artifact", current_policy)
    _require(
        "id: upload-release-bundle" in "\n".join(upload_step),
        "synthetic upload step ID is missing",
    )
    upload_with = _mapping_block(upload_step, "with", 8)
    _require(
        _direct_mapping_pairs(upload_with, 10)
        == [
            ("name", "ci-release-bundle-${{ github.run_id }}-${{ github.run_attempt }}"),
            (
                "path",
                "${{ runner.temp }}/s9h-ci-release-${{ github.run_id }}-${{ github.run_attempt }}/publish-bundle",
            ),
            ("if-no-files-found", "error"),
            ("compression-level", "0"),
            ("overwrite", "false"),
            ("include-hidden-files", "false"),
            ("retention-days", "1"),
        ],
        "synthetic upload inputs, retention-days or archive behavior changed",
    )
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
    _require(len(handoff_steps) == 4, "handoff job must contain exactly four steps")
    output_check = _named_step(handoff_steps, "Validate immutable synthetic bundle outputs")
    download = _action_step(handoff_steps, "actions/download-artifact")
    extractor = _named_step(handoff_steps, RAW_EXTRACTION_STEP)
    verifier = _named_step(handoff_steps, "Verify synthetic release bundle handoff")
    _require(
        handoff_steps == [output_check, download, extractor, verifier],
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
    _require_current_action(download, "actions/download-artifact", current_policy)
    download_text = "\n".join(download)
    download_env = _mapping_block(download, "env", 8)
    _require(
        _direct_mapping_pairs(download_env, 10)
        == [("NODE_OPTIONS", "--throw-deprecation")],
        "handoff download NODE_OPTIONS must fail on Node deprecations",
    )
    download_with = _mapping_block(download, "with", 8)
    _require(
        _scalar_value(download_with, "merge-multiple", 10) == "true",
        "handoff download merge-multiple must be the YAML boolean true",
    )
    _require(
        _scalar_value(download_with, "skip-decompress", 10) == "true",
        "handoff download skip-decompress must be the YAML boolean true",
    )
    _require(
        _scalar_value(download_with, "digest-mismatch", 10) == "error",
        "handoff download digest mismatch must fail with error",
    )
    _require(
        _direct_mapping_pairs(download_with, 10)
        == [
            ("artifact-ids", "${{ needs.windows-smoke.outputs.artifact-id }}"),
            ("path", "artifact-download"),
            ("merge-multiple", "true"),
            ("skip-decompress", "true"),
            ("digest-mismatch", "error"),
        ],
        "handoff download must preserve the immutable raw-artifact contract",
    )
    extractor_text = "\n".join(extractor)
    extractor_env = _mapping_block(extractor, "env", 8)
    _require(
        _direct_mapping_pairs(extractor_env, 10)
        == [
            (
                "ARTIFACT_DIGEST",
                "${{ needs.windows-smoke.outputs.artifact-digest }}",
            )
        ],
        "secure extraction must use only the immutable producer digest",
    )
    for required in (
        "Add-Type -AssemblyName System.IO.Compression",
        "Get-RawZipEntryNames",
        "raw NUL or backslash entry name",
        "$entryName -cne [string]$entry.FullName",
        "$MaxEntryCount = 128",
        "$MaxTotalUncompressedBytes = 16777216",
        RAW_EXTRACTION_SUCCESS,
        'Join-Path $workspace "artifact-download"',
        'Join-Path $workspace "release-bundle"',
        "Assert-BelowRoot $workspace $rawRoot",
        "Assert-BelowRoot $workspace $destinationRoot",
        "$rawFiles.Count -ne 1",
        "$rawDirectories.Count -ne 0",
        "$rawReparsePoints.Count -ne 0",
        '^[0-9a-f]{64}$',
        "Get-FileHash -LiteralPath $rawArchive.FullName -Algorithm SHA256",
        "$actualDigest -cne $env:ARTIFACT_DIGEST",
        "[IO.Compression.ZipArchive]::new",
        "$entries.Count -gt $MaxEntryCount",
        "$entryName.IndexOf([char]0)",
        '$entryName.Contains("\\")',
        '$entryName.Contains(":")',
        '$entryName.StartsWith("/")',
        "[IO.Path]::IsPathRooted($entryName)",
        "$_ -ceq \".\" -or $_ -ceq \"..\"",
        "[StringComparer]::Ordinal",
        "[StringComparer]::OrdinalIgnoreCase",
        "$caseInsensitivePaths.Add($normalizedPath)",
        "$unixFileType -eq 0xA000",
        "$declaredTotal -gt $MaxTotalUncompressedBytes",
        'Assert-BelowRoot $destinationRoot $targetPath "Raw artifact ZIP entry"',
        "[IO.FileMode]::CreateNew",
        "$actualTotal -gt $MaxTotalUncompressedBytes",
        "$archive.Dispose()",
        "$archiveStream.Dispose()",
        "Release bundle contains a reparse point after extraction",
        "[IO.Directory]::Delete($rawRoot, $true)",
        "Raw artifact root was not removed after extraction",
        "Write-Host $SuccessMessage",
    ):
        _require(required in extractor_text, f"secure extraction contract is missing: {required}")
    for forbidden in (
        "Expand-Archive",
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "Start-Process",
        "python ",
        "pip ",
        "gh api",
        "curl ",
        "wget ",
        "7z ",
    ):
        _require(
            forbidden.casefold() not in extractor_text.casefold(),
            f"secure extraction uses a forbidden command: {forbidden}",
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
    _require(
        handoff_text.count("actions/download-artifact@") == 1,
        "handoff job must contain exactly one download action",
    )
    for forbidden in (
        "NODE_NO_WARNINGS",
        "--no-deprecation",
        "--no-warnings",
        "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION",
        "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24",
    ):
        _require(forbidden not in handoff_text, f"handoff warning suppression is forbidden: {forbidden}")

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
        r"(?im)^\s*git\s+(?:push|fetch|pull|clone|submodule)\b",
        "CI must not require persisted Git credentials",
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


def _require_current_action(step: list[str], action: str, current_policy: dict) -> None:
    text = "\n".join(step)
    match = re.search(
        rf"(?m)^\s*(?:-\s*)?uses:\s*{re.escape(action)}@([^\s#]+)\s+#\s*"
        rf"(v\d+\.\d+\.\d+)\s*$",
        text,
    )
    _require(match is not None, f"{action} must include an exact semantic-version comment")
    ref, comment = match.groups()
    expected = current_policy[action]
    _require(ref == expected["commit"], f"{action} ref differs from selected release")
    _require(
        comment == expected["workflow_comment"],
        f"{action} semantic-version comment differs from inventory",
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
            "missing persist credentials control",
            _replace_once(workflow, "          persist-credentials: false\n", ""),
            "credentials",
        ),
        (
            "persisted checkout credentials",
            _replace_once(
                workflow,
                "          persist-credentials: false",
                "          persist-credentials: true",
            ),
            "credentials",
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
            "missing skip-decompress",
            _replace_once(workflow, "          skip-decompress: true\n", ""),
            "skip-decompress",
        ),
        (
            "false skip-decompress",
            _replace_once(
                workflow,
                "          skip-decompress: true",
                "          skip-decompress: false",
            ),
            "skip-decompress",
        ),
        (
            "quoted true skip-decompress",
            _replace_once(
                workflow,
                "          skip-decompress: true",
                '          skip-decompress: "true"',
            ),
            "skip-decompress",
        ),
        (
            "missing digest mismatch control",
            _replace_once(workflow, "          digest-mismatch: error\n", ""),
            "digest mismatch",
        ),
        (
            "ignored digest mismatch",
            _replace_once(
                workflow,
                "          digest-mismatch: error",
                "          digest-mismatch: ignore",
            ),
            "digest mismatch",
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
            "raw-artifact contract",
        ),
        (
            "nested artifact destination",
            _replace_once(
                workflow,
                "          path: artifact-download",
                "          path: artifact-download/nested",
            ),
            "raw-artifact contract",
        ),
        (
            "artifact name input",
            _replace_once(
                workflow,
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}",
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}\n"
                "          name: synthetic-release-bundle",
            ),
            "raw-artifact contract",
        ),
        (
            "artifact pattern input",
            _replace_once(
                workflow,
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}",
                "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}\n"
                "          pattern: synthetic-*",
            ),
            "raw-artifact contract",
        ),
        (
            "missing throw-deprecation",
            _replace_once(
                workflow,
                "        env:\n          NODE_OPTIONS: --throw-deprecation\n",
                "",
            ),
            "mapping is missing: env",
        ),
        (
            "warning suppression",
            _replace_once(
                workflow,
                "          NODE_OPTIONS: --throw-deprecation",
                "          NODE_OPTIONS: --no-warnings",
            ),
            "NODE_OPTIONS",
        ),
        (
            "missing secure extraction step",
            _remove_named_step(workflow, RAW_EXTRACTION_STEP),
            "exactly four steps",
        ),
        (
            "secure extraction reordered",
            _move_named_step_before(
                workflow,
                "Verify synthetic release bundle handoff",
                RAW_EXTRACTION_STEP,
            ),
            "step order",
        ),
        (
            "secure extraction continue-on-error",
            _replace_once(
                workflow,
                f"      - name: {RAW_EXTRACTION_STEP}\n",
                f"      - name: {RAW_EXTRACTION_STEP}\n        continue-on-error: true\n",
            ),
            "continue-on-error",
        ),
        (
            "Expand-Archive extraction",
            _replace_once(
                workflow,
                "          Add-Type -AssemblyName System.IO.Compression",
                "          Expand-Archive -LiteralPath raw.zip -DestinationPath release-bundle",
            ),
            "secure extraction contract",
        ),
        (
            "raw digest comparison removed",
            _replace_once(
                workflow,
                "          if ($actualDigest -cne $env:ARTIFACT_DIGEST) {",
                "          if ($false) {",
            ),
            "secure extraction contract",
        ),
        (
            "exact-one-raw-file gate removed",
            _replace_once(
                workflow,
                "              $rawFiles.Count -ne 1 -or",
                "              $rawFiles.Count -lt 1 -or",
            ),
            "secure extraction contract",
        ),
        (
            "path traversal gate removed",
            _replace_once(
                workflow,
                '$_ -ceq "." -or $_ -ceq ".."',
                '$_ -ceq "." -or $_ -ceq "..."',
            ),
            "secure extraction contract",
        ),
        (
            "case-insensitive duplicate gate removed",
            _replace_once(
                workflow,
                "                  if (-not $caseInsensitivePaths.Add($normalizedPath)) {",
                "                  if ($false) {",
            ),
            "secure extraction contract",
        ),
        (
            "UNIX symlink gate removed",
            _replace_once(
                workflow,
                "                  if ($unixFileType -eq 0xA000) {",
                "                  if ($false) {",
            ),
            "secure extraction contract",
        ),
        (
            "declared extraction size cap removed",
            _replace_once(
                workflow,
                "                  if ($declaredTotal -gt $MaxTotalUncompressedBytes) {",
                "                  if ($false) {",
            ),
            "secure extraction contract",
        ),
        (
            "raw archive cleanup removed",
            _replace_once(
                workflow,
                "          [IO.Directory]::Delete($rawRoot, $true)",
                "          Write-Host \"raw archive retained\"",
            ),
            "secure extraction contract",
        ),
        (
            "missing handoff digest validation",
            _replace_once(
                workflow,
                "          ARTIFACT_ID: ${{ needs.windows-smoke.outputs.artifact-id }}\n"
                "          ARTIFACT_DIGEST: ${{ needs.windows-smoke.outputs.artifact-digest }}",
                "          ARTIFACT_ID: ${{ needs.windows-smoke.outputs.artifact-id }}\n"
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


def _test_secure_extraction_fixtures(workflow: str) -> tuple[int, int]:
    _require(os.name == "nt", "secure extraction fixtures require Windows")
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    _require(powershell is not None, "PowerShell is required for secure extraction fixtures")
    body = _secure_extraction_body(workflow)
    negative_labels: list[str] = []

    with tempfile.TemporaryDirectory(prefix="s9h-secure-extraction-") as temp_name:
        temp_root = Path(temp_name).resolve()

        positive_root = temp_root / "positive"
        positive_workspace = positive_root / "workspace"
        positive_raw = positive_workspace / "artifact-download"
        positive_raw.mkdir(parents=True)
        expected_files = {
            "RELEASE_MANIFEST.json": b'{"schema_version":3}\n',
            "assets/checksum.txt": b"0123456789abcdef\n",
        }
        positive_archive = positive_raw / "synthetic-artifact.bin"
        _write_fixture_zip(
            positive_archive,
            [
                ("assets/", b"", None),
                *[(name, data, None) for name, data in expected_files.items()],
            ],
        )
        positive_digest = _sha256_file(positive_archive)
        positive_result = _execute_extraction_body(
            powershell,
            body,
            positive_root,
            positive_workspace,
            positive_digest,
        )
        _require(
            positive_result.returncode == 0,
            "secure extraction positive fixture failed: "
            + _combined_process_output(positive_result),
        )
        positive_output = _combined_process_output(positive_result)
        _require(
            positive_output.count(RAW_EXTRACTION_SUCCESS) == 1,
            "secure extraction positive fixture success message differs",
        )
        positive_bundle = positive_workspace / "release-bundle"
        for relative, expected in expected_files.items():
            path = positive_bundle / Path(relative)
            _require(path.is_file(), f"secure extraction positive file is missing: {relative}")
            _require(
                path.read_bytes() == expected,
                f"secure extraction positive content differs: {relative}",
            )
        _require(not positive_raw.exists(), "secure extraction positive raw root remains")
        _require(
            not (positive_root / "outside.txt").exists(),
            "secure extraction positive fixture wrote outside the workspace",
        )

        def run_negative(
            label: str,
            entries: list[tuple[str, bytes, int | None]] | None = None,
            *,
            digest_override: str | None = None,
            extra_raw_file: bool = False,
            raw_subdirectory: bool = False,
            destination_preexists: bool = False,
            expect_clean_destination: bool = False,
            unsupported_compression_entry: str | None = None,
        ) -> None:
            case_root = temp_root / f"negative-{len(negative_labels) + 1:02d}"
            workspace = case_root / "workspace"
            raw_root = workspace / "artifact-download"
            raw_root.mkdir(parents=True)
            archive_path = raw_root / "synthetic-artifact.bin"
            digest = "0" * 64
            if entries is not None:
                _write_fixture_zip(archive_path, entries)
                if unsupported_compression_entry is not None:
                    _patch_zip_compression_method(
                        archive_path,
                        unsupported_compression_entry,
                        99,
                    )
                digest = _sha256_file(archive_path)
            if digest_override is not None:
                digest = digest_override
            if extra_raw_file:
                (raw_root / "unexpected.bin").write_bytes(b"unexpected")
            if raw_subdirectory:
                (raw_root / "unexpected-directory").mkdir()
            if destination_preexists:
                (workspace / "release-bundle").mkdir()
            result = _execute_extraction_body(
                powershell,
                body,
                case_root,
                workspace,
                digest,
            )
            output = _combined_process_output(result)
            _require(result.returncode != 0, f"negative extraction fixture was accepted: {label}")
            _require(
                RAW_EXTRACTION_SUCCESS not in output,
                f"negative extraction fixture printed success: {label}",
            )
            for outside_name in (
                "outside.txt",
                "outside-traversal.txt",
                "outside-resolved.txt",
                "absolute.txt",
            ):
                _require(
                    not (case_root / outside_name).exists()
                    and not (workspace.parent / outside_name).exists(),
                    f"negative extraction fixture wrote outside its destination: {label}",
                )
            if expect_clean_destination:
                _require(
                    not (workspace / "release-bundle").exists(),
                    f"partial extraction destination was not cleaned: {label}",
                )
            negative_labels.append(label)

        valid_entries = [("valid.txt", b"valid\n", None)]
        mismatch_root = temp_root / "digest-source"
        mismatch_root.mkdir()
        mismatch_archive = mismatch_root / "source.zip"
        _write_fixture_zip(mismatch_archive, valid_entries)
        mismatch = "0" * 64
        if _sha256_file(mismatch_archive) == mismatch:
            mismatch = "1" * 64
        run_negative("digest mismatch", valid_entries, digest_override=mismatch)
        run_negative("no raw file")
        run_negative("two raw files", valid_entries, extra_raw_file=True)
        run_negative("raw subdirectory", valid_entries, raw_subdirectory=True)

        reparse_case = temp_root / "negative-05"
        reparse_workspace = reparse_case / "workspace"
        reparse_workspace.mkdir(parents=True)
        reparse_target = reparse_case / "raw-target"
        reparse_target.mkdir()
        reparse_archive = reparse_target / "synthetic-artifact.bin"
        _write_fixture_zip(reparse_archive, valid_entries)
        reparse_root = reparse_workspace / "artifact-download"
        _create_directory_reparse_point(reparse_root, reparse_target)
        reparse_result = _execute_extraction_body(
            powershell,
            body,
            reparse_case,
            reparse_workspace,
            _sha256_file(reparse_archive),
        )
        reparse_output = _combined_process_output(reparse_result)
        _require(
            reparse_result.returncode != 0,
            "negative extraction fixture was accepted: raw-root reparse point",
        )
        _require(
            RAW_EXTRACTION_SUCCESS not in reparse_output,
            "raw-root reparse fixture printed success",
        )
        negative_labels.append("raw-root reparse point")

        run_negative("ZIP traversal", [("../outside-traversal.txt", b"x", None)])
        run_negative("absolute-path entry", [("/absolute.txt", b"x", None)])
        run_negative("backslash entry", [("nested\\escape.txt", b"x", None)])
        run_negative("colon entry", [("ads:name.txt", b"x", None)])
        run_negative("empty path segment", [("nested//empty.txt", b"x", None)])
        run_negative("dot path segment", [("nested/./dot.txt", b"x", None)])
        run_negative(
            "case-insensitive duplicates",
            [("Case.txt", b"A", None), ("case.txt", b"B", None)],
        )
        run_negative(
            "exact duplicates",
            [("same.txt", b"A", None), ("same.txt", b"B", None)],
        )
        symlink_attributes = (stat.S_IFLNK | 0o777) << 16
        run_negative(
            "UNIX symlink entry",
            [("link.txt", b"target.txt", symlink_attributes)],
        )
        run_negative(
            "excess entry count",
            [(f"entry-{index:03d}.txt", b"x", None) for index in range(129)],
        )
        run_negative(
            "excess uncompressed size",
            [("oversized.bin", b"\0" * (MAX_EXTRACTION_BYTES + 1), None)],
        )
        run_negative("destination pre-exists", valid_entries, destination_preexists=True)
        run_negative(
            "entry resolving outside destination",
            [("nested/../../outside-resolved.txt", b"x", None)],
        )
        run_negative(
            "partial extraction failure",
            [("good.txt", b"good", None), ("unsupported.bin", b"blocked", None)],
            expect_clean_destination=True,
            unsupported_compression_entry="unsupported.bin",
        )

        warning_mutation = _replace_once(
            workflow,
            "          NODE_OPTIONS: --throw-deprecation",
            "          NODE_OPTIONS: --no-warnings",
        )
        _expect_contract_failure(
            "warning-suppression environment mutation",
            warning_mutation,
            "NODE_OPTIONS",
        )
        negative_labels.append("warning-suppression environment mutation")

    _require(
        len(negative_labels) == 20,
        f"secure extraction negative fixture count differs: {len(negative_labels)}",
    )
    return 1, len(negative_labels)


def _secure_extraction_body(workflow: str) -> str:
    normalized = _normalize_newlines(workflow)
    jobs = _mapping_block(normalized.splitlines(), "jobs", 0)
    handoff = _mapping_block(jobs, "release-bundle-handoff", 2)
    extractor = _named_step(_step_blocks(handoff), RAW_EXTRACTION_STEP)
    run_index = next(
        (
            index
            for index, line in enumerate(extractor)
            if line == "        run: |"
        ),
        None,
    )
    _require(run_index is not None, "secure extraction run body is missing")
    body_lines = []
    for line in extractor[run_index + 1 :]:
        _require(
            not line.strip() or _indent(line) >= 10,
            "secure extraction run body indentation is invalid",
        )
        body_lines.append(line[10:] if len(line) >= 10 else "")
    body = "\n".join(body_lines).rstrip("\n") + "\n"
    _require("Set-StrictMode -Version Latest" in body, "secure extraction body is incomplete")
    return body


def _write_fixture_zip(
    path: Path,
    entries: list[tuple[str, bytes, int | None]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_name_replacements: list[tuple[bytes, bytes]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data, external_attributes in entries:
                info = zipfile.ZipInfo(name)
                if "\\" in name:
                    normalized_name = name.replace("\\", "/")
                    raw_name_replacements.append(
                        (normalized_name.encode("utf-8"), name.encode("utf-8"))
                    )
                info.compress_type = zipfile.ZIP_DEFLATED
                if external_attributes is not None:
                    info.create_system = 3
                    info.external_attr = external_attributes
                archive.writestr(info, data)
    if raw_name_replacements:
        payload = path.read_bytes()
        for normalized_name, raw_name in raw_name_replacements:
            _require(
                len(normalized_name) == len(raw_name)
                and payload.count(normalized_name) == 2,
                "backslash ZIP fixture name could not be patched exactly",
            )
            payload = payload.replace(normalized_name, raw_name)
        path.write_bytes(payload)


def _execute_extraction_body(
    powershell: str,
    body: str,
    case_root: Path,
    workspace: Path,
    digest: str,
) -> subprocess.CompletedProcess[str]:
    case_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    script_path = case_root / "workflow-secure-extraction.ps1"
    script_path.write_text(body, encoding="utf-8", newline="\n")
    environment = os.environ.copy()
    environment["GITHUB_WORKSPACE"] = str(workspace.resolve())
    environment["ARTIFACT_DIGEST"] = digest
    return subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _patch_zip_compression_method(path: Path, entry_name: str, method: int) -> None:
    payload = bytearray(path.read_bytes())
    encoded_name = entry_name.encode("utf-8")
    positions = []
    start = 0
    while True:
        position = payload.find(encoded_name, start)
        if position < 0:
            break
        positions.append(position)
        start = position + len(encoded_name)
    _require(
        len(positions) == 2,
        "unsupported-compression ZIP fixture entry count differs",
    )
    patched = 0
    for position in positions:
        if position >= 30 and payload[position - 30 : position - 26] == b"PK\x03\x04":
            payload[position - 22 : position - 20] = method.to_bytes(2, "little")
            patched += 1
        elif position >= 46 and payload[position - 46 : position - 42] == b"PK\x01\x02":
            payload[position - 36 : position - 34] = method.to_bytes(2, "little")
            patched += 1
    _require(patched == 2, "unsupported-compression ZIP fixture headers differ")
    path.write_bytes(payload)


def _create_directory_reparse_point(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    _require(
        result.returncode == 0 and link.exists(),
        "raw-root reparse fixture is unsupported: " + _combined_process_output(result),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _combined_process_output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout + "\n" + result.stderr).strip()


def _verify_upstream_download_artifact_warning_contract() -> None:
    expected = {
        "release": "v8.0.1",
        "commit": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "issue": "actions/download-artifact#484",
        "warning_path": "upstream extraction path",
        "supported_input": "skip-decompress",
        "digest_check": "remains inside actions/download-artifact",
        "repository_extraction": "independently fail-closed",
        "node_options": "--throw-deprecation",
    }
    _require(
        UPSTREAM_DOWNLOAD_ARTIFACT_WARNING_CONTRACT == expected,
        "upstream download-artifact warning regression contract differs",
    )


def _test_immutable_action_policy(workflow: str) -> None:
    checkout_ref = _workflow_action_ref(workflow, "actions/checkout")
    setup_ref = _workflow_action_ref(workflow, "actions/setup-python")
    upload_ref = _workflow_action_ref(workflow, "actions/upload-artifact")
    download_ref = _workflow_action_ref(workflow, "actions/download-artifact")

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
            "different valid full SHA",
            _replace_action_ref(workflow, "actions/checkout", "a" * 40),
        ),
        (
            "semantic-version comment mismatch",
            _replace_once(workflow, "# v6.1.0", "# v6.0.0"),
        ),
        (
            "third-party checkout substitution",
            _replace_once(workflow, "actions/checkout@", "third-party/checkout@"),
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
