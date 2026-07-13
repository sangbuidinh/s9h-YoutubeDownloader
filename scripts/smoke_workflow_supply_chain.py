import copy
import datetime
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
PIN_INVENTORY_PATH = REPO_ROOT / ".github" / "actions-pins.json"

EXPECTED_WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/prerelease-v1.2.7-rc.1.yml",
    ".github/workflows/prerelease-v1.3.0-rc.1.yml",
    ".github/workflows/release-v1.3.0.yml",
    ".github/workflows/release-v1.3.1.yml",
)
CI_WORKFLOW = ".github/workflows/ci.yml"
FORBIDDEN_VERSION_WORKFLOW_TRIGGERS = {
    "branch_protection_rule",
    "pull_request",
    "pull_request_target",
    "push",
    "release",
    "repository_dispatch",
    "schedule",
    "workflow_run",
}
ACTION_POLICY = {
    "actions/checkout": ("v4", 8),
    "actions/setup-python": ("v5", 5),
    "actions/upload-artifact": ("v4", 5),
    "actions/download-artifact": ("v4", 5),
    "softprops/action-gh-release": ("v2", 4),
}
LEGACY_WORKFLOW = ".github/workflows/prerelease-v1.2.7-rc.1.yml"
FIXED_TAG_WORKFLOWS = {
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": "v1.3.0-rc.1",
    ".github/workflows/release-v1.3.0.yml": "v1.3.0",
    ".github/workflows/release-v1.3.1.yml": "v1.3.1",
}
RELEASE_POLICY = {
    ".github/workflows/prerelease-v1.2.7-rc.1.yml": (
        "v1.2.7-rc.1",
        r".\scripts\build_prerelease_v1_2_7_rc1.ps1",
    ),
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": (
        "v1.3.0-rc.1",
        r".\scripts\build_prerelease_v1_3_0_rc1.ps1 -PreparePinnedRuntime",
    ),
    ".github/workflows/release-v1.3.0.yml": (
        "v1.3.0",
        r".\scripts\build_release_v1_3_0.ps1 -PreparePinnedRuntime",
    ),
    ".github/workflows/release-v1.3.1.yml": (
        "v1.3.1",
        r".\scripts\build_release_v1_3_1.ps1 -PreparePinnedRuntime",
    ),
}
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
ACTION_LINE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([^@\s#]+)@([^\s#]+)\s+#\s*(v\d+)\s*$"
)
PERMISSION_LINE = re.compile(
    r"^(\s*)(contents|actions|checks|packages|pull-requests|"
    r"id-token|issues|deployments)\s*:\s*(read|write)\s*$"
)


class SupplyChainContractError(AssertionError):
    pass


def main() -> int:
    documents = _load_workflows()
    inventory = _load_inventory()
    validate_supply_chain(documents, inventory)
    _test_negative_mutations(documents, inventory)
    print("workflow supply-chain smoke tests passed")
    return 0


def validate_supply_chain(documents: dict[str, str], inventory: dict) -> None:
    _require(
        tuple(sorted(documents)) == EXPECTED_WORKFLOWS,
        "workflow file inventory differs from the expected five files",
    )
    pins = _validate_inventory(inventory)

    uses_by_action: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path, workflow in documents.items():
        workflow = _normalize_newlines(workflow)
        _validate_trigger_policy(path, workflow)
        _validate_workflow_environment(path, workflow)
        _validate_permissions(path, workflow)
        _validate_historical_behavior(path, workflow)
        _validate_action_placement(path, workflow)
        _verify_no_sensitive_literals(path, workflow)

        uses_lines = [
            (number, line)
            for number, line in enumerate(workflow.splitlines(), 1)
            if re.match(r"^\s*(?:-\s*)?uses\s*:", line)
        ]
        _require(bool(uses_lines), f"{path} contains no action invocation")
        for number, line in uses_lines:
            stripped = line.strip()
            _require(
                not stripped.startswith(("uses: ./", "- uses: ./")),
                f"{path}:{number} local action is forbidden",
            )
            _require(
                "docker://" not in stripped.casefold(),
                f"{path}:{number} Docker action is forbidden",
            )
            _require(
                "/.github/workflows/" not in stripped,
                f"{path}:{number} reusable workflow call is forbidden",
            )
            match = ACTION_LINE.fullmatch(line)
            _require(
                match is not None,
                f"{path}:{number} action must use full SHA and version comment",
            )
            repository, commit, comment = match.groups()
            _require(repository in ACTION_POLICY, f"{path}:{number} action is not allowlisted")
            expected_tag, _ = ACTION_POLICY[repository]
            _require(
                FULL_SHA.fullmatch(commit) is not None,
                f"{path}:{number} action ref must be lowercase 40-character SHA",
            )
            _require(
                comment == expected_tag,
                f"{path}:{number} action version comment is incorrect",
            )
            _require(
                commit == pins[repository],
                f"{path}:{number} action ref differs from pin inventory",
            )
            uses_by_action[repository].append((path, commit, comment))

    counts = Counter({name: len(rows) for name, rows in uses_by_action.items()})
    expected_counts = Counter({name: policy[1] for name, policy in ACTION_POLICY.items()})
    _require(counts == expected_counts, f"action occurrence counts differ: {counts}")
    _require(sum(counts.values()) == 27, "total immutable action count must be 27")
    for repository, rows in uses_by_action.items():
        _require(
            len({commit for _, commit, _ in rows}) == 1,
            f"{repository} uses inconsistent SHAs across workflows",
        )


def _load_workflows() -> dict[str, str]:
    paths = sorted(WORKFLOW_DIR.glob("*.yml"))
    return {
        path.relative_to(REPO_ROOT).as_posix(): _normalize_newlines(
            path.read_text(encoding="utf-8")
        )
        for path in paths
    }


def _load_inventory() -> dict:
    _require(PIN_INVENTORY_PATH.is_file(), "action pin inventory is missing")
    try:
        return json.loads(PIN_INVENTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SupplyChainContractError(f"action pin inventory is invalid JSON: {exc}") from exc


def _validate_inventory(inventory: dict) -> dict[str, str]:
    _require(inventory.get("schema_version") == 1, "pin inventory schema must be 1")
    resolved = inventory.get("resolved_at_utc")
    _require(isinstance(resolved, str) and resolved.endswith("Z"), "resolution time is invalid")
    try:
        datetime.datetime.fromisoformat(resolved.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise SupplyChainContractError("resolution time is not ISO-8601 UTC") from exc

    actions = inventory.get("actions")
    _require(isinstance(actions, dict), "pin inventory actions mapping is missing")
    _require(set(actions) == set(ACTION_POLICY), "pin inventory action set differs")
    pins = {}
    for repository, (expected_tag, _) in ACTION_POLICY.items():
        entry = actions[repository]
        _require(
            set(entry) == {"tag", "commit"},
            f"{repository} pin entry has unexpected fields",
        )
        _require(entry["tag"] == expected_tag, f"{repository} tag is incorrect")
        _require(
            isinstance(entry["commit"], str)
            and FULL_SHA.fullmatch(entry["commit"]) is not None,
            f"{repository} commit must be lowercase 40-character SHA",
        )
        pins[repository] = entry["commit"]
    return pins


def _validate_workflow_environment(path: str, workflow: str) -> None:
    runners = re.findall(r"(?m)^\s*runs-on:\s*(\S+)\s*$", workflow)
    _require(
        runners == ["windows-2022", "windows-2022"],
        f"{path} jobs must use windows-2022",
    )

    versions = re.findall(r'(?m)^\s*python-version:\s*"?([^"\s]+)"?\s*$', workflow)
    _require(versions == ["3.11.9"], f"{path} Python selector must be 3.11.9")
    for required in (
        "python --version",
        '$VersionOutput = (& python --version 2>&1 | Out-String).Trim()',
        'if ($VersionOutput -ne "Python 3.11.9")',
        'Write-Host "Pinned Python verified: $VersionOutput"',
    ):
        _require(required in workflow, f"{path} exact Python verification is missing")


def _validate_trigger_policy(path: str, workflow: str) -> None:
    lines = workflow.splitlines()
    trigger_block = _mapping_block(lines, "on", 0)
    event_keys = _direct_mapping_keys(trigger_block, 2)

    if path == CI_WORKFLOW:
        _require(
            event_keys == ["pull_request", "push"],
            "CI direct triggers must be pull_request and push only",
        )
        for event in event_keys:
            event_block = _mapping_block(trigger_block, event, 2)
            _require(
                _direct_mapping_keys(event_block, 4) == ["branches"],
                f"CI {event} must define only a branches filter",
            )
            branches_block = _mapping_block(event_block, "branches", 4)
            _require(
                _direct_sequence_values(branches_block, 6) == ["main"],
                f"CI {event} must target main only",
            )
        return

    forbidden = FORBIDDEN_VERSION_WORKFLOW_TRIGGERS.intersection(event_keys)
    _require(
        not forbidden,
        f"{path} contains automatic trigger(s): {sorted(forbidden)}",
    )
    _require(
        event_keys == ["workflow_dispatch"],
        f"{path} direct trigger must be workflow_dispatch only",
    )


def _validate_permissions(path: str, workflow: str) -> None:
    lines = workflow.splitlines()
    top = _mapping_block(lines, "permissions", 0)
    _require(
        _direct_mapping_pairs(top, 2) == [("contents", "read")],
        f"{path} top-level permissions must be contents read only",
    )
    writes = []
    for number, line in enumerate(lines, 1):
        match = PERMISSION_LINE.fullmatch(line)
        if match and match.group(3) == "write":
            writes.append((number, len(match.group(1)), match.group(2)))

    if path == ".github/workflows/ci.yml":
        _require(not writes, "CI must not contain job-level write permission")
        for job_name in ("windows-smoke", "release-bundle-handoff"):
            job = _mapping_block(lines, job_name, 2)
            _require(
                _direct_mapping_pairs(_mapping_block(job, "permissions", 4), 6)
                == [("contents", "read")],
                f"CI {job_name} job must have contents read only",
            )
        return

    build_job = _mapping_block(lines, "build", 2)
    _require(
        _direct_mapping_pairs(_mapping_block(build_job, "permissions", 4), 6)
        == [("contents", "read")],
        f"{path} build job must have contents read only",
    )
    publish_job = _mapping_block(lines, "publish", 2)
    job_permissions = _mapping_block(publish_job, "permissions", 4)
    _require(
        _direct_mapping_pairs(job_permissions, 6) == [("contents", "write")],
        f"{path} publish job must have contents write only",
    )
    _require(
        len(writes) == 1 and writes[0][1:] == (6, "contents"),
        f"{path} contains unexpected write permissions",
    )


def _validate_historical_behavior(path: str, workflow: str) -> None:
    if path == ".github/workflows/ci.yml":
        _require(
            "softprops/action-gh-release@" not in workflow,
            "CI must not contain a publishing action",
        )
        return
    tag, build_command = RELEASE_POLICY[path]
    _require(tag in workflow, f"{path} historical tag is missing")
    _require(build_command in workflow, f"{path} build command changed")
    _require(
        "softprops/action-gh-release@" in workflow,
        f"{path} publishing action is missing",
    )
    _validate_dependency_install(path, workflow, tag, build_command)


def _validate_action_placement(path: str, workflow: str) -> None:
    lines = workflow.splitlines()
    if path == CI_WORKFLOW:
        producer = "\n".join(_mapping_block(lines, "windows-smoke", 2))
        consumer = "\n".join(_mapping_block(lines, "release-bundle-handoff", 2))
        _require(producer.count("actions/upload-artifact@") == 1, "CI producer upload action count")
        _require("actions/download-artifact@" not in producer, "CI producer must not download")
        _require(consumer.count("actions/download-artifact@") == 1, "CI consumer download action count")
        for forbidden in (
            "actions/checkout@",
            "actions/setup-python@",
            "actions/upload-artifact@",
            "softprops/action-gh-release@",
        ):
            _require(forbidden not in consumer, f"CI consumer contains forbidden action: {forbidden}")
        _validate_download_inputs(consumer, "${{ needs.windows-smoke.outputs.artifact-id }}", "CI consumer")
        return

    build = "\n".join(_mapping_block(lines, "build", 2))
    publish = "\n".join(_mapping_block(lines, "publish", 2))
    _require(build.count("actions/upload-artifact@") == 1, f"{path} build upload action count")
    _require("actions/download-artifact@" not in build, f"{path} build must not download")
    _require("softprops/action-gh-release@" not in build, f"{path} build must not publish")
    _require(publish.count("actions/download-artifact@") == 1, f"{path} publish download action count")
    _require(publish.count("softprops/action-gh-release@") == 1, f"{path} publish action count")
    for forbidden in ("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@"):
        _require(forbidden not in publish, f"{path} publish contains forbidden action: {forbidden}")
    _validate_download_inputs(publish, "${{ needs.build.outputs.artifact-id }}", path)


def _validate_download_inputs(job: str, artifact_id: str, label: str) -> None:
    _require(f"artifact-ids: {artifact_id}" in job, f"{label} must download by artifact ID")
    for forbidden in ("pattern:", "github-token:", "repository:", "run-id:", "merge-multiple:"):
        _require(forbidden not in job, f"{label} contains forbidden download input: {forbidden}")


def _validate_dependency_install(
    path: str,
    workflow: str,
    tag: str,
    build_command: str,
) -> None:
    _require(
        re.search(
            r"(?im)^\s*(?:run:\s*)?(?:python\s+-m\s+)?pip\s+install\b",
            workflow,
        )
        is None,
        f"{path} must not contain a direct pip install",
    )
    for forbidden in (
        "--extra-index-url",
        "--trusted-host",
        "--pre",
        "--no-binary",
        "--only-binary=:none:",
    ):
        _require(
            re.search(rf"(?m)(?:^|\s){re.escape(forbidden)}(?:\s|=|$)", workflow)
            is None,
            f"{path} contains unsafe installer input",
        )

    job = _mapping_block(workflow.splitlines(), "build", 2)
    steps = _step_blocks(job)
    installer = _named_step(steps, "Install locked build dependencies")
    installer_text = "\n".join(installer)
    installer_command = (
        "python scripts/install_build_dependencies.py"
        if path == LEGACY_WORKFLOW
        else "python control/scripts/install_build_dependencies.py"
    )
    for required in (
        installer_command,
        '$env:RUNNER_TEMP\\s9h-build-venv-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT',
        '--github-env "$env:GITHUB_ENV"',
        '--github-path "$env:GITHUB_PATH"',
    ):
        _require(required in installer_text, f"{path} installer is missing: {required}")
    _require(
        "\n".join(job).count(installer_command) == 1,
        f"{path} must invoke the locked installer exactly once",
    )

    setup_steps = _action_steps(steps, "actions/setup-python")
    checkout_steps = _action_steps(steps, "actions/checkout")
    _require(len(setup_steps) == 1, f"{path} must set up Python exactly once")
    build_steps = [step for step in steps if build_command in "\n".join(step)]
    _require(len(build_steps) == 1, f"{path} build step is ambiguous")
    _require(
        steps.index(setup_steps[0]) < steps.index(installer) < steps.index(build_steps[0]),
        f"{path} installer must run after setup-python and before build",
    )

    if path == LEGACY_WORKFLOW:
        _require(len(checkout_steps) == 1, "legacy workflow must use one source checkout")
        _require(
            _step_name(checkout_steps[0]) == "Check out source",
            "legacy workflow must retain current-source build semantics",
        )
        _require(
            _scalar_value(checkout_steps[0], "ref", 10) is None,
            "legacy source checkout must not select a historical tag",
        )
        return

    _require(path in FIXED_TAG_WORKFLOWS, f"unexpected version workflow: {path}")
    _require(len(checkout_steps) == 2, f"{path} must use two checkout steps")
    lock_checkout, tag_checkout = checkout_steps
    _require(
        _step_name(lock_checkout) == "Check out workflow control source",
        f"{path} control-source checkout is missing",
    )
    _require(
        _scalar_value(lock_checkout, "ref", 10) is None,
        f"{path} control-source checkout must use dispatch source",
    )
    _require(
        _unquote(_scalar_value(lock_checkout, "path", 10) or "") == "control",
        f"{path} control-source checkout path changed",
    )
    _require(
        _step_name(tag_checkout) == "Check out release tag",
        f"{path} release-tag checkout is missing",
    )
    _require(
        _unquote(_scalar_value(tag_checkout, "ref", 10) or "") == tag,
        f"{path} release-tag checkout ref changed",
    )
    _require(
        _unquote(_scalar_value(tag_checkout, "fetch-depth", 10) or "") == "0",
        f"{path} release-tag checkout must use fetch-depth 0",
    )
    _require(
        _unquote(_scalar_value(tag_checkout, "path", 10) or "") == "source",
        f"{path} release-tag checkout path changed",
    )
    verification = _named_step(steps, "Verify annotated tag and release absence")
    canonical_temp = _named_step(steps, "Configure canonical Windows temp path")
    _require(
        steps.index(lock_checkout)
        < steps.index(setup_steps[0])
        < steps.index(installer)
        < steps.index(tag_checkout)
        < steps.index(verification)
        < steps.index(canonical_temp)
        < steps.index(build_steps[0]),
        f"{path} fixed-tag dependency installation order is invalid",
    )


def _verify_no_sensitive_literals(path: str, workflow: str) -> None:
    patterns = (
        (r"AIza[0-9A-Za-z_-]{30,}", "YouTube API key"),
        (r"(?:ghp_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})", "GitHub token"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
        (r"(?im)^\s*(?:SID|SAPISID|HSID)\s*=", "cookie assignment"),
        (r"https?://[^\s]+googlevideo\.com[^\s]*", "signed media URL"),
        (r"(?i)[A-Z]:\\Users\\[^\\\r\n]+", "developer path"),
    )
    for pattern, label in patterns:
        _require(re.search(pattern, workflow) is None, f"{path} contains {label}")


def _test_negative_mutations(documents: dict[str, str], inventory: dict) -> None:
    ci = CI_WORKFLOW
    release = ".github/workflows/release-v1.3.1.yml"
    checkout_sha = inventory["actions"]["actions/checkout"]["commit"]
    setup_sha = inventory["actions"]["actions/setup-python"]["commit"]
    upload_sha = inventory["actions"]["actions/upload-artifact"]["commit"]
    download_sha = inventory["actions"]["actions/download-artifact"]["commit"]
    release_sha = inventory["actions"]["softprops/action-gh-release"]["commit"]

    mutations = []

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{checkout_sha}",
        "actions/checkout@v4",
    )
    mutations.append(("action major tag", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{checkout_sha}",
        "actions/checkout@" + checkout_sha[:7],
    )
    mutations.append(("short action SHA", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/download-artifact@{download_sha}",
        "actions/download-artifact@v4",
    )
    mutations.append(("mutable download action", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "actions/download-artifact@",
        "unknown/download-artifact@",
    )
    mutations.append(("unknown download action owner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "actions/checkout@",
        "unknown/checkout@",
    )
    mutations.append(("unknown action owner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Check out workflow control source\n"
        f"        uses: actions/checkout@{checkout_sha} # v4\n",
        "      - name: Check out workflow control source\n"
        f"        uses: actions/checkout@{'a' * 40} # v4\n",
    )
    mutations.append(("inconsistent action SHA", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "    runs-on: windows-2022\n    timeout-minutes: 30",
        "    runs-on: windows-latest\n    timeout-minutes: 30",
    )
    mutations.append(("mutable runner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        'python-version: "3.11.9"',
        'python-version: "3.11"',
    )
    mutations.append(("broad Python selector", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "permissions:\n  contents: read",
        "permissions:\n  contents: write",
    )
    mutations.append(("top-level contents write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n",
        "",
    )
    mutations.append(("missing publish job write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "  windows-smoke:\n    name: Windows compile, preflight and smoke\n    permissions:\n      contents: read",
        "  windows-smoke:\n    name: Windows compile, preflight and smoke\n    permissions:\n      contents: write",
    )
    mutations.append(("CI job write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n",
        "    permissions:\n      contents: write\n      id-token: write\n",
    )
    mutations.append(("id-token write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"uses: actions/checkout@{checkout_sha} # v4",
        "uses: ./.github/actions/unsafe",
    )
    mutations.append(("local action", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{checkout_sha}",
        f"owner/repo/.github/workflows/reuse.yml@{checkout_sha}",
    )
    mutations.append(("reusable workflow", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/setup-python@{setup_sha} # v5",
        "docker://example.invalid/tool:latest",
    )
    mutations.append(("Docker action", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] += (
        "\n      - name: Legacy direct dependency install\n"
        "        run: python -m pip install --upgrade pip pyinstaller\n"
    )
    mutations.append(("legacy direct pip install", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] += (
        "\n      - name: Unpinned dependency install\n"
        "        run: python -m pip install pyinstaller\n"
    )
    mutations.append(("unpinned direct pip install", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Install locked build dependencies",
        "Build and validate checksum-pinned assets",
    )
    mutations.append(("installer after build", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Install locked build dependencies",
        "Check out release tag",
    )
    mutations.append(("installer after fixed-tag checkout", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _remove_named_step(
        mutated[release],
        "Check out workflow control source",
    )
    mutations.append(("missing control-source checkout", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: read\n    runs-on: windows-2022",
        "    permissions:\n      contents: write\n    runs-on: windows-2022",
    )
    mutations.append(("build job contents write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n    runs-on: windows-2022",
        "    permissions:\n      contents: write\n      packages: write\n    runs-on: windows-2022",
    )
    mutations.append(("publish job packages write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Create and verify release bundle",
        f"      - uses: softprops/action-gh-release@{release_sha} # v2\n"
        "      - name: Create and verify release bundle",
    )
    mutations.append(("release action in build job", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Validate immutable build outputs",
        f"      - uses: actions/upload-artifact@{upload_sha} # v4\n"
        "      - name: Validate immutable build outputs",
    )
    mutations.append(("upload action in publish job", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Create and verify release bundle",
        f"      - uses: actions/download-artifact@{download_sha} # v4\n"
        "      - name: Create and verify release bundle",
    )
    mutations.append(("download action in build job", mutated))

    for label, injected in (
        ("download from another repository", "          repository: other/repository\n"),
        ("download from another run", "          run-id: 123\n"),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(
            mutated[release],
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n" + injected,
        )
        mutations.append((label, mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "          ref: v1.3.1\n",
        "          ref: v1.3.0\n",
    )
    mutations.append(("modified release tag ref", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        '$env:RUNNER_TEMP\\s9h-build-venv-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT',
        ".build-venv",
    )
    mutations.append(("repository-contained build venv", mutated))

    for label, unsafe_argument in (
        ("alternate package index", "--extra-index-url https://example.invalid/simple"),
        ("trusted host", "--trusted-host example.invalid"),
        ("source distribution allowance", "--no-binary=:all:"),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(
            mutated[release],
            '            --github-path "$env:GITHUB_PATH"\n',
            '            --github-path "$env:GITHUB_PATH" `\n'
            f"            {unsafe_argument}\n",
        )
        mutations.append((label, mutated))

    manual_trigger = "on:\n  workflow_dispatch:\n"
    trigger_mutations = (
        (
            "version workflow push trigger",
            "on:\n  workflow_dispatch:\n  push:\n    branches:\n      - main\n",
        ),
        (
            "version workflow self-path push trigger",
            "on:\n  workflow_dispatch:\n  push:\n    branches:\n      - main\n"
            "    paths:\n      - .github/workflows/release-v1.3.1.yml\n",
        ),
        (
            "version workflow schedule trigger",
            'on:\n  workflow_dispatch:\n  schedule:\n    - cron: "0 0 * * *"\n',
        ),
        (
            "version workflow pull request trigger",
            "on:\n  workflow_dispatch:\n  pull_request:\n",
        ),
        ("missing version workflow manual trigger", "on:\n"),
        (
            "version workflow pull request target trigger",
            "on:\n  workflow_dispatch:\n  pull_request_target:\n",
        ),
        (
            "version workflow inline pull request trigger",
            "on:\n  workflow_dispatch:\n  pull_request: {}\n",
        ),
    )
    for label, replacement in trigger_mutations:
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(mutated[release], manual_trigger, replacement)
        mutations.append((label, mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "  pull_request:\n", "")
    mutations.append(("CI missing pull request trigger", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "  push:\n", "")
    mutations.append(("CI missing push trigger", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "  pull_request:\n    branches:\n      - main\n",
        "  pull_request:\n    branches:\n      - develop\n",
    )
    mutations.append(("CI non-main branch filter", mutated))

    for label, mutated_documents in mutations:
        _expect_failure(label, mutated_documents, inventory)


def _expect_failure(label: str, documents: dict[str, str], inventory: dict) -> None:
    try:
        validate_supply_chain(documents, inventory)
    except SupplyChainContractError:
        return
    raise SupplyChainContractError(f"negative mutation was accepted: {label}")


def _mapping_block(lines: list[str], key: str, indent: int) -> list[str]:
    marker = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:\s*$")
    start = next((index for index, line in enumerate(lines) if marker.match(line)), None)
    _require(start is not None, f"mapping is missing: {key}")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= indent:
            break
        block.append(line)
    return block


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
    _require(bool(blocks), "release job contains no workflow steps")
    return blocks


def _step_name(step: list[str]) -> str | None:
    match = re.match(r"^\s*-\s+name:\s*(.*?)\s*$", step[0])
    return _unquote(match.group(1)) if match else None


def _named_step(steps: list[list[str]], name: str) -> list[str]:
    matches = [step for step in steps if _step_name(step) == name]
    _require(len(matches) == 1, f"workflow step must appear exactly once: {name}")
    return matches[0]


def _action_steps(steps: list[list[str]], action: str) -> list[list[str]]:
    pattern = re.compile(rf"\buses:\s*{re.escape(action)}@")
    return [step for step in steps if pattern.search("\n".join(step))]


def _scalar_value(lines: list[str], key: str, indent: int) -> str | None:
    pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:\s*(.*?)\s*$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def _direct_mapping_pairs(lines: list[str], indent: int) -> list[tuple[str, str]]:
    pattern = re.compile(rf"^{' ' * indent}([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$")
    return [
        (match.group(1), _unquote(match.group(2)))
        for line in lines
        if (match := pattern.match(line))
    ]


def _direct_mapping_keys(lines: list[str], indent: int) -> list[str]:
    pattern = re.compile(rf"^{' ' * indent}([A-Za-z0-9_-]+)\s*:.*$")
    return [match.group(1) for line in lines if (match := pattern.match(line))]


def _direct_sequence_values(lines: list[str], indent: int) -> list[str]:
    pattern = re.compile(rf"^{' ' * indent}-\s*(.*?)\s*$")
    return [
        _unquote(match.group(1))
        for line in lines
        if (match := pattern.match(line))
    ]


def _remove_named_step(workflow: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    mutated, count = pattern.subn("", _normalize_newlines(workflow), count=1)
    _require(count == 1, f"step mutation target count is {count}: {name}")
    return mutated


def _move_named_step_after(workflow: str, name: str, target: str) -> str:
    normalized = _normalize_newlines(workflow)
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    match = pattern.search(normalized)
    _require(match is not None, f"step mutation target is missing: {name}")
    block = match.group(0).rstrip("\n") + "\n"
    without = normalized[: match.start()] + normalized[match.end() :]
    target_pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(target)}\s*$\n.*?(?=^      - |\Z)"
    )
    target_match = target_pattern.search(without)
    _require(target_match is not None, f"step move target is missing: {target}")
    return without[: target_match.end()] + block + without[target_match.end() :]


def _replace_once(value: str, old: str, new: str) -> str:
    _require(value.count(old) == 1, f"mutation source is not unique: {old!r}")
    return value.replace(old, new, 1)


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _indent(value: str) -> int:
    return len(value) - len(value.lstrip(" "))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SupplyChainContractError(message)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
