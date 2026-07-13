from __future__ import annotations

import argparse
import json
import os
import platform
import re
import struct
import subprocess
import sys
import venv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_LOCK = REPO_ROOT / "requirements-build-bootstrap.txt"
BUILD_LOCK = REPO_ROOT / "requirements-build.txt"
INVENTORY_PATH = REPO_ROOT / ".github" / "build-dependencies.json"
EXPECTED_PYTHON = (3, 11, 9)
EXPECTED_PIP = "26.1.2"
EXPECTED_PYINSTALLER = "6.21.0"
OFFICIAL_INDEX = "https://pypi.org/simple"
S9H_BUILD_VENV_MARKER = ".s9h-build-venv.json"
S9H_BUILD_VENV_MARKER_DATA = {
    "schema_version": 1,
    "purpose": "s9h-build-dependencies",
}


class BuildDependencyError(RuntimeError):
    pass


def main() -> int:
    args = _parse_args()
    _verify_host()
    target = args.venv.expanduser().resolve(strict=False)
    _install_locked_dependencies(target, args.github_env, args.github_path)
    return 0


def _install_locked_dependencies(
    target: Path,
    github_env: Path | None,
    github_path: Path | None,
) -> None:
    inventory = _load_inventory()

    target = _create_build_venv(target)
    venv_python = target / "Scripts" / "python.exe"
    scripts_dir = target / "Scripts"
    if not venv_python.is_file():
        raise BuildDependencyError("The build virtual environment has no Python executable")

    _run(
        venv_python,
        "bootstrap pip",
        "-m",
        "pip",
        "install",
        "--isolated",
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--no-deps",
        "--require-hashes",
        "--index-url",
        OFFICIAL_INDEX,
        "-r",
        str(BOOTSTRAP_LOCK),
    )
    _verify_pip(venv_python)

    _run(
        venv_python,
        "install build dependencies",
        "-m",
        "pip",
        "install",
        "--isolated",
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--require-hashes",
        "--index-url",
        OFFICIAL_INDEX,
        "-r",
        str(BUILD_LOCK),
    )
    _run(venv_python, "pip dependency check", "-m", "pip", "check")
    _verify_pyinstaller(venv_python)
    installed = _installed_distributions(venv_python)
    expected = _expected_packages(inventory)
    _verify_inventory(installed, expected)

    for name in sorted(expected):
        print(f"{name}=={expected[name]}")

    if github_env is not None:
        _append_utf8(github_env, f"S9H_BUILD_PYTHON={venv_python.resolve()}")
    if github_path is not None:
        _append_utf8(github_path, str(scripts_dir.resolve()))

    _write_owned_marker(target)
    print("Locked build dependencies verified")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install locked build dependencies")
    parser.add_argument("--venv", required=True, type=Path)
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--github-path", type=Path)
    return parser.parse_args()


def _verify_host() -> None:
    if os.name != "nt" or platform.system() != "Windows":
        raise BuildDependencyError("Locked build dependencies require Windows")
    if sys.version_info[:3] != EXPECTED_PYTHON:
        expected = ".".join(str(value) for value in EXPECTED_PYTHON)
        actual = ".".join(str(value) for value in sys.version_info[:3])
        raise BuildDependencyError(f"Python {expected} is required; found {actual}")
    if struct.calcsize("P") != 8 or platform.machine().casefold() not in {
        "amd64",
        "x86_64",
    }:
        raise BuildDependencyError("Locked build dependencies require Windows x86_64")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_target_location(target: Path, repo: Path) -> None:
    target = target.resolve(strict=False)
    repo = repo.resolve()
    if target == Path(target.anchor):
        raise BuildDependencyError("The virtual environment cannot be a filesystem root")
    if _is_relative_to(target, repo):
        raise BuildDependencyError("The build virtual environment must be outside the repository")
    if _is_relative_to(repo, target):
        raise BuildDependencyError("The build virtual environment cannot contain the repository")


def _read_owned_marker(target: Path) -> dict:
    marker = target / S9H_BUILD_VENV_MARKER
    if not marker.exists():
        raise BuildDependencyError("The non-empty target is not owned by this installer")
    if not marker.is_file():
        raise BuildDependencyError("The build virtual environment marker is not a file")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BuildDependencyError("The build virtual environment marker is unreadable") from exc
    except json.JSONDecodeError as exc:
        raise BuildDependencyError("The build virtual environment marker is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BuildDependencyError("The build virtual environment marker schema is invalid")
    if value.get("purpose") != "s9h-build-dependencies":
        raise BuildDependencyError("The build virtual environment marker purpose is invalid")
    if value != S9H_BUILD_VENV_MARKER_DATA:
        raise BuildDependencyError("The build virtual environment marker contains unknown data")
    return value


def _target_is_owned_venv(target: Path) -> bool:
    try:
        _read_owned_marker(target)
    except BuildDependencyError:
        return False
    return True


def _verify_target(target: Path) -> Path:
    target = target.resolve(strict=False)
    repo = REPO_ROOT.resolve()
    _validate_target_location(target, repo)
    if not target.exists():
        return target
    if not target.is_dir():
        raise BuildDependencyError("The build virtual environment target must be a directory")
    try:
        next(target.iterdir())
    except StopIteration:
        return target
    except OSError as exc:
        raise BuildDependencyError("The build virtual environment target is unreadable") from exc
    _read_owned_marker(target)
    return target


def _create_build_venv(target: Path) -> Path:
    target = _verify_target(target)
    venv.EnvBuilder(with_pip=True, clear=True).create(target)
    return target


def _write_owned_marker(target: Path) -> None:
    marker = target / S9H_BUILD_VENV_MARKER
    content = json.dumps(S9H_BUILD_VENV_MARKER_DATA, indent=2) + "\n"
    with marker.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _load_inventory() -> dict:
    try:
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildDependencyError("Build dependency inventory is unavailable") from exc
    if inventory.get("schema_version") != 1:
        raise BuildDependencyError("Unsupported build dependency inventory schema")
    if inventory.get("index") != OFFICIAL_INDEX:
        raise BuildDependencyError("Build dependency inventory uses an unexpected index")
    return inventory


def _run(python: Path, label: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(python), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if result.returncode != 0:
        raise BuildDependencyError(f"{label} failed with exit code {result.returncode}")
    return result


def _verify_pip(venv_python: Path) -> None:
    result = _run(venv_python, "verify pip", "-m", "pip", "--version")
    match = re.match(r"^pip\s+([^\s]+)\s+", result.stdout.strip())
    if match is None or match.group(1) != EXPECTED_PIP:
        raise BuildDependencyError("The locked pip version was not installed")
    print(f"Verified pip {match.group(1)}")


def _verify_pyinstaller(venv_python: Path) -> None:
    result = _run(
        venv_python,
        "verify PyInstaller",
        "-m",
        "PyInstaller",
        "--version",
    )
    if result.stdout.strip() != EXPECTED_PYINSTALLER:
        raise BuildDependencyError("The locked PyInstaller version was not installed")


def _installed_distributions(venv_python: Path) -> dict[str, str]:
    probe = (
        "import importlib.metadata as m, json, re; "
        "c=lambda v: re.sub(r'[-_.]+', '-', v).lower(); "
        "print(json.dumps({c(d.metadata['Name']): d.version for d in m.distributions()}, "
        "sort_keys=True))"
    )
    result = _run(venv_python, "read installed distributions", "-c", probe)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BuildDependencyError("Installed package inventory is invalid") from exc
    if not isinstance(value, dict):
        raise BuildDependencyError("Installed package inventory is not a mapping")
    return {str(name): str(version) for name, version in value.items()}


def _expected_packages(inventory: dict) -> dict[str, str]:
    packages = inventory.get("packages")
    if not isinstance(packages, list):
        raise BuildDependencyError("Build package inventory is missing")
    expected: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise BuildDependencyError("Build package inventory contains an invalid entry")
        name = str(package.get("name", ""))
        version = str(package.get("version", ""))
        if not name or not version or name in expected:
            raise BuildDependencyError("Build package inventory contains duplicate or empty data")
        expected[name] = version
    return expected


def _verify_inventory(installed: dict[str, str], expected: dict[str, str]) -> None:
    for name, version in expected.items():
        if installed.get(name) != version:
            raise BuildDependencyError(f"Managed package version mismatch: {name}")
    allowed = set(expected) | {"pip"}
    unexpected = sorted(set(installed) - allowed)
    if unexpected:
        raise BuildDependencyError("Unexpected packages exist in the build environment")


def _append_utf8(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(value + "\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildDependencyError as exc:
        print(f"Build dependency installation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
