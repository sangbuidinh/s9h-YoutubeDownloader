import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import smoke_release_bundle as bundle_smoke


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
BASELINE_COMMIT = "9ca319d01164c4bb353816175f429d564e41ee7d"


@dataclass(frozen=True)
class ReleaseContract:
    path: str
    tag: str
    title: str
    build_command: str
    build_step: str
    prerelease: str
    fixed_tag: bool

    @property
    def zip_name(self) -> str:
        return f"Youtube-Downloaderbs-{self.tag}.zip"

    @property
    def legal_name(self) -> str:
        return f"Youtube-Downloaderbs-{self.tag}-legal.zip"

    @property
    def aria2_source_name(self) -> str:
        return f"Youtube-Downloaderbs-{self.tag}-aria2-source.zip"

    @property
    def ffmpeg_source_name(self) -> str:
        return f"Youtube-Downloaderbs-{self.tag}-ffmpeg-source.zip"


CONTRACTS = (
    ReleaseContract(
        ".github/workflows/prerelease-v1.2.7-rc.1.yml",
        "v1.2.7-rc.1",
        "Youtube Downloaderbs v1.2.7-rc.1",
        r".\scripts\build_prerelease_v1_2_7_rc1.ps1",
        "Build and validate assets",
        "true",
        False,
    ),
    ReleaseContract(
        ".github/workflows/prerelease-v1.3.0-rc.1.yml",
        "v1.3.0-rc.1",
        "Youtube Downloaderbs v1.3.0-rc.1",
        r".\scripts\build_prerelease_v1_3_0_rc1.ps1 -PreparePinnedRuntime",
        "Build and validate checksum-pinned assets",
        "true",
        True,
    ),
    ReleaseContract(
        ".github/workflows/release-v1.3.0.yml",
        "v1.3.0",
        "Youtube Downloaderbs v1.3.0",
        r".\scripts\build_release_v1_3_0.ps1 -PreparePinnedRuntime",
        "Build and validate checksum-pinned assets",
        "false",
        True,
    ),
    ReleaseContract(
        ".github/workflows/release-v1.3.1.yml",
        "v1.3.1",
        "Youtube Downloaderbs v1.3.1",
        r".\scripts\build_release_v1_3_1.ps1 -PreparePinnedRuntime",
        "Build and validate checksum-pinned assets",
        "false",
        True,
    ),
    ReleaseContract(
        ".github/workflows/release-v1.3.2.yml",
        "v1.3.2",
        "Youtube Downloaderbs v1.3.2",
        r".\scripts\build_release_v1_3_2.ps1 -PreparePinnedRuntime",
        "Build and validate checksum-pinned assets",
        "false",
        True,
    ),
)
HISTORICAL_CONTRACTS = CONTRACTS[:-1]


class WorkflowSplitError(AssertionError):
    pass


def main() -> int:
    documents = {
        contract.path: _normalize_newlines((REPO_ROOT / contract.path).read_text(encoding="utf-8"))
        for contract in CONTRACTS
    }
    validate_contracts(documents)
    _validate_historical_bundle_commands()
    _test_negative_mutations(documents)
    print("release workflow split smoke tests passed: 5 versioned v2 command paths exercised")
    return 0


def validate_contracts(documents: dict[str, str]) -> None:
    _require(set(documents) == {contract.path for contract in CONTRACTS}, "workflow set is invalid")
    for contract in CONTRACTS:
        _validate_workflow(contract, documents[contract.path])


def _validate_workflow(contract: ReleaseContract, workflow: str) -> None:
    workflow = _normalize_newlines(workflow)
    lines = workflow.splitlines()
    trigger = _mapping_block(lines, "on", 0)
    _require(
        _direct_mapping_keys(trigger, 2) == ["workflow_dispatch"],
        f"{contract.path} must remain manual-only",
    )
    top_permissions = _mapping_block(lines, "permissions", 0)
    _require(
        _direct_mapping_pairs(top_permissions, 2) == [("contents", "read")],
        f"{contract.path} top-level permissions must be read only",
    )

    jobs = _mapping_block(lines, "jobs", 0)
    _require(
        _direct_mapping_keys(jobs, 2) == ["build", "publish"],
        f"{contract.path} must contain exactly build and publish jobs",
    )
    build = _mapping_block(jobs, "build", 2)
    publish = _mapping_block(jobs, "publish", 2)
    _require(_scalar_value(build, "runs-on", 4) == "windows-2022", f"{contract.path} build runner changed")
    _require(_scalar_value(publish, "runs-on", 4) == "windows-2022", f"{contract.path} publish runner changed")
    _require(
        _direct_mapping_pairs(_mapping_block(build, "permissions", 4), 6)
        == [("contents", "read")],
        f"{contract.path} build permissions must be read only",
    )
    _require(
        _direct_mapping_pairs(_mapping_block(publish, "permissions", 4), 6)
        == [("contents", "write")],
        f"{contract.path} publish permissions must be write only",
    )
    _require(_scalar_value(publish, "needs", 4) == "build", f"{contract.path} publish must need build")

    outputs = _direct_mapping_pairs(_mapping_block(build, "outputs", 4), 6)
    _require(
        outputs
        == [
            ("artifact-id", "${{ steps.upload-release-bundle.outputs.artifact-id }}"),
            ("artifact-digest", "${{ steps.upload-release-bundle.outputs.artifact-digest }}"),
            ("source-commit", "${{ steps.release-metadata.outputs.source-commit }}"),
            ("control-commit", "${{ steps.release-metadata.outputs.control-commit }}"),
        ],
        f"{contract.path} build outputs are invalid",
    )

    build_steps = _step_blocks(build)
    publish_steps = _step_blocks(publish)
    checkout_steps = _action_steps(build_steps, "actions/checkout")
    _require(bool(checkout_steps), f"{contract.path} build checkout is missing")
    _require(len(_action_steps(build_steps, "actions/setup-python")) == 1, f"{contract.path} setup-python count changed")
    setup_python = _action_steps(build_steps, "actions/setup-python")[0]
    python_verification = _named_step(build_steps, "Verify pinned Python version")
    legal_gate = _named_step(build_steps, "Enforce fail-closed release legal gate")
    installer = _named_step(build_steps, "Install locked build dependencies")
    build_step = _named_step(build_steps, contract.build_step)
    legal_payload_step = _named_step(build_steps, "Prepare and verify release legal payload")
    bundle_step = _named_step(build_steps, "Create and verify release bundle")
    upload = _single_action_step(build_steps, "actions/upload-artifact")
    _validate_legal_gate(contract, build_steps, legal_gate)
    _require(
        build_steps.index(setup_python)
        < build_steps.index(python_verification)
        < build_steps.index(legal_gate)
        < build_steps.index(installer)
        < build_steps.index(build_step),
        f"{contract.path} legal gate and build order is invalid",
    )
    _require(
        build_steps.index(build_step)
        < build_steps.index(legal_payload_step)
        < build_steps.index(bundle_step)
        < build_steps.index(upload),
        f"{contract.path} build/legal/bundle/upload order is invalid",
    )
    _require(contract.build_command in "\n".join(build_step), f"{contract.path} build command changed")
    legal_payload_text = "\n".join(legal_payload_step)
    legal_tool = (
        "..\\control\\scripts\\prepare_release_legal_payload.py"
        if contract.fixed_tag
        else "scripts/prepare_release_legal_payload.py"
    )
    for required in (
        f"python {legal_tool} create",
        f"python {legal_tool} verify",
        "--control-root " + ("..\\control" if contract.fixed_tag else "."),
        f"--portable-zip release/assets/{contract.zip_name}",
        f"--output-zip release/assets/{contract.legal_name}",
        "--release-notes release/RELEASE_NOTES.md",
        f"--tag {contract.tag}",
    ):
        _require(required in legal_payload_text, f"{contract.path} legal payload step is missing: {required}")
    _require(
        "\n".join(build).count("Prepare and verify release legal payload") == 1
        and "\n".join(build).count("prepare_release_legal_payload.py create") == 1
        and "\n".join(build).count("prepare_release_legal_payload.py verify") == 1
        and legal_payload_text.count("--release-notes release/RELEASE_NOTES.md") == 2,
        f"{contract.path} legal payload step count changed",
    )
    bundle_text = "\n".join(bundle_step)
    _require("--sbom-input" not in bundle_text, f"{contract.path} silently switched bundle contract")
    for required in (
        "prepare_release_bundle.py create",
        "prepare_release_bundle.py verify",
        "release-assets-v2.json",
        f"--legal-payload release/assets/{contract.legal_name}",
        "--source-assets-root release/source-assets",
        "--require-release-ready false",
    ):
        _require(required in bundle_text, f"{contract.path} bundle v2 step is missing: {required}")
    for forbidden in ("New-Item", "SYNTHETIC_SOURCE_FIXTURE", "SOURCE_MANIFEST.json"):
        _require(forbidden not in legal_payload_text + bundle_text, f"{contract.path} creates a source placeholder")
    _require(
        re.search(
            r"(?i)(?:New-Item|Set-Content|write_bytes|writestr)[^\n]*source-assets",
            "\n".join(build),
        )
        is None,
        f"{contract.path} creates an empty source placeholder",
    )
    _require("softprops/action-gh-release@" not in "\n".join(build), f"{contract.path} build must not publish")
    _require("actions/download-artifact@" not in "\n".join(build), f"{contract.path} build must not download artifacts")

    upload_text = "\n".join(upload)
    _require("id: upload-release-bundle" in upload_text, f"{contract.path} upload ID is missing")
    upload_with = _direct_mapping_pairs(_mapping_block(upload, "with", 8), 10)
    expected_upload_path = "release/publish-bundle" if not contract.fixed_tag else "source/release/publish-bundle"
    _require(
        upload_with
        == [
            ("name", f"release-bundle-{contract.tag}-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}"),
            ("path", expected_upload_path),
            ("if-no-files-found", "error"),
            ("compression-level", "0"),
            ("overwrite", "false"),
            ("include-hidden-files", "false"),
            ("retention-days", "7"),
        ],
        f"{contract.path} upload settings are invalid",
    )
    report = "\n".join(_named_step(build_steps, "Report immutable release bundle handoff"))
    _require("ARTIFACT_DIGEST" in report and "^[0-9a-f]{64}$" in report, f"{contract.path} artifact digest validation is missing")

    publish_checkout = _single_action_step(publish_steps, "actions/checkout")
    publish_checkout_with = _direct_mapping_pairs(
        _mapping_block(publish_checkout, "with", 8), 10
    )
    _require(
        publish_checkout_with
        == [
            ("ref", "${{ needs.build.outputs.control-commit }}"),
            ("path", "control"),
            ("persist-credentials", "false"),
        ],
        f"{contract.path} publish control checkout is invalid",
    )
    publish_setup = _single_action_step(publish_steps, "actions/setup-python")
    _require(
        _direct_mapping_pairs(_mapping_block(publish_setup, "with", 8), 10)
        == [("python-version", "3.11.9")],
        f"{contract.path} publish Python selector changed",
    )
    publish_python_check = _named_step(publish_steps, "Verify publish Python version")
    publish_python_check_text = "\n".join(publish_python_check)
    for required in (
        "python --version",
        '$VersionOutput = (& python --version 2>&1 | Out-String).Trim()',
        'if ($VersionOutput -ne "Python 3.11.9")',
    ):
        _require(
            required in publish_python_check_text,
            f"{contract.path} publish Python verification is missing: {required}",
        )

    download = _single_action_step(publish_steps, "actions/download-artifact")
    download_with_block = _mapping_block(download, "with", 8)
    _require(
        _raw_scalar_value(download_with_block, "merge-multiple", 10) == "true",
        f"{contract.path} merge-multiple must be the YAML boolean true",
    )
    download_with = _direct_mapping_pairs(download_with_block, 10)
    _require(
        download_with
        == [
            ("artifact-ids", "${{ needs.build.outputs.artifact-id }}"),
            ("path", "release-bundle"),
            ("merge-multiple", "true"),
        ],
        f"{contract.path} download must use only the build artifact ID",
    )
    output_validation = "\n".join(_named_step(publish_steps, "Validate immutable build outputs"))
    for required in ("ARTIFACT_ID", "ARTIFACT_DIGEST", "SOURCE_COMMIT", "CONTROL_COMMIT", "^[0-9a-f]{64}$"):
        _require(required in output_validation, f"{contract.path} output validation is missing: {required}")

    verifier = _named_step(publish_steps, "Verify downloaded release bundle")
    verifier_text = "\n".join(verifier)
    for required in (
        "Release bundle v2 handoff verified",
        "Get-FileHash",
        "ConvertFrom-Json",
        "s9h-release-bundle-v2",
        "legal-payload",
        "aria2-source",
        "ffmpeg-source",
        contract.legal_name,
        contract.aria2_source_name,
        contract.ffmpeg_source_name,
        "SHA256SUMS.txt",
        "RELEASE_NOTES.md",
        "ReparsePoint",
        "${{ needs.build.outputs.source-commit }}",
        "${{ needs.build.outputs.control-commit }}",
        '$bundle = [IO.Path]::GetFullPath((Join-Path $workspace "release-bundle"))',
    ):
        _require(required in verifier_text, f"{contract.path} inline verifier is missing: {required}")
    for forbidden in ("Invoke-Expression", "Start-Process", "& release-bundle", ".ps1", ".cmd", ".bat"):
        _require(forbidden not in verifier_text, f"{contract.path} inline verifier may execute downloaded code: {forbidden}")

    publish_ready = _named_step(publish_steps, "Enforce publish-ready release bundle")
    publish_ready_text = "\n".join(publish_ready)
    _require(
        len(
            re.findall(
                r"(?m)^\s*python control/scripts/prepare_release_bundle\.py verify\s*`?\s*$",
                publish_ready_text,
            )
        )
        == 1,
        f"{contract.path} must execute exactly one real publish-ready verifier",
    )
    for required in (
        "--bundle-root release-bundle",
        f"--tag {contract.tag}",
        '--source-commit "${{ needs.build.outputs.source-commit }}"',
        '--control-commit "${{ needs.build.outputs.control-commit }}"',
        f"--prerelease {contract.prerelease}",
        "--policy control/legal/release-policy.json",
        "--asset-contract control/legal/release-assets-v2.json",
        f"--legal-payload release-bundle/assets/{contract.legal_name}",
        "--source-assets-root release-bundle/assets",
        "--require-release-ready true",
        "release bundle is not approved for publishing",
    ):
        _require(
            required in publish_ready_text,
            f"{contract.path} publish-ready verifier is missing: {required}",
        )
    _require(
        re.search(r"(?m)^\s+(?:continue-on-error|if)\s*:", publish_ready_text) is None,
        f"{contract.path} publish-ready verifier contains a YAML bypass",
    )
    for forbidden in ("SilentlyContinue", "-ErrorAction Ignore", "2>$null", "||", "; exit 0"):
        _require(
            forbidden not in publish_ready_text,
            f"{contract.path} publish-ready verifier contains a bypass: {forbidden}",
        )

    absence = _named_step(publish_steps, "Confirm release absence immediately before publishing")
    release = _single_action_step(publish_steps, "softprops/action-gh-release")
    output_validation_step = _named_step(publish_steps, "Validate immutable build outputs")
    _require(
        publish_steps.index(output_validation_step)
        < publish_steps.index(publish_checkout)
        < publish_steps.index(publish_setup)
        < publish_steps.index(publish_python_check)
        < publish_steps.index(download)
        < publish_steps.index(verifier)
        < publish_steps.index(publish_ready)
        < publish_steps.index(absence)
        < publish_steps.index(release),
        f"{contract.path} publish verification order is invalid",
    )
    _require(publish_steps.index(absence) + 1 == publish_steps.index(release), f"{contract.path} final absence check must immediately precede release")
    absence_text = "\n".join(absence)
    _require(f"gh release view $tag" in absence_text and f'$tag = "{contract.tag}"' in absence_text, f"{contract.path} final release absence check changed")
    _require("GH_TOKEN: ${{ github.token }}" in absence_text, f"{contract.path} final release check token wiring changed")

    publish_text = "\n".join(publish)
    for forbidden in (
        "actions/upload-artifact@",
        "Install locked build dependencies",
        "install_build_dependencies.py",
        contract.build_command,
        "pip install",
        "python -m pip",
        "continue-on-error",
    ):
        _require(forbidden not in publish_text, f"{contract.path} publish contains forbidden behavior: {forbidden}")
    direct_python_commands = re.findall(r"(?m)^\s*python\s+([^\r\n]+)$", publish_text)
    _require(
        direct_python_commands
        == ["--version", "control/scripts/prepare_release_bundle.py verify `"],
        f"{contract.path} publish Python command allowlist changed",
    )

    release_with = _direct_mapping_pairs(_mapping_block(release, "with", 8), 10)
    expected_release_inputs = [
        ("tag_name", contract.tag),
        ("name", contract.title),
        ("prerelease", contract.prerelease),
    ]
    if contract.fixed_tag:
        expected_release_inputs.append(("draft", "false"))
    else:
        expected_release_inputs.append(("target_commitish", "${{ github.sha }}"))
    expected_release_inputs.extend(
        [
            ("body_path", "release-bundle/RELEASE_NOTES.md"),
            ("fail_on_unmatched_files", "true"),
        ]
    )
    _require(release_with[: len(expected_release_inputs)] == expected_release_inputs, f"{contract.path} publication inputs changed unexpectedly")
    files = _sequence_block(release, "files", 10)
    _require(
        files
        == [
            "release-bundle/assets/Youtube.Downloaderbs.exe",
            f"release-bundle/assets/{contract.zip_name}",
            f"release-bundle/assets/{contract.legal_name}",
            f"release-bundle/assets/{contract.aria2_source_name}",
            f"release-bundle/assets/{contract.ffmpeg_source_name}",
            "release-bundle/assets/SHA256SUMS.txt",
            "release-bundle/RELEASE_MANIFEST.json",
        ],
        f"{contract.path} published file set is invalid",
    )

    if contract.fixed_tag:
        _require(len(checkout_steps) == 2, f"{contract.path} must have control and source checkouts")
        control_with = _direct_mapping_pairs(_mapping_block(checkout_steps[0], "with", 8), 10)
        source_with = _direct_mapping_pairs(_mapping_block(checkout_steps[1], "with", 8), 10)
        _require(control_with == [("path", "control")], f"{contract.path} control checkout changed")
        _require(source_with == [("path", "source"), ("ref", contract.tag), ("fetch-depth", "0")], f"{contract.path} fixed tag checkout changed")
        _require(
            build_steps.index(legal_gate) < build_steps.index(checkout_steps[1]),
            f"{contract.path} legal gate must precede release-tag checkout",
        )
        _require("working-directory: source" in "\n".join(build_step), f"{contract.path} build must execute from source")
        _require("python ..\\control\\scripts\\prepare_release_bundle.py" in "\n".join(bundle_step), f"{contract.path} bundle tool must execute from control source")
    else:
        _require(len(checkout_steps) == 1, f"{contract.path} must retain current-source checkout semantics")
        checkout_with = _mapping_block(checkout_steps[0], "with", 8)
        _require(not checkout_with, f"{contract.path} current-source checkout must not set ref or path")
        _require("target_commitish: ${{ github.sha }}" in "\n".join(release), f"{contract.path} target commit semantics changed")


def _validate_legal_gate(
    contract: ReleaseContract, build_steps: list[list[str]], gate_step: list[str]
) -> None:
    gate_text = "\n".join(gate_step)
    prefix = "control/" if contract.fixed_tag else ""
    verifier = f"python {prefix}scripts/verify_release_legal_gate.py"
    policy = f"--policy {prefix}legal/release-policy.json"
    for required in (verifier, policy, f"--tag {contract.tag}"):
        _require(required in gate_text, f"{contract.path} legal gate is missing: {required}")
    build_text = "\n".join("\n".join(step) for step in build_steps)
    _require(build_text.count("verify_release_legal_gate.py") == 1, f"{contract.path} legal gate count changed")
    _require(build_text.count("release-policy.json") == 3, f"{contract.path} release policy path count changed")
    _require(
        re.search(r"(?m)^\s+(?:continue-on-error|if|env)\s*:", gate_text) is None,
        f"{contract.path} legal gate contains a YAML bypass",
    )
    for forbidden in (
        "||",
        "; exit 0",
        "continue-on-error",
        "SilentlyContinue",
        "-ErrorAction Ignore",
        "2>$null",
        "--allow",
        "ALLOW_RELEASE",
    ):
        _require(forbidden not in gate_text, f"{contract.path} legal gate contains a bypass: {forbidden}")


def _validate_historical_bundle_commands() -> None:
    for contract in HISTORICAL_CONTRACTS:
        current_blob = subprocess.run(
            ["git", "rev-parse", f"HEAD:{contract.path}"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        ).stdout.strip()
        baseline_blob = subprocess.run(
            ["git", "rev-parse", f"{BASELINE_COMMIT}:{contract.path}"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        ).stdout.strip()
        _require(
            current_blob == baseline_blob,
            f"{contract.path} Git blob changed from the pre-R1 baseline",
        )
        worktree_diff = subprocess.run(
            ["git", "diff", "--exit-code", "--", contract.path],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        _require(worktree_diff.returncode == 0, f"{contract.path} has a working-tree change")

    with tempfile.TemporaryDirectory(prefix="s9h-historical-workflow-v2-") as temp:
        root = Path(temp)
        for index, contract in enumerate(CONTRACTS):
            fixture = bundle_smoke._release_fixture(
                root / f"fixture-{index}",
                contract.tag,
            )
            bundle_root = root / f"bundle-{index}"
            prerelease = contract.prerelease == "true"
            create_output = bundle_smoke._run_cli(
                "create",
                *bundle_smoke._v2_create_cli_arguments(
                    fixture,
                    bundle_root,
                    contract.tag,
                    prerelease,
                ),
            )
            _require(
                create_output == "Release bundle v2 created and verified",
                f"{contract.path} create command failed",
            )
            verify_output = bundle_smoke._run_cli(
                "verify",
                *bundle_smoke._v2_verify_cli_arguments(
                    bundle_root,
                    contract.tag,
                    prerelease,
                    require_ready=False,
                ),
            )
            _require(
                verify_output == "Release bundle v2 verified",
                f"{contract.path} verify command failed",
            )
            bundle_smoke._validate_v2_generated_files(
                bundle_root,
                contract.tag,
                prerelease,
            )
            publish_ready = bundle_smoke._run_cli_result(
                "verify",
                *bundle_smoke._v2_verify_cli_arguments(
                    bundle_root,
                    contract.tag,
                    prerelease,
                    require_ready=True,
                ),
            )
            _require(
                publish_ready.returncode == 1
                and "release bundle is not approved for publishing" in publish_ready.stderr,
                f"{contract.path} historical publish gate did not remain fail-closed",
            )


def _test_negative_mutations(documents: dict[str, str]) -> None:
    legacy = CONTRACTS[0]
    fixed = CONTRACTS[1]
    cases = (
        ("missing publish job", legacy.path, "  publish:\n", "  publish-disabled:\n"),
        ("build write permission", legacy.path, "  build:\n    permissions:\n      contents: read", "  build:\n    permissions:\n      contents: write"),
        ("publish read permission", legacy.path, "  publish:\n    needs: build\n    permissions:\n      contents: write", "  publish:\n    needs: build\n    permissions:\n      contents: read"),
        ("missing needs", legacy.path, "    needs: build\n", ""),
        ("release action in build", legacy.path, "      - name: Create and verify release bundle", "      - uses: softprops/action-gh-release@" + "0" * 40 + " # v2\n      - name: Create and verify release bundle"),
        ("artifact execution", legacy.path, "          Write-Host \"Release bundle v2 handoff verified\"", "          & release-bundle/assets/Youtube.Downloaderbs.exe\n          Write-Host \"Release bundle v2 handoff verified\""),
        ("missing legal payload step", legacy.path, "      - name: Prepare and verify release legal payload", "      - name: Removed legal payload step"),
        ("bundle v1", legacy.path, "s9h-release-bundle-v2", "s9h-release-bundle-v1"),
        (
            "real publish verifier replaced by previous comment-only pattern",
            legacy.path,
            "          python control/scripts/prepare_release_bundle.py verify `",
            "          $requireReleaseReady = $true # --require-release-ready true",
        ),
        (
            "publish verifier only in comment",
            legacy.path,
            "          python control/scripts/prepare_release_bundle.py verify `",
            "          # python control/scripts/prepare_release_bundle.py verify --require-release-ready true",
        ),
        (
            "publish verifier only in variable",
            legacy.path,
            "          python control/scripts/prepare_release_bundle.py verify `",
            '          $Verifier = "python control/scripts/prepare_release_bundle.py verify --require-release-ready true"',
        ),
        (
            "publish verifier only in documentation text",
            legacy.path,
            "          python control/scripts/prepare_release_bundle.py verify `",
            '          Write-Host "python control/scripts/prepare_release_bundle.py verify --require-release-ready true"',
        ),
        ("empty source placeholder", legacy.path, "      - name: Create and verify release bundle", "      - name: Create empty source placeholder\n        run: New-Item release/source-assets/empty.zip\n      - name: Create and verify release bundle"),
        ("upload compression", legacy.path, "          compression-level: 0", "          compression-level: 6"),
        ("missing merge-multiple", legacy.path, "          merge-multiple: true\n", ""),
        ("false merge-multiple", legacy.path, "          merge-multiple: true", "          merge-multiple: false"),
        ("quoted true merge-multiple", legacy.path, "          merge-multiple: true", '          merge-multiple: "true"'),
        ("download by name", legacy.path, "          artifact-ids: ${{ needs.build.outputs.artifact-id }}", "          name: release-bundle"),
        ("multiple artifact IDs", legacy.path, "          artifact-ids: ${{ needs.build.outputs.artifact-id }}", "          artifact-ids: ${{ needs.build.outputs.artifact-id }}, 123"),
        ("nested artifact destination", legacy.path, "          path: release-bundle", "          path: release-bundle/nested"),
        (
            "dynamic artifact-name discovery",
            legacy.path,
            '          $bundle = [IO.Path]::GetFullPath((Join-Path $workspace "release-bundle"))',
            '          $bundleRoot = [IO.Path]::GetFullPath((Join-Path $workspace "release-bundle"))\n'
            '          $bundle = @(Get-ChildItem -LiteralPath $bundleRoot -Directory)[0].FullName',
        ),
        ("missing digest output", legacy.path, "      artifact-digest: ${{ steps.upload-release-bundle.outputs.artifact-digest }}\n", ""),
        ("missing digest validation", legacy.path, "          if ($env:ARTIFACT_DIGEST -cnotmatch \"^[0-9a-f]{64}$\")", "          if ($env:ARTIFACT_DIGEST -eq \"\")"),
        ("notes outside bundle", legacy.path, "          body_path: release-bundle/RELEASE_NOTES.md", "          body_path: release/RELEASE_NOTES.md"),
        ("missing checksum asset", legacy.path, "            release-bundle/assets/SHA256SUMS.txt\n", ""),
        ("missing manifest asset", legacy.path, "            release-bundle/RELEASE_MANIFEST.json\n", ""),
        ("missing legal publish asset", legacy.path, f"            release-bundle/assets/{legacy.legal_name}\n", ""),
        ("missing aria2 source publish asset", legacy.path, f"            release-bundle/assets/{legacy.aria2_source_name}\n", ""),
        ("missing FFmpeg source publish asset", legacy.path, f"            release-bundle/assets/{legacy.ffmpeg_source_name}\n", ""),
        ("unmatched files allowed", legacy.path, "          fail_on_unmatched_files: true", "          fail_on_unmatched_files: false"),
        ("absence not final", legacy.path, "      - name: Publish GitHub prerelease", "      - name: Extra publish step\n        shell: pwsh\n        run: Write-Host extra\n      - name: Publish GitHub prerelease"),
        ("automatic trigger", legacy.path, "on:\n  workflow_dispatch:", "on:\n  push:\n    branches:\n      - main"),
        ("fixed tag changed", fixed.path, f"          ref: {fixed.tag}", "          ref: main"),
        ("legacy target changed", legacy.path, "          target_commitish: ${{ github.sha }}", "          target_commitish: main"),
        ("release title changed", fixed.path, f"          name: {fixed.title}", "          name: Changed title"),
    )
    for label, path, old, new in cases:
        mutated = dict(documents)
        mutated[path] = _replace_once(mutated[path], old, new)
        try:
            validate_contracts(mutated)
        except WorkflowSplitError:
            continue
        raise WorkflowSplitError(f"negative mutation was accepted: {label}")

    contract = CONTRACTS[1]
    gate_block = _named_step_text(documents[contract.path], "Enforce fail-closed release legal gate")
    extra = []
    extra.append(("remove legal gate", documents[contract.path].replace(gate_block, "", 1)))
    extra.append(
        (
            "legal gate after installer",
            _move_named_step_after(
                documents[contract.path],
                "Enforce fail-closed release legal gate",
                "Install locked build dependencies",
            ),
        )
    )
    extra.append(
        (
            "legal gate after build",
            _move_named_step_after(
                documents[contract.path],
                "Enforce fail-closed release legal gate",
                contract.build_step,
            ),
        )
    )
    extra.append(
        (
            "legal payload before build",
            _move_named_step_after(
                documents[contract.path],
                contract.build_step,
                "Prepare and verify release legal payload",
            ),
        )
    )
    for label, old, new in (
        ("wrong gate tag", f"--tag {contract.tag}", "--tag v1.3.1"),
        ("wrong policy path", "--policy control/legal/release-policy.json", "--policy source/legal/release-policy.json"),
        (
            "gate continue-on-error",
            "      - name: Enforce fail-closed release legal gate\n",
            "      - name: Enforce fail-closed release legal gate\n        continue-on-error: true\n",
        ),
        (
            "gate if false",
            "      - name: Enforce fail-closed release legal gate\n",
            "      - name: Enforce fail-closed release legal gate\n        if: false\n",
        ),
        (
            "gate environment bypass",
            "      - name: Enforce fail-closed release legal gate\n",
            "      - name: Enforce fail-closed release legal gate\n        env:\n          ALLOW_RELEASE: 1\n",
        ),
        ("gate shell suppression", f"--tag {contract.tag}", f"--tag {contract.tag} || exit 0"),
        (
            "gate PowerShell suppression",
            "        run: |\n          python control/scripts/verify_release_legal_gate.py",
            "        run: |\n          $ErrorActionPreference = \"SilentlyContinue\"\n          python control/scripts/verify_release_legal_gate.py",
        ),
        ("gate allow argument", f"--tag {contract.tag}", f"--tag {contract.tag} `\n            --allow"),
    ):
        extra.append((label, _replace_once(documents[contract.path], old, new)))
    extra.append(
        (
            "gate only in publish",
            _move_named_step_after(
                documents[contract.path],
                "Enforce fail-closed release legal gate",
                "Validate immutable build outputs",
            ),
        )
    )
    for label, workflow in extra:
        mutated = dict(documents)
        mutated[contract.path] = workflow
        try:
            validate_contracts(mutated)
        except WorkflowSplitError:
            continue
        raise WorkflowSplitError(f"negative mutation was accepted: {label}")


def _named_step_text(workflow: str, name: str) -> str:
    pattern = re.compile(rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)")
    match = pattern.search(_normalize_newlines(workflow))
    _require(match is not None, f"step text is missing: {name}")
    return match.group(0)


def _move_named_step_after(workflow: str, name: str, target: str) -> str:
    normalized = _normalize_newlines(workflow)
    block = _named_step_text(normalized, name)
    without = normalized.replace(block, "", 1)
    target_block = _named_step_text(without, target)
    return without.replace(target_block, target_block + block, 1)


def _mapping_block(lines: list[str], key: str, indent: int) -> list[str]:
    prefix = " " * indent + key + ":"
    for index, line in enumerate(lines):
        if line == prefix or line.startswith(prefix + " "):
            result = [line]
            for candidate in lines[index + 1 :]:
                if candidate.strip() and _indent(candidate) <= indent:
                    break
                result.append(candidate)
            return result
    return []


def _direct_mapping_keys(lines: list[str], indent: int) -> list[str]:
    keys = []
    for line in lines:
        if _indent(line) != indent:
            continue
        match = re.fullmatch(r"([^:#]+):(?:\s.*)?", line.strip())
        if match:
            keys.append(match.group(1))
    return keys


def _direct_mapping_pairs(lines: list[str], indent: int) -> list[tuple[str, str]]:
    pairs = []
    for line in lines:
        if _indent(line) != indent:
            continue
        match = re.fullmatch(r"([^:#]+):\s*(.*?)\s*", line.strip())
        if match and match.group(2):
            pairs.append((match.group(1), _unquote(match.group(2))))
    return pairs


def _scalar_value(lines: list[str], key: str, indent: int) -> str | None:
    prefix = " " * indent + key + ":"
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            return _unquote(value) if value else None
    return None


def _raw_scalar_value(lines: list[str], key: str, indent: int) -> str | None:
    prefix = " " * indent + key + ":"
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            return value if value else None
    return None


def _step_blocks(job: list[str]) -> list[list[str]]:
    steps = []
    indexes = [index for index, line in enumerate(job) if re.match(r"^      - (?:name|uses):", line)]
    for position, start in enumerate(indexes):
        stop = indexes[position + 1] if position + 1 < len(indexes) else len(job)
        steps.append(job[start:stop])
    return steps


def _named_step(steps: list[list[str]], name: str) -> list[str]:
    prefix = f"      - name: {name}"
    matches = [step for step in steps if step and step[0] == prefix]
    _require(len(matches) == 1, f"step count is invalid for {name}")
    return matches[0]


def _action_steps(steps: list[list[str]], action: str) -> list[list[str]]:
    return [step for step in steps if any(f"uses: {action}@" in line for line in step)]


def _single_action_step(steps: list[list[str]], action: str) -> list[str]:
    matches = _action_steps(steps, action)
    _require(len(matches) == 1, f"action count is invalid for {action}")
    ref_lines = [line for line in matches[0] if f"uses: {action}@" in line]
    _require(len(ref_lines) == 1, f"action ref count is invalid for {action}")
    ref = ref_lines[0].split("@", 1)[1].split()[0]
    _require(FULL_SHA.fullmatch(ref) is not None, f"{action} must use an immutable SHA")
    return matches[0]


def _sequence_block(lines: list[str], key: str, indent: int) -> list[str]:
    prefix = " " * indent + key + ": |"
    for index, line in enumerate(lines):
        if line == prefix:
            values = []
            for candidate in lines[index + 1 :]:
                if candidate.strip() and _indent(candidate) <= indent:
                    break
                if candidate.strip():
                    values.append(candidate.strip())
            return values
    return []


def _replace_once(value: str, old: str, new: str) -> str:
    _require(old in value, f"mutation target is missing: {old}")
    return value.replace(old, new, 1)


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _indent(value: str) -> int:
    return len(value) - len(value.lstrip(" "))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowSplitError(message)


if __name__ == "__main__":
    raise SystemExit(main())
