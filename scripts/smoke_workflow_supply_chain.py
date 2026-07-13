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
    "actions/checkout": ("v4", 5),
    "actions/setup-python": ("v5", 5),
    "actions/upload-artifact": ("v4", 4),
    "softprops/action-gh-release": ("v2", 4),
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
    _require(sum(counts.values()) == 18, "total immutable action count must be 18")
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
    _require(runners == ["windows-2022"], f"{path} runner must be windows-2022")

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
        return

    release_job = _mapping_block(lines, "release", 2)
    job_permissions = _mapping_block(release_job, "permissions", 4)
    _require(
        _direct_mapping_pairs(job_permissions, 6) == [("contents", "write")],
        f"{path} release job must have contents write only",
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
    _require(
        "python -m pip install --upgrade pip pyinstaller" in workflow,
        f"{path} Phase 4B install line changed early",
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
        "actions/checkout@",
        "unknown/checkout@",
    )
    mutations.append(("unknown action owner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        f"actions/checkout@{checkout_sha}",
        "actions/checkout@" + "a" * 40,
    )
    mutations.append(("inconsistent action SHA", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "runs-on: windows-2022",
        "runs-on: windows-latest",
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
    mutated[ci] = _replace_once(mutated[ci], "contents: read", "contents: write")
    mutations.append(("top-level contents write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n",
        "",
    )
    mutations.append(("missing release job write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "  windows-smoke:\n",
        "  windows-smoke:\n    permissions:\n      contents: write\n",
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
