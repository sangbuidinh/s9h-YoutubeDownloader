from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "scripts" / "prepare_release_bundle.py"
SOURCE_COMMIT = "1" * 40
CONTROL_COMMIT = "2" * 40
RC_TAG = "v1.3.0-rc.1"
STABLE_TAG = "v1.3.1"


def main() -> int:
    bundle = _load_bundle_module()
    with tempfile.TemporaryDirectory(prefix="s9h-release-bundle-smoke-") as temp:
        root = Path(temp)
        _test_positive_contract(bundle, root)
        _test_missing_and_invalid_inputs(bundle, root)
        _test_bundle_mutations(bundle, root)
        _test_nonempty_output(bundle, root)
        _test_symlink_input(bundle, root)
    print("release bundle smoke tests passed")
    return 0


def _load_bundle_module():
    spec = importlib.util.spec_from_file_location("prepare_release_bundle", TOOL_PATH)
    _require(spec is not None and spec.loader is not None, "could not load bundle tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_positive_contract(bundle, root: Path) -> None:
    release_a = _release_root(root / "release-a", RC_TAG)
    release_b = _release_root(root / "release-b", RC_TAG)
    bundle_a = root / "bundle-a"
    bundle_b = root / "bundle-b"
    create_output = _run_cli(
        "create",
        "--release-root",
        str(release_a),
        "--bundle-root",
        str(bundle_a),
        "--tag",
        RC_TAG,
        "--source-commit",
        SOURCE_COMMIT,
        "--control-commit",
        CONTROL_COMMIT,
        "--prerelease",
        "true",
    )
    _require(
        create_output == "Release bundle created and verified",
        "create success output changed",
    )
    verify_output = _run_cli(
        "verify",
        "--bundle-root",
        str(bundle_a),
        "--tag",
        RC_TAG,
        "--source-commit",
        SOURCE_COMMIT,
        "--control-commit",
        CONTROL_COMMIT,
        "--prerelease",
        "true",
    )
    _require(verify_output == "Release bundle verified", "verify success output changed")
    bundle.create_bundle(
        release_root=release_b,
        bundle_root=bundle_b,
        tag=RC_TAG,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=True,
    )
    _require(_tree_bytes(bundle_a) == _tree_bytes(bundle_b), "bundle output is not deterministic")
    _validate_generated_files(bundle_a, RC_TAG, True)

    stable_release = _release_root(root / "release-stable", STABLE_TAG)
    stable_bundle = root / "bundle-stable"
    bundle.create_bundle(
        release_root=stable_release,
        bundle_root=stable_bundle,
        tag=STABLE_TAG,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=False,
    )
    bundle.verify_bundle(
        bundle_root=stable_bundle,
        tag=STABLE_TAG,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=False,
    )
    _validate_generated_files(stable_bundle, STABLE_TAG, False)


def _test_missing_and_invalid_inputs(bundle, root: Path) -> None:
    cases = []
    missing_exe = _release_root(root / "missing-exe", RC_TAG)
    (missing_exe / "assets" / bundle.EXE_NAME).unlink()
    cases.append(("missing EXE", missing_exe))
    missing_zip = _release_root(root / "missing-zip", RC_TAG)
    (missing_zip / "assets" / bundle._zip_name(RC_TAG)).unlink()
    cases.append(("missing ZIP", missing_zip))
    invalid_zip = _release_root(root / "invalid-zip", RC_TAG)
    (invalid_zip / "assets" / bundle._zip_name(RC_TAG)).write_bytes(b"not-a-zip")
    cases.append(("invalid ZIP", invalid_zip))
    invalid_exe = _release_root(root / "invalid-exe", RC_TAG)
    (invalid_exe / "assets" / bundle.EXE_NAME).write_bytes(b"NO")
    cases.append(("EXE without MZ", invalid_exe))
    empty_exe = _release_root(root / "empty-exe", RC_TAG)
    (empty_exe / "assets" / bundle.EXE_NAME).write_bytes(b"")
    cases.append(("empty asset", empty_exe))
    missing_notes = _release_root(root / "missing-notes", RC_TAG)
    (missing_notes / bundle.NOTES_NAME).unlink()
    cases.append(("missing notes", missing_notes))
    for index, (label, release_root) in enumerate(cases):
        _expect_bundle_error(
            bundle,
            label,
            lambda release_root=release_root, index=index: bundle.create_bundle(
                release_root=release_root,
                bundle_root=root / f"invalid-output-{index}",
                tag=RC_TAG,
                source_commit=SOURCE_COMMIT,
                control_commit=CONTROL_COMMIT,
                prerelease=True,
            ),
        )


def _test_bundle_mutations(bundle, root: Path) -> None:
    source = _release_root(root / "mutation-release", RC_TAG)
    pristine = root / "mutation-pristine"
    bundle.create_bundle(
        release_root=source,
        bundle_root=pristine,
        tag=RC_TAG,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=True,
    )

    mutations = [
        ("extra file", lambda target: (target / "extra.txt").write_bytes(b"extra")),
        ("asset tamper", lambda target: _append(target / "assets" / bundle.EXE_NAME)),
        ("notes tamper", lambda target: _append(target / bundle.NOTES_NAME)),
        ("checksum tamper", lambda target: _append(target / "assets" / bundle.CHECKSUM_NAME)),
        ("manifest tamper", lambda target: _manifest_field(target, "bundle_format", "wrong")),
        ("wrong file size", lambda target: _record_field(target, "assets", 0, "size", 1)),
        ("wrong asset hash", lambda target: _record_field(target, "assets", 0, "sha256", "0" * 64)),
        (
            "wrong checksum-file hash",
            lambda target: _record_field(target, "checksum_file", None, "sha256", "0" * 64),
        ),
        (
            "wrong notes hash",
            lambda target: _record_field(target, "release_notes", None, "sha256", "0" * 64),
        ),
        ("duplicate manifest asset", _duplicate_asset),
        ("unexpected manifest field", lambda target: _manifest_field(target, "unexpected", True)),
        ("traversal filename", lambda target: _record_field(target, "assets", 0, "name", "../bad")),
        ("filename", _rename_asset),
        ("malformed JSON", lambda target: (target / bundle.MANIFEST_NAME).write_bytes(b"{\n")),
        ("BOM", lambda target: _prefix(target / bundle.MANIFEST_NAME, b"\xef\xbb\xbf")),
        (
            "CRLF checksum file",
            lambda target: _to_crlf(target / "assets" / bundle.CHECKSUM_NAME),
        ),
    ]
    for index, (label, mutate) in enumerate(mutations):
        target = root / f"mutation-{index}"
        shutil.copytree(pristine, target)
        mutate(target)
        _expect_verify_error(bundle, label, target)

    _expect_bundle_error(
        bundle,
        "wrong tag",
        lambda: bundle.verify_bundle(
            bundle_root=pristine,
            tag=STABLE_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
        ),
    )
    _expect_bundle_error(
        bundle,
        "wrong source commit",
        lambda: bundle.verify_bundle(
            bundle_root=pristine,
            tag=RC_TAG,
            source_commit="3" * 40,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
        ),
    )
    _expect_bundle_error(
        bundle,
        "wrong control commit",
        lambda: bundle.verify_bundle(
            bundle_root=pristine,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit="3" * 40,
            prerelease=True,
        ),
    )
    _expect_bundle_error(
        bundle,
        "wrong prerelease flag",
        lambda: bundle.verify_bundle(
            bundle_root=pristine,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=False,
        ),
    )


def _test_nonempty_output(bundle, root: Path) -> None:
    release = _release_root(root / "nonempty-release", RC_TAG)
    output = root / "nonempty-output"
    output.mkdir()
    (output / "sentinel.txt").write_bytes(b"preserve")
    _expect_bundle_error(
        bundle,
        "non-empty bundle output target",
        lambda: bundle.create_bundle(
            release_root=release,
            bundle_root=output,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
        ),
    )
    _require((output / "sentinel.txt").read_bytes() == b"preserve", "non-empty output changed")


def _test_symlink_input(bundle, root: Path) -> None:
    release = _release_root(root / "symlink-release", RC_TAG)
    exe = release / "assets" / bundle.EXE_NAME
    target = root / "symlink-target.exe"
    target.write_bytes(exe.read_bytes())
    exe.unlink()
    try:
        os.symlink(target, exe)
    except OSError:
        exe.write_bytes(target.read_bytes())
        real_is_reparse = bundle._is_reparse

        def simulated_reparse(path: Path) -> bool:
            return Path(path) == exe or real_is_reparse(Path(path))

        context = mock.patch.object(bundle, "_is_reparse", simulated_reparse)
    else:
        context = _null_context()
    with context:
        _expect_bundle_error(
            bundle,
            "symlink input",
            lambda: bundle.create_bundle(
                release_root=release,
                bundle_root=root / "symlink-output",
                tag=RC_TAG,
                source_commit=SOURCE_COMMIT,
                control_commit=CONTROL_COMMIT,
                prerelease=True,
            ),
        )


def _release_root(root: Path, tag: str) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "Youtube.Downloaderbs.exe").write_bytes(b"MZ" + b"synthetic-exe\n")
    zip_path = assets / f"Youtube-Downloaderbs-{tag}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
        info = zipfile.ZipInfo("README.txt", date_time=(2020, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        archive.writestr(info, b"synthetic portable bundle\n")
    (root / "RELEASE_NOTES.md").write_bytes(b"# Synthetic release\n")
    return root


def _validate_generated_files(root: Path, tag: str, prerelease: bool) -> None:
    expected = {
        "RELEASE_MANIFEST.json",
        "RELEASE_NOTES.md",
        "assets/SHA256SUMS.txt",
        "assets/Youtube.Downloaderbs.exe",
        f"assets/Youtube-Downloaderbs-{tag}.zip",
    }
    _require(set(_tree_bytes(root)) == expected, "generated file set is invalid")
    checksum = (root / "assets" / "SHA256SUMS.txt").read_bytes()
    _require(not checksum.startswith(b"\xef\xbb\xbf"), "checksum BOM")
    _require(b"\r" not in checksum and checksum.endswith(b"\n"), "checksum EOL")
    lines = checksum.decode("ascii").splitlines()
    _require(len(lines) == 2, "checksum line count")
    names = [line.split("  ", 1)[1] for line in lines]
    _require(names == sorted(names), "checksum order")
    manifest_bytes = (root / "RELEASE_MANIFEST.json").read_bytes()
    _require(b"\r" not in manifest_bytes and manifest_bytes.endswith(b"\n"), "manifest EOL")
    manifest = json.loads(manifest_bytes)
    _require(manifest["release_tag"] == tag, "manifest tag")
    _require(manifest["prerelease"] is prerelease, "manifest prerelease")
    _require(manifest["source_commit"] == SOURCE_COMMIT, "manifest source commit")
    _require(manifest["control_commit"] == CONTROL_COMMIT, "manifest control commit")
    _require("timestamp" not in manifest_bytes.decode().casefold(), "manifest timestamp")


def _run_cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(TOOL_PATH), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    _require(not result.stderr, "bundle CLI wrote stderr on success")
    return result.stdout.strip()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _expect_verify_error(bundle, label: str, root: Path) -> None:
    _expect_bundle_error(
        bundle,
        label,
        lambda: bundle.verify_bundle(
            bundle_root=root,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
        ),
    )


def _expect_bundle_error(bundle, label: str, callback) -> None:
    try:
        callback()
    except bundle.BundleError:
        return
    raise AssertionError(f"mutation was accepted: {label}")


def _manifest(root: Path) -> dict:
    return json.loads((root / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, value: dict) -> None:
    (root / "RELEASE_MANIFEST.json").write_bytes(
        (json.dumps(value, indent=2) + "\n").encode("utf-8")
    )


def _manifest_field(root: Path, key: str, value) -> None:
    manifest = _manifest(root)
    manifest[key] = value
    _write_manifest(root, manifest)


def _record_field(root: Path, section: str, index: int | None, key: str, value) -> None:
    manifest = _manifest(root)
    record = manifest[section] if index is None else manifest[section][index]
    record[key] = value
    _write_manifest(root, manifest)


def _duplicate_asset(root: Path) -> None:
    manifest = _manifest(root)
    manifest["assets"][1] = dict(manifest["assets"][0])
    _write_manifest(root, manifest)


def _rename_asset(root: Path) -> None:
    source = root / "assets" / "Youtube.Downloaderbs.exe"
    source.rename(root / "assets" / "renamed.exe")


def _append(path: Path) -> None:
    with path.open("ab") as stream:
        stream.write(b"tamper")


def _prefix(path: Path, prefix: bytes) -> None:
    path.write_bytes(prefix + path.read_bytes())


def _to_crlf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
