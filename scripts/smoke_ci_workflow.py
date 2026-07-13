import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
IMMUTABLE_ACTION_REF = re.compile(r"[0-9a-f]{40}\Z")


class WorkflowContractError(AssertionError):
    pass


def main() -> int:
    _require(WORKFLOW_PATH.is_file(), "CI workflow file is missing")
    workflow = _normalize_newlines(WORKFLOW_PATH.read_text(encoding="utf-8"))
    validate_workflow(workflow)
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

    job = _mapping_block(lines, "windows-smoke", 2)
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
        steps.index(smoke_step) < steps.index(installer_step),
        "tracked smoke suite must run before build dependency installation",
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

    _require("continue-on-error" not in code, "continue-on-error must be absent")
    _forbid(
        code,
        r"(?im)^\s*(?:run:\s*)?(?:python\s+-m\s+)?pip\s+(?:install|upgrade)\b",
        "pip install or upgrade must be absent",
    )
    _forbid(code, r"(?i)\bPyInstaller\b", "PyInstaller invocation must be absent")
    _forbid(code, r"(?i)upload-artifact", "artifact upload must be absent")
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
            _replace_once(workflow, "contents: read", "contents: write"),
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
                "    runs-on: windows-2022\n",
                "    runs-on: windows-2022\n    continue-on-error: true\n",
            ),
            "continue-on-error",
        ),
        (
            "artifact upload",
            workflow
            + "\n      - name: Unsafe artifact upload\n"
            + "        uses: actions/upload-artifact@v4\n",
            "artifact upload",
        ),
        (
            "pip install",
            workflow
            + "\n      - name: Unsafe dependency install\n"
            + "        run: python -m pip install pyinstaller\n",
            "pip install",
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
            "installer before smoke suite",
            _move_named_step_before(
                workflow,
                "Validate locked build dependencies",
                "Run tracked smoke suite",
            ),
            "before build dependency installation",
        ),
        (
            "missing installer step",
            _remove_named_step(workflow, "Validate locked build dependencies"),
            "Validate locked build dependencies",
        ),
    )
    for label, mutated, expected in mutations:
        _expect_contract_failure(label, mutated, expected)


def _test_immutable_action_policy(workflow: str) -> None:
    checkout_ref = _workflow_action_ref(workflow, "actions/checkout")
    setup_ref = _workflow_action_ref(workflow, "actions/setup-python")
    generic_pins = _replace_action_ref(
        _replace_action_ref(workflow, "actions/checkout", "a" * 40),
        "actions/setup-python",
        "b" * 40,
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
            _replace_once(workflow, "runs-on: windows-2022", "runs-on: windows-latest"),
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
                "  windows-smoke:\n",
                "  windows-smoke:\n    permissions:\n      contents: write\n",
            ),
        ),
    )
    for label, mutated in mutations:
        _expect_contract_failure(label, mutated, "")

    _require(checkout_ref != setup_ref, "action refs unexpectedly share one commit")


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
