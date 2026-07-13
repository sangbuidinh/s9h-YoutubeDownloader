from __future__ import annotations

import copy
import datetime
import json
import re
import sys
from pathlib import Path


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
        "venv.EnvBuilder(with_pip=True, clear=True)",
        'target / "Scripts" / "python.exe"',
        'repo in target.parents',
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
        "--github-env",
        "--github-path",
        "shell=False",
        "Locked build dependencies verified",
    ):
        _require(required in installer, f"installer contract is missing: {required}")
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
