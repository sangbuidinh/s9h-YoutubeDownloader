from __future__ import annotations

import copy
import contextlib
import datetime
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = REPO_ROOT / "requirements-build-bootstrap.txt"
BUILD_PATH = REPO_ROOT / "requirements-build.txt"
INVENTORY_PATH = REPO_ROOT / ".github" / "build-dependencies.json"
INSTALLER_PATH = REPO_ROOT / "scripts" / "install_build_dependencies.py"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_PATHS = (
    ".github/workflows/ci.yml",
    ".github/workflows/prerelease-v1.2.7-rc.1.yml",
    ".github/workflows/prerelease-v1.3.0-rc.1.yml",
    ".github/workflows/release-v1.3.0.yml",
    ".github/workflows/release-v1.3.1.yml",
)
LEGACY_WORKFLOW = ".github/workflows/prerelease-v1.2.7-rc.1.yml"
FIXED_TAG_WORKFLOWS = WORKFLOW_PATHS[2:]
OFFICIAL_INDEX = "https://pypi.org/simple"
HASH = re.compile(r"[0-9a-f]{64}\Z")
WHEEL = re.compile(r"[A-Za-z0-9_.]+-[^-]+-[^-]+-[^-]+-[^-]+\.whl\Z")


class BuildLockContractError(AssertionError):
    pass


def main() -> int:
    bootstrap = _read_lf_text(BOOTSTRAP_PATH)
    build = _read_lf_text(BUILD_PATH)
    inventory = json.loads(_read_lf_text(INVENTORY_PATH))
    installer = _read_lf_text(INSTALLER_PATH)
    workflows = {
        path: _read_lf_text(REPO_ROOT / path)
        for path in WORKFLOW_PATHS
    }
    validate_contract(bootstrap, build, inventory, installer, workflows)
    _test_negative_mutations(bootstrap, build, inventory, installer, workflows)
    _test_installer_logging_mutations(installer)
    installer_module = _load_installer_module()
    _test_verified_pip_output(installer_module)
    _test_target_path_safety(installer_module)
    print("build dependency lock smoke tests passed")
    return 0


def validate_contract(
    bootstrap: str,
    build: str,
    inventory: dict,
    installer: str,
    workflows: dict[str, str],
) -> None:
    bootstrap_entries = _parse_lock(bootstrap, "bootstrap lock")
    build_entries = _parse_lock(build, "build lock")
    _require(
        [(entry["name"], entry["version"]) for entry in bootstrap_entries]
        == [("pip", "26.1.2")],
        "bootstrap lock must contain only pip 26.1.2",
    )
    _require(build_entries[0]["name"] == "pyinstaller", "PyInstaller must be first")
    _require(
        build_entries[0]["version"] == "6.21.0",
        "PyInstaller must be version 6.21.0",
    )
    dependency_names = [entry["name"] for entry in build_entries[1:]]
    _require(
        dependency_names == sorted(dependency_names),
        "transitive build dependencies must be sorted",
    )
    _require("pip" not in {entry["name"] for entry in build_entries}, "pip is duplicated")

    _validate_inventory(inventory, bootstrap_entries, build_entries)
    _validate_installer(installer)
    _validate_workflow_usage(workflows)


def _parse_lock(value: str, label: str) -> list[dict[str, str]]:
    _require(value.endswith("\n") and not value.endswith("\n\n"), f"{label} newline")
    lowered = value.casefold()
    for forbidden in (
        "://",
        "git+",
        "--index-url",
        "--extra-index-url",
        "--trusted-host",
        "--no-binary",
        "--pre",
        " -e ",
        ";",
    ):
        _require(forbidden not in lowered, f"{label} contains forbidden input: {forbidden}")

    lines = value[:-1].split("\n")
    _require(len(lines) % 2 == 0 and bool(lines), f"{label} entry shape is invalid")
    entries = []
    seen = set()
    requirement = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\]+) \\$" )
    hash_line = re.compile(r"^    --hash=sha256:([0-9a-f]{64})$")
    for index in range(0, len(lines), 2):
        requirement_match = requirement.fullmatch(lines[index])
        hash_match = hash_line.fullmatch(lines[index + 1])
        _require(requirement_match is not None, f"{label} has an unpinned requirement")
        _require(hash_match is not None, f"{label} has a missing or invalid hash")
        name = _canonical_name(requirement_match.group(1))
        version = requirement_match.group(2)
        _require(name not in seen, f"{label} contains a duplicate package")
        seen.add(name)
        entries.append({"name": name, "version": version, "sha256": hash_match.group(1)})
    return entries


def _validate_inventory(
    inventory: dict,
    bootstrap_entries: list[dict[str, str]],
    build_entries: list[dict[str, str]],
) -> None:
    _require(inventory.get("schema_version") == 1, "inventory schema must be 1")
    timestamp = inventory.get("resolved_at_utc")
    _require(isinstance(timestamp, str) and timestamp.endswith("Z"), "resolution timestamp")
    try:
        datetime.datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise BuildLockContractError("resolution timestamp is invalid") from exc
    _require(
        inventory.get("target")
        == {"os": "windows", "architecture": "x86_64", "python": "3.11.9"},
        "inventory target must be Windows x86_64 Python 3.11.9",
    )
    _require(inventory.get("index") == OFFICIAL_INDEX, "inventory index must be PyPI")

    bootstrap = inventory.get("bootstrap")
    _require(
        isinstance(bootstrap, dict)
        and bootstrap.get("name") == "pip"
        and bootstrap.get("version") == "26.1.2",
        "inventory bootstrap pin is incorrect",
    )
    _require(
        bootstrap.get("wheel") == "pip-26.1.2-py3-none-any.whl",
        "inventory bootstrap wheel is incorrect",
    )
    _require(HASH.fullmatch(str(bootstrap.get("sha256", ""))) is not None, "pip hash")
    _require(
        bootstrap["sha256"] == bootstrap_entries[0]["sha256"],
        "bootstrap inventory hash differs from lock",
    )
    _require(
        inventory.get("build_root") == {"name": "PyInstaller", "version": "6.21.0"},
        "build root pin is incorrect",
    )

    packages = inventory.get("packages")
    _require(isinstance(packages, list) and bool(packages), "inventory packages missing")
    names = [package.get("name") for package in packages if isinstance(package, dict)]
    _require(len(names) == len(packages), "inventory package entry is invalid")
    _require(names == sorted(names) and len(names) == len(set(names)), "inventory order")
    lock_by_name = {entry["name"]: entry for entry in build_entries}
    _require(set(names) == set(lock_by_name), "inventory package set differs from lock")
    direct_names = []
    for package in packages:
        _require(set(package) == {"name", "version", "wheel", "sha256", "direct"}, "fields")
        name = package["name"]
        _require(name == _canonical_name(name), f"package name is not canonical: {name}")
        _require(WHEEL.fullmatch(str(package["wheel"])) is not None, f"invalid wheel: {name}")
        _require(not str(package["wheel"]).endswith((".tar.gz", ".zip")), "sdist recorded")
        _require(HASH.fullmatch(str(package["sha256"])) is not None, f"invalid hash: {name}")
        _require(package["version"] == lock_by_name[name]["version"], f"version mismatch: {name}")
        _require(package["sha256"] == lock_by_name[name]["sha256"], f"hash mismatch: {name}")
        _require(isinstance(package["direct"], bool), f"direct flag is invalid: {name}")
        if package["direct"]:
            direct_names.append(name)
    _require(direct_names == ["pyinstaller"], "only PyInstaller may be direct")


def _validate_installer(installer: str) -> None:
    for required in (
        'os.name != "nt"',
        'platform.system() != "Windows"',
        "EXPECTED_PYTHON = (3, 11, 9)",
        'S9H_BUILD_VENV_MARKER = ".s9h-build-venv.json"',
        '"purpose": "s9h-build-dependencies"',
        "venv.EnvBuilder(with_pip=True, clear=True)",
        'target / "Scripts" / "python.exe"',
        "_is_relative_to(target, repo)",
        "_is_relative_to(repo, target)",
        "target = target.resolve(strict=False)",
        "repo = repo.resolve()",
        "_read_owned_marker(target)",
        "def _target_is_owned_venv(",
        "_write_owned_marker(target)",
        '"--isolated"',
        '"--disable-pip-version-check"',
        '"--no-input"',
        '"--only-binary=:all:"',
        '"--require-hashes"',
        '"--index-url"',
        '"-m", "pip", "check"',
        '"-m",\n        "PyInstaller",',
        "import importlib.metadata",
        "S9H_BUILD_PYTHON=",
        'print(f"Verified pip {match.group(1)}")',
        "--github-env",
        "--github-path",
        "shell=False",
        "Locked build dependencies verified",
    ):
        _require(required in installer, f"installer contract is missing: {required}")
    _require(
        installer.index("_verify_inventory(installed, expected)")
        < installer.index("_write_owned_marker(target)"),
        "ownership marker must be written after inventory verification",
    )
    _require(
        installer.index("if github_path is not None:")
        < installer.index("_write_owned_marker(target)"),
        "ownership marker must be written after GitHub environment export",
    )
    _require(
        installer.index('if match is None or match.group(1) != EXPECTED_PIP:')
        < installer.index('print(f"Verified pip {match.group(1)}")'),
        "verified pip output must follow exact version validation",
    )
    _require(
        re.search(r"print\s*\([^)]*result\.(?:stdout|stderr)", installer) is None,
        "raw pip process output must not be printed",
    )
    _require(installer.count('"--no-deps"') == 1, "--no-deps must be bootstrap-only")
    for forbidden in (
        "shell=True",
        "--trusted-host",
        "--extra-index-url",
        '"--pre"',
        "--no-binary",
        "--find-links",
        "wheelhouse",
    ):
        _require(forbidden not in installer, f"installer contains forbidden input: {forbidden}")


def _test_installer_logging_mutations(installer: str) -> None:
    success_line = '    print(f"Verified pip {match.group(1)}")\n'
    validation_line = '    if match is None or match.group(1) != EXPECTED_PIP:\n'
    _require(installer.count(success_line) == 1, "verified pip source line is ambiguous")
    raw_output = installer.replace(success_line, "    print(result.stdout)\n", 1)
    early_output = installer.replace(success_line, "", 1).replace(
        validation_line,
        success_line + validation_line,
        1,
    )
    for label, mutated in (
        ("raw pip output", raw_output),
        ("pip output before validation", early_output),
    ):
        try:
            _validate_installer(mutated)
        except BuildLockContractError:
            continue
        raise BuildLockContractError(f"unsafe installer logging mutation accepted: {label}")


def _test_verified_pip_output(installer) -> None:
    cases = (
        ("pip 26.1.1 from <NEUTRAL_PATH> (python 3.11)", "wrong version"),
        ("unexpected output", "malformed output"),
        ("", "empty output"),
    )
    success = subprocess.CompletedProcess(
        args=["python", "-m", "pip", "--version"],
        returncode=0,
        stdout="pip 26.1.2 from <NEUTRAL_PATH> (python 3.11)\n",
        stderr="",
    )
    output = io.StringIO()
    with mock.patch.object(installer, "_run", return_value=success):
        with contextlib.redirect_stdout(output):
            installer._verify_pip(Path("<PYTHON_EXECUTABLE>"))
    value = output.getvalue()
    _require(value == "Verified pip 26.1.2\n", "verified pip output is not sanitized")
    _require(value.count("Verified pip 26.1.2") == 1, "verified pip output is duplicated")
    for forbidden in ("<NEUTRAL_PATH>", " from ", "<PYTHON_EXECUTABLE>", OFFICIAL_INDEX):
        _require(forbidden not in value, f"verified pip output leaked: {forbidden}")

    for stdout, label in cases:
        result = subprocess.CompletedProcess(
            args=["python", "-m", "pip", "--version"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        output = io.StringIO()
        with mock.patch.object(installer, "_run", return_value=result):
            with contextlib.redirect_stdout(output):
                _expect_installer_error(
                    installer,
                    lambda: installer._verify_pip(Path("<PYTHON_EXECUTABLE>")),
                    label,
                )
        _require(not output.getvalue(), f"failed pip verification emitted output: {label}")


def _load_installer_module():
    spec = importlib.util.spec_from_file_location(
        "phase_4b_build_dependency_installer",
        INSTALLER_PATH,
    )
    _require(spec is not None and spec.loader is not None, "installer module load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_target_path_safety(installer) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-phase-4b-path-safety-") as raw_temp:
        temp_root = Path(raw_temp).resolve()
        synthetic_repo = temp_root / "workspace" / "repository"
        synthetic_repo.mkdir(parents=True)
        safe_root = temp_root / "venvs"
        safe_root.mkdir()
        _require(
            not installer._is_relative_to(Path("C:/safe"), Path("D:/repository")),
            "cross-drive ancestry check failed",
        )

        _expect_installer_error(
            installer,
            lambda: installer._validate_target_location(Path(temp_root.anchor), synthetic_repo),
            "filesystem root",
        )
        _expect_installer_error(
            installer,
            lambda: installer._validate_target_location(synthetic_repo, synthetic_repo),
            "repository root",
        )
        _expect_installer_error(
            installer,
            lambda: installer._validate_target_location(
                synthetic_repo / "nested",
                synthetic_repo,
            ),
            "repository descendant",
        )
        _expect_installer_error(
            installer,
            lambda: installer._validate_target_location(
                synthetic_repo.parent,
                synthetic_repo,
            ),
            "repository ancestor",
        )

        with mock.patch.object(installer, "REPO_ROOT", synthetic_repo):
            absent = safe_root / "absent"
            _require(installer._verify_target(absent) == absent, "absent target rejected")

            empty = safe_root / "empty"
            empty.mkdir()
            _require(installer._verify_target(empty) == empty, "empty target rejected")

            existing_file = safe_root / "existing-file"
            existing_file.write_bytes(b"sentinel-file")
            _expect_installer_error(
                installer,
                lambda: installer._verify_target(existing_file),
                "existing file",
            )

            unowned = safe_root / "unowned"
            unowned.mkdir()
            (unowned / "Scripts").mkdir()
            (unowned / "Scripts" / "python.exe").write_bytes(b"unrelated-venv")
            sentinel = unowned / "pyvenv.cfg"
            sentinel.write_bytes(b"do-not-delete")
            before = sentinel.read_bytes()
            _expect_installer_error(
                installer,
                lambda: installer._verify_target(unowned),
                "non-empty unmarked target",
            )
            _require(sentinel.read_bytes() == before, "unowned sentinel changed")

            malformed = _marker_target(installer, safe_root / "malformed", "{not-json\n")
            _expect_installer_error(
                installer,
                lambda: installer._verify_target(malformed),
                "malformed marker",
            )
            wrong_schema = _marker_target(
                installer,
                safe_root / "wrong-schema",
                {"schema_version": 2, "purpose": "s9h-build-dependencies"},
            )
            _expect_installer_error(
                installer,
                lambda: installer._verify_target(wrong_schema),
                "wrong marker schema",
            )
            wrong_purpose = _marker_target(
                installer,
                safe_root / "wrong-purpose",
                {"schema_version": 1, "purpose": "unrelated"},
            )
            _expect_installer_error(
                installer,
                lambda: installer._verify_target(wrong_purpose),
                "wrong marker purpose",
            )
            marker_directory = safe_root / "marker-directory"
            marker_directory.mkdir()
            (marker_directory / installer.S9H_BUILD_VENV_MARKER).mkdir()
            _expect_installer_error(
                installer,
                lambda: installer._verify_target(marker_directory),
                "marker directory",
            )

            unreadable = _marker_target(
                installer,
                safe_root / "unreadable",
                installer.S9H_BUILD_VENV_MARKER_DATA,
            )
            marker_path = unreadable / installer.S9H_BUILD_VENV_MARKER
            path_type = type(marker_path)
            original_read_text = path_type.read_text

            def deny_marker_read(path, *args, **kwargs):
                if path == marker_path:
                    raise OSError("simulated unreadable marker")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(path_type, "read_text", deny_marker_read):
                _expect_installer_error(
                    installer,
                    lambda: installer._verify_target(unreadable),
                    "unreadable marker",
                )

            owned = _marker_target(
                installer,
                safe_root / "owned",
                installer.S9H_BUILD_VENV_MARKER_DATA,
            )
            _require(installer._verify_target(owned) == owned, "owned target rejected")
            _require(installer._target_is_owned_venv(owned), "owned marker not recognized")
            _require(
                not installer._target_is_owned_venv(unowned),
                "unowned target recognized as owned",
            )

            create_calls = []

            def record_create(_builder, target):
                create_calls.append(Path(target))

            with mock.patch.object(installer.venv.EnvBuilder, "create", record_create):
                _expect_installer_error(
                    installer,
                    lambda: installer._create_build_venv(synthetic_repo.parent),
                    "repository ancestor destructive guard",
                )
                _expect_installer_error(
                    installer,
                    lambda: installer._create_build_venv(unowned),
                    "destructive guard",
                )
                _require(not create_calls, "create reached for an unowned target")
                installer._create_build_venv(owned)
                _require(
                    create_calls == [owned],
                    "create was not reached exactly once for an owned target",
                )

            _test_marker_lifecycle(installer, safe_root, synthetic_repo)


def _marker_target(installer, target: Path, value) -> Path:
    target.mkdir()
    marker = target / installer.S9H_BUILD_VENV_MARKER
    if isinstance(value, str):
        content = value
    else:
        content = json.dumps(value, indent=2) + "\n"
    marker.write_text(content, encoding="utf-8", newline="\n")
    return target


def _test_marker_lifecycle(installer, safe_root: Path, synthetic_repo: Path) -> None:
    inventory = {
        "packages": [
            {"name": "pyinstaller", "version": "6.21.0"},
        ],
    }

    def create_skeleton(_builder, target):
        scripts = Path(target) / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "python.exe").write_bytes(b"")

    common_patches = (
        mock.patch.object(installer, "REPO_ROOT", synthetic_repo),
        mock.patch.object(installer, "_load_inventory", return_value=inventory),
        mock.patch.object(installer.venv.EnvBuilder, "create", create_skeleton),
        mock.patch.object(installer, "_run", return_value=None),
        mock.patch.object(installer, "_verify_pip", return_value=None),
        mock.patch.object(installer, "_verify_pyinstaller", return_value=None),
        mock.patch.object(
            installer,
            "_installed_distributions",
            return_value={"pyinstaller": "6.21.0"},
        ),
    )
    success = safe_root / "marker-success"
    with _combined_patches(common_patches):
        installer._install_locked_dependencies(success, None, None)
    marker = success / installer.S9H_BUILD_VENV_MARKER
    _require(marker.is_file(), "success marker was not written")
    _require(
        json.loads(marker.read_text(encoding="utf-8"))
        == installer.S9H_BUILD_VENV_MARKER_DATA,
        "success marker content is invalid",
    )
    _require(marker.read_bytes().endswith(b"\n"), "success marker lacks trailing newline")

    failed = safe_root / "marker-failure"
    failing_patches = common_patches + (
        mock.patch.object(
            installer,
            "_verify_inventory",
            side_effect=installer.BuildDependencyError("simulated verification failure"),
        ),
    )
    with _combined_patches(failing_patches):
        _expect_installer_error(
            installer,
            lambda: installer._install_locked_dependencies(failed, None, None),
            "failed installation marker",
        )
    _require(
        not (failed / installer.S9H_BUILD_VENV_MARKER).exists(),
        "marker was written before complete verification",
    )


class _combined_patches:
    def __init__(self, patches) -> None:
        self._patches = patches

    def __enter__(self):
        for patcher in self._patches:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        for patcher in reversed(self._patches):
            patcher.stop()


def _expect_installer_error(installer, callback, label: str) -> None:
    try:
        callback()
    except installer.BuildDependencyError:
        return
    raise BuildLockContractError(f"unsafe installer case was accepted: {label}")


def _validate_workflow_usage(workflows: dict[str, str]) -> None:
    _require(tuple(sorted(workflows)) == tuple(sorted(WORKFLOW_PATHS)), "workflow inventory")
    direct_pip = re.compile(
        r"(?im)^\s*(?:run:\s*)?(?:python\s+-m\s+)?pip\s+install\b"
    )
    for path, workflow in workflows.items():
        _require(
            workflow.count("python scripts/install_build_dependencies.py") == 1,
            f"{path} must call installer once",
        )
        _require(direct_pip.search(workflow) is None, f"{path} contains direct pip install")
    ci = workflows[".github/workflows/ci.yml"]
    _require(
        ci.index("Run tracked smoke suite") < ci.index("Validate locked build dependencies"),
        "CI installer must run after smoke suite",
    )
    legacy = workflows[LEGACY_WORKFLOW]
    _require(legacy.count("uses: actions/checkout@") == 1, "legacy checkout count")
    _require(
        legacy.index("Install locked build dependencies")
        < legacy.index(r".\scripts\build_prerelease_v1_2_7_rc1.ps1"),
        "legacy installer order",
    )
    for path in FIXED_TAG_WORKFLOWS:
        workflow = workflows[path]
        _require(workflow.count("uses: actions/checkout@") == 2, f"{path} checkout count")
        _require(
            workflow.index("Check out dependency lock source")
            < workflow.index("Install locked build dependencies")
            < workflow.index("Check out release tag")
            < workflow.index("Verify annotated tag and release absence"),
            f"{path} fixed-tag order",
        )


def _test_negative_mutations(
    bootstrap: str,
    build: str,
    inventory: dict,
    installer: str,
    workflows: dict[str, str],
) -> None:
    mutations = []

    mutations.append(("missing hash", bootstrap, build.replace(
        "    --hash=sha256:f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597\n",
        "",
        1,
    ), inventory, workflows))
    mutations.append(("wrong pip", bootstrap.replace("26.1.2", "26.1.1", 1), build, inventory, workflows))
    mutations.append(("wrong PyInstaller", bootstrap, build.replace("6.21.0", "6.20.0", 1), inventory, workflows))
    mutations.append(("unpinned dependency", bootstrap, build.replace("altgraph==", "altgraph>=", 1), inventory, workflows))
    duplicate = build + (
        "altgraph==0.17.5 \\\n"
        "    --hash=sha256:f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597\n"
    )
    mutations.append(("duplicate package", bootstrap, duplicate, inventory, workflows))
    mutations.append(("URL requirement", bootstrap, build.replace("altgraph==0.17.5 \\", "altgraph @ https://example.invalid/altgraph.whl", 1), inventory, workflows))
    mutations.append(("VCS requirement", bootstrap, build.replace("altgraph==0.17.5 \\", "altgraph @ git+https://example.invalid/repo.git", 1), inventory, workflows))
    mutations.append(("trusted host", bootstrap, build + "--trusted-host example.invalid\n", inventory, workflows))

    sdist_inventory = copy.deepcopy(inventory)
    sdist_inventory["packages"][0]["wheel"] = "altgraph-0.17.5.tar.gz"
    mutations.append(("sdist filename", bootstrap, build, sdist_inventory, workflows))
    hash_inventory = copy.deepcopy(inventory)
    hash_inventory["packages"][0]["sha256"] = "0" * 64
    mutations.append(("inventory hash mismatch", bootstrap, build, hash_inventory, workflows))
    python_inventory = copy.deepcopy(inventory)
    python_inventory["target"]["python"] = "3.11"
    mutations.append(("wrong target Python", bootstrap, build, python_inventory, workflows))

    direct_workflows = copy.deepcopy(workflows)
    direct_workflows[LEGACY_WORKFLOW] += (
        "\n      - name: Unsafe install\n"
        "        run: python -m pip install pyinstaller\n"
    )
    mutations.append(("direct pip workflow", bootstrap, build, inventory, direct_workflows))
    ordered_workflows = copy.deepcopy(workflows)
    ordered_workflows[".github/workflows/release-v1.3.1.yml"] = _move_named_step_after(
        ordered_workflows[".github/workflows/release-v1.3.1.yml"],
        "Install locked build dependencies",
        "Build and validate checksum-pinned assets",
    )
    mutations.append(("installer after build", bootstrap, build, inventory, ordered_workflows))

    for label, mutated_bootstrap, mutated_build, mutated_inventory, mutated_workflows in mutations:
        try:
            validate_contract(
                mutated_bootstrap,
                mutated_build,
                mutated_inventory,
                installer,
                mutated_workflows,
            )
        except BuildLockContractError:
            continue
        raise BuildLockContractError(f"negative mutation was accepted: {label}")


def _move_named_step_after(workflow: str, name: str, target: str) -> str:
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    match = pattern.search(workflow)
    _require(match is not None, f"mutation step is missing: {name}")
    block = match.group(0).rstrip("\n") + "\n"
    without = workflow[: match.start()] + workflow[match.end() :]
    target_pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(target)}\s*$\n.*?(?=^      - |\Z)"
    )
    target_match = target_pattern.search(without)
    _require(target_match is not None, f"mutation target is missing: {target}")
    return without[: target_match.end()] + block + without[target_match.end() :]


def _read_lf_text(path: Path) -> str:
    _require(path.is_file(), f"required file is missing: {path.name}")
    value = path.read_bytes()
    _require(not value.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM is forbidden: {path.name}")
    _require(b"\x00" not in value, f"text hygiene: {path.name}")
    try:
        return value.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise BuildLockContractError(f"file is not UTF-8: {path.name}") from exc


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildLockContractError(message)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
