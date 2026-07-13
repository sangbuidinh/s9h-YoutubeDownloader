import re
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


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
)


class WorkflowSplitError(AssertionError):
    pass


def main() -> int:
    documents = {
        contract.path: _normalize_newlines((REPO_ROOT / contract.path).read_text(encoding="utf-8"))
        for contract in CONTRACTS
    }
    validate_contracts(documents)
    _test_negative_mutations(documents)
    print("release workflow split smoke tests passed")
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
    installer = _named_step(build_steps, "Install locked build dependencies")
    build_step = _named_step(build_steps, contract.build_step)
    bundle_step = _named_step(build_steps, "Create and verify release bundle")
    upload = _single_action_step(build_steps, "actions/upload-artifact")
    _require(build_steps.index(installer) < build_steps.index(build_step), f"{contract.path} installer must precede build")
    _require(build_steps.index(build_step) < build_steps.index(bundle_step) < build_steps.index(upload), f"{contract.path} build/bundle/upload order is invalid")
    _require(contract.build_command in "\n".join(build_step), f"{contract.path} build command changed")
    _require("prepare_release_bundle.py create" in "\n".join(bundle_step), f"{contract.path} bundle create is missing")
    _require("prepare_release_bundle.py verify" in "\n".join(bundle_step), f"{contract.path} bundle verify is missing")
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

    download = _single_action_step(publish_steps, "actions/download-artifact")
    download_with = _direct_mapping_pairs(_mapping_block(download, "with", 8), 10)
    _require(
        download_with
        == [
            ("artifact-ids", "${{ needs.build.outputs.artifact-id }}"),
            ("path", "release-bundle"),
        ],
        f"{contract.path} download must use only the build artifact ID",
    )
    output_validation = "\n".join(_named_step(publish_steps, "Validate immutable build outputs"))
    for required in ("ARTIFACT_ID", "ARTIFACT_DIGEST", "SOURCE_COMMIT", "CONTROL_COMMIT", "^[0-9a-f]{64}$"):
        _require(required in output_validation, f"{contract.path} output validation is missing: {required}")

    verifier = _named_step(publish_steps, "Verify downloaded release bundle")
    verifier_text = "\n".join(verifier)
    for required in (
        "Release bundle handoff verified",
        "Get-FileHash",
        "ConvertFrom-Json",
        "s9h-release-bundle-v1",
        "SHA256SUMS.txt",
        "RELEASE_NOTES.md",
        "ReparsePoint",
        "${{ needs.build.outputs.source-commit }}",
        "${{ needs.build.outputs.control-commit }}",
    ):
        _require(required in verifier_text, f"{contract.path} inline verifier is missing: {required}")
    for forbidden in ("Invoke-Expression", "Start-Process", "&", "python ", ".ps1", ".cmd", ".bat"):
        _require(forbidden not in verifier_text, f"{contract.path} inline verifier may execute downloaded code: {forbidden}")

    absence = _named_step(publish_steps, "Confirm release absence immediately before publishing")
    release = _single_action_step(publish_steps, "softprops/action-gh-release")
    _require(publish_steps.index(absence) + 1 == publish_steps.index(release), f"{contract.path} final absence check must immediately precede release")
    absence_text = "\n".join(absence)
    _require(f"gh release view $tag" in absence_text and f'$tag = "{contract.tag}"' in absence_text, f"{contract.path} final release absence check changed")
    _require("GH_TOKEN: ${{ github.token }}" in absence_text, f"{contract.path} final release check token wiring changed")

    publish_text = "\n".join(publish)
    for forbidden in (
        "actions/checkout@",
        "actions/setup-python@",
        "actions/upload-artifact@",
        "Install locked build dependencies",
        "prepare_release_bundle.py",
        contract.build_command,
        "pip install",
        "python -m pip",
        "python ",
        "continue-on-error",
    ):
        _require(forbidden not in publish_text, f"{contract.path} publish contains forbidden behavior: {forbidden}")

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
        _require("working-directory: source" in "\n".join(build_step), f"{contract.path} build must execute from source")
        _require("python ..\\control\\scripts\\prepare_release_bundle.py" in "\n".join(bundle_step), f"{contract.path} bundle tool must execute from control source")
    else:
        _require(len(checkout_steps) == 1, f"{contract.path} must retain current-source checkout semantics")
        checkout_with = _mapping_block(checkout_steps[0], "with", 8)
        _require(not checkout_with, f"{contract.path} current-source checkout must not set ref or path")
        _require("target_commitish: ${{ github.sha }}" in "\n".join(release), f"{contract.path} target commit semantics changed")


def _test_negative_mutations(documents: dict[str, str]) -> None:
    legacy = CONTRACTS[0]
    fixed = CONTRACTS[1]
    cases = (
        ("missing publish job", legacy.path, "  publish:\n", "  publish-disabled:\n"),
        ("build write permission", legacy.path, "  build:\n    permissions:\n      contents: read", "  build:\n    permissions:\n      contents: write"),
        ("publish read permission", legacy.path, "  publish:\n    needs: build\n    permissions:\n      contents: write", "  publish:\n    needs: build\n    permissions:\n      contents: read"),
        ("missing needs", legacy.path, "    needs: build\n", ""),
        ("release action in build", legacy.path, "      - name: Create and verify release bundle", "      - uses: softprops/action-gh-release@" + "0" * 40 + " # v2\n      - name: Create and verify release bundle"),
        ("checkout in publish", legacy.path, "    steps:\n      - name: Validate immutable build outputs", "    steps:\n      - uses: actions/checkout@" + "0" * 40 + " # v4\n      - name: Validate immutable build outputs"),
        ("setup Python in publish", legacy.path, "    steps:\n      - name: Validate immutable build outputs", "    steps:\n      - uses: actions/setup-python@" + "0" * 40 + " # v5\n      - name: Validate immutable build outputs"),
        ("Python in publish", legacy.path, "      - name: Validate immutable build outputs\n        shell: pwsh", "      - name: Validate immutable build outputs\n        run: python downloaded.py\n        shell: pwsh"),
        ("artifact execution", legacy.path, "          Write-Host \"Release bundle handoff verified\"", "          & release-bundle/assets/Youtube.Downloaderbs.exe\n          Write-Host \"Release bundle handoff verified\""),
        ("upload compression", legacy.path, "          compression-level: 0", "          compression-level: 6"),
        ("download by name", legacy.path, "          artifact-ids: ${{ needs.build.outputs.artifact-id }}", "          name: release-bundle"),
        ("missing digest output", legacy.path, "      artifact-digest: ${{ steps.upload-release-bundle.outputs.artifact-digest }}\n", ""),
        ("missing digest validation", legacy.path, "          if ($env:ARTIFACT_DIGEST -cnotmatch \"^[0-9a-f]{64}$\")", "          if ($env:ARTIFACT_DIGEST -eq \"\")"),
        ("notes outside bundle", legacy.path, "          body_path: release-bundle/RELEASE_NOTES.md", "          body_path: release/RELEASE_NOTES.md"),
        ("missing checksum asset", legacy.path, "            release-bundle/assets/SHA256SUMS.txt\n", ""),
        ("missing manifest asset", legacy.path, "            release-bundle/RELEASE_MANIFEST.json\n", ""),
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
