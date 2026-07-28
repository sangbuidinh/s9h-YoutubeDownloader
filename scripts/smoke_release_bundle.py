from __future__ import annotations

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

import prepare_release_legal_payload as legal_builder
import create_synthetic_release_sbom_input as sbom_fixture
import release_sbom


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "scripts" / "prepare_release_bundle.py"
POLICY_PATH = REPO_ROOT / "legal" / "release-policy.json"
V2_CONTRACT_PATH = REPO_ROOT / "legal" / "release-assets-v2.json"
V3_CONTRACT_PATH = REPO_ROOT / "legal" / "release-assets-v3.json"
BASELINE_COMMIT = "9ca319d01164c4bb353816175f429d564e41ee7d"
SOURCE_COMMIT = "1" * 40
CONTROL_COMMIT = "2" * 40
RC_TAG = "v1.3.0-rc.1"
STABLE_TAG = "v1.3.1"
CI_TAG = "v0.0.0-ci"


def main() -> int:
    bundle = _load_bundle_module()
    with tempfile.TemporaryDirectory(prefix="s9h-release-bundle-versioned-smoke-") as temp:
        root = Path(temp)
        _test_v2_compatibility(bundle, root)
        _test_positive_contract(bundle, root)
        _test_missing_and_invalid_inputs(bundle, root)
        _test_bundle_mutations(bundle, root)
        _test_source_zip_mutations(bundle, root)
        _test_nonempty_output(bundle, root)
        _test_symlink_input(bundle, root)
    print("release bundle smoke tests passed: v2 compatibility and v3 SBOM integration")
    return 0


def _load_bundle_module():
    spec = importlib.util.spec_from_file_location("prepare_release_bundle", TOOL_PATH)
    _require(spec is not None and spec.loader is not None, "could not load bundle tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_v2_compatibility(bundle, root: Path) -> None:
    baseline_contract = subprocess.run(
        ["git", "show", f"{BASELINE_COMMIT}:legal/release-assets-v2.json"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    _require(
        V2_CONTRACT_PATH.read_bytes() == baseline_contract,
        "release assets v2 contract changed from the pre-R1 baseline",
    )

    fixture = _release_fixture(root / "v2-fixture", RC_TAG)
    v2_bundle = root / "v2-bundle"
    create_output = _run_cli(
        "create",
        *_v2_create_cli_arguments(fixture, v2_bundle, RC_TAG, True),
    )
    _require(
        create_output == "Release bundle v2 created and verified",
        "historical v2 create output changed",
    )
    baseline_tool = root / "baseline-prepare-release-bundle.py"
    baseline_tool.write_bytes(
        subprocess.run(
            ["git", "show", f"{BASELINE_COMMIT}:scripts/prepare_release_bundle.py"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
    )
    baseline_bundle = root / "baseline-v2-bundle"
    baseline_result = subprocess.run(
        [
            sys.executable,
            str(baseline_tool),
            "create",
            *_v2_create_cli_arguments(fixture, baseline_bundle, RC_TAG, True),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT / "scripts"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    _require(
        baseline_result.stdout.strip() == "Release bundle v2 created and verified"
        and not baseline_result.stderr,
        "pre-R1 baseline v2 fixture failed",
    )
    _require(
        _tree_bytes(v2_bundle) == _tree_bytes(baseline_bundle),
        "historical v2 output bytes changed from the pre-R1 implementation",
    )
    verify_output = _run_cli(
        "verify",
        *_v2_verify_cli_arguments(v2_bundle, RC_TAG, True, require_ready=False),
    )
    _require(verify_output == "Release bundle v2 verified", "historical v2 verify output changed")
    _validate_v2_generated_files(v2_bundle, RC_TAG, True)

    failed = _run_cli_result(
        "verify",
        *_v2_verify_cli_arguments(v2_bundle, RC_TAG, True, require_ready=True),
    )
    _require(failed.returncode == 1, "historical v2 publish-ready verification passed")
    _require(
        "release bundle is not approved for publishing" in failed.stderr,
        "historical v2 publish-ready failure changed",
    )

    _expect_bundle_error(
        bundle,
        "v3 evidence accepted by v2",
        lambda: bundle.verify_bundle(
            bundle_root=v2_bundle,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
            policy=POLICY_PATH,
            asset_contract=V2_CONTRACT_PATH,
            legal_payload_path=v2_bundle / "assets" / _legal_name(RC_TAG),
            source_assets_root=v2_bundle / "assets",
            require_release_ready=False,
            sbom_input=Path(fixture["sbom_input"]),
        ),
    )
    _expect_bundle_error(
        bundle,
        "missing v3 SBOM input",
        lambda: bundle.create_bundle(
            release_root=Path(fixture["release"]),
            bundle_root=root / "v3-without-evidence",
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
            policy=POLICY_PATH,
            asset_contract=V3_CONTRACT_PATH,
            legal_payload_path=Path(fixture["legal"]),
            source_assets_root=Path(fixture["sources"]),
        ),
    )

    v3_bundle = root / "v3-cross-contract"
    _create_bundle(bundle, fixture, v3_bundle, RC_TAG, True)
    _expect_bundle_error(
        bundle,
        "v2 bundle accepted as v3",
        lambda: bundle.verify_bundle(
            bundle_root=v2_bundle,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
            policy=POLICY_PATH,
            asset_contract=V3_CONTRACT_PATH,
            legal_payload_path=v2_bundle / "assets" / _legal_name(RC_TAG),
            source_assets_root=v2_bundle / "assets",
            require_release_ready=False,
            sbom_input=Path(fixture["sbom_input"]),
        ),
    )
    _expect_bundle_error(
        bundle,
        "v3 bundle accepted as v2",
        lambda: bundle.verify_bundle(
            bundle_root=v3_bundle,
            tag=RC_TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=True,
            policy=POLICY_PATH,
            asset_contract=V2_CONTRACT_PATH,
            legal_payload_path=v3_bundle / "assets" / _legal_name(RC_TAG),
            source_assets_root=v3_bundle / "assets",
            require_release_ready=False,
        ),
    )


def _test_positive_contract(bundle, root: Path) -> None:
    contract = json.loads(V3_CONTRACT_PATH.read_text(encoding="utf-8"))
    _require(contract["bundle_format"] == "s9h-release-bundle-v3", "v3 contract format")
    _require(contract["bundle_manifest_schema_version"] == 3, "v3 manifest schema")
    _require(contract["sbom_input_required"] is True, "v3 SBOM input requirement")
    _require(
        [item["role"] for item in contract["release_assets"]]
        == [
            "application-executable",
            "portable-package",
            "legal-payload",
            "aria2-source",
            "ffmpeg-source",
            "release-sbom",
        ],
        "v3 release asset declarations",
    )
    _require(
        next(
            item for item in contract["release_assets"] if item["role"] == "release-sbom"
        )
        == {
            "role": "release-sbom",
            "filename": "Youtube-Downloaderbs-{tag}.spdx.json",
            "required": True,
        },
        "v3 SBOM asset declaration",
    )
    fixture_a = _release_fixture(root / "fixture-a", RC_TAG)
    fixture_b = _release_fixture(root / "fixture-b", RC_TAG)
    bundle_a = root / "bundle-a"
    bundle_b = root / "bundle-b"
    create_output = _run_cli(
        "create",
        *_create_cli_arguments(fixture_a, bundle_a, RC_TAG, True),
    )
    _require(
        create_output == "Release bundle v3 created and verified",
        "create success output changed",
    )
    shutil.copy2(Path(fixture_a["sbom_input"]), _sbom_input_path(bundle_a))
    verify_output = _run_cli(
        "verify",
        *_verify_cli_arguments(
            bundle_a,
            RC_TAG,
            True,
            sbom_input=Path(fixture_a["sbom_input"]),
            require_ready=False,
        ),
    )
    _require(verify_output == "Release bundle v3 verified", "verify success output changed")
    _create_bundle(bundle, fixture_b, bundle_b, RC_TAG, True)
    _require(_tree_bytes(bundle_a) == _tree_bytes(bundle_b), "bundle output is not deterministic")
    _validate_generated_files(bundle_a, RC_TAG, True)
    _expect_bundle_error(
        bundle,
        "publish-ready verification accepted blocked bundle",
        lambda: _verify_bundle(bundle, bundle_a, RC_TAG, True, require_ready=True),
    )

    stable_fixture = _release_fixture(root / "fixture-stable", STABLE_TAG)
    stable_bundle = root / "bundle-stable"
    _create_bundle(bundle, stable_fixture, stable_bundle, STABLE_TAG, False)
    _verify_bundle(bundle, stable_bundle, STABLE_TAG, False, require_ready=False)
    _validate_generated_files(stable_bundle, STABLE_TAG, False)

    ci_fixture = _release_fixture(root / "fixture-ci", CI_TAG)
    ci_bundle = root / "bundle-ci"
    _create_bundle(bundle, ci_fixture, ci_bundle, CI_TAG, True)
    _verify_bundle(bundle, ci_bundle, CI_TAG, True, require_ready=False)
    _validate_generated_files(ci_bundle, CI_TAG, True)


def _test_missing_and_invalid_inputs(bundle, root: Path) -> None:
    cases: list[tuple[str, dict[str, Path | str]]] = []

    missing_exe = _release_fixture(root / "missing-exe", RC_TAG)
    (missing_exe["release"] / "assets" / bundle.EXE_NAME).unlink()
    cases.append(("missing EXE", missing_exe))

    missing_portable = _release_fixture(root / "missing-portable", RC_TAG)
    (missing_portable["release"] / "assets" / bundle._zip_name(RC_TAG)).unlink()
    cases.append(("missing portable", missing_portable))

    invalid_exe = _release_fixture(root / "invalid-exe", RC_TAG)
    (invalid_exe["release"] / "assets" / bundle.EXE_NAME).write_bytes(b"NO")
    cases.append(("EXE without MZ", invalid_exe))

    empty_exe = _release_fixture(root / "empty-exe", RC_TAG)
    (empty_exe["release"] / "assets" / bundle.EXE_NAME).write_bytes(b"")
    cases.append(("empty asset", empty_exe))

    missing_notes = _release_fixture(root / "missing-notes", RC_TAG)
    (missing_notes["release"] / bundle.NOTES_NAME).unlink()
    cases.append(("missing notes", missing_notes))

    missing_legal = _release_fixture(root / "missing-legal", RC_TAG)
    Path(missing_legal["legal"]).unlink()
    cases.append(("missing legal payload", missing_legal))

    missing_aria2 = _release_fixture(root / "missing-aria2", RC_TAG)
    _source_path(missing_aria2, RC_TAG, "aria2").unlink()
    cases.append(("missing aria2 source", missing_aria2))

    missing_ffmpeg = _release_fixture(root / "missing-ffmpeg", RC_TAG)
    _source_path(missing_ffmpeg, RC_TAG, "ffmpeg").unlink()
    cases.append(("missing FFmpeg source", missing_ffmpeg))

    wrong_source_name = _release_fixture(root / "wrong-source-name", RC_TAG)
    _source_path(wrong_source_name, RC_TAG, "aria2").rename(
        Path(wrong_source_name["sources"]) / "aria2-source.zip"
    )
    cases.append(("source asset filename mismatch", wrong_source_name))

    mismatched_legal = _release_fixture(root / "mismatched-legal", RC_TAG)
    _mutate_first_zip_entry(Path(mismatched_legal["legal"]))
    cases.append(("mismatched portable and legal payload", mismatched_legal))

    for index, (label, fixture) in enumerate(cases):
        _expect_bundle_error(
            bundle,
            label,
            lambda fixture=fixture, index=index: _create_bundle(
                bundle,
                fixture,
                root / f"invalid-output-{index}",
                RC_TAG,
                True,
            ),
        )


def _test_bundle_mutations(bundle, root: Path) -> None:
    fixture = _release_fixture(root / "mutation-fixture", RC_TAG)
    pristine = root / "mutation-pristine"
    _create_bundle(bundle, fixture, pristine, RC_TAG, True)

    mutations = [
        ("extra file", lambda target: (target / "extra.txt").write_bytes(b"extra")),
        ("asset tamper", lambda target: _append(target / "assets" / bundle.EXE_NAME)),
        ("SBOM tamper", lambda target: _append(target / "assets" / _sbom_name(RC_TAG))),
        ("missing SBOM asset", lambda target: (target / "assets" / _sbom_name(RC_TAG)).unlink()),
        ("notes tamper", lambda target: _append(target / bundle.NOTES_NAME)),
        ("stale release notes checksum", lambda target: _set_notes_checksum(target, "0" * 64)),
        ("missing release notes checksum", _remove_notes_checksum),
        ("duplicate release notes checksum", _duplicate_notes_checksum),
        ("malformed release notes checksum", _malform_notes_checksum),
        ("wrong release notes filename", _rename_notes_checksum_target),
        ("checksum/manifest mismatch", lambda target: _set_notes_checksum_from_asset(target, bundle.EXE_NAME)),
        ("checksum tamper", lambda target: _append(target / "assets" / bundle.CHECKSUM_NAME)),
        ("v1 manifest", lambda target: _manifest_field(target, "schema_version", 1)),
        ("manifest format", lambda target: _manifest_field(target, "bundle_format", "wrong")),
        ("release ready mutation", lambda target: _manifest_field(target, "release_ready", True)),
        ("legal certification mutation", lambda target: _manifest_field(target, "legal_compliance_certified", True)),
        ("source certification mutation", lambda target: _manifest_field(target, "source_availability_certified", True)),
        ("blocker mutation", lambda target: _manifest_field(target, "release_blockers", [])),
        ("wrong file size", lambda target: _record_field(target, "assets", 0, "size", 1)),
        ("wrong asset hash", lambda target: _record_field(target, "assets", 0, "sha256", "0" * 64)),
        ("role mutation", lambda target: _record_field(target, "assets", 0, "role", "portable-package")),
        ("duplicate role", _duplicate_role),
        ("wrong checksum hash", lambda target: _record_field(target, "checksum_file", None, "sha256", "0" * 64)),
        ("wrong notes hash", lambda target: _record_field(target, "release_notes", None, "sha256", "0" * 64)),
        ("duplicate manifest asset", _duplicate_asset),
        ("unknown manifest field", lambda target: _manifest_field(target, "unexpected", True)),
        ("traversal filename", lambda target: _record_field(target, "assets", 0, "name", "../bad")),
        ("renamed asset", _rename_asset),
        ("malformed JSON", lambda target: (target / bundle.MANIFEST_NAME).write_bytes(b"{\n")),
        ("BOM", lambda target: _prefix(target / bundle.MANIFEST_NAME, b"\xef\xbb\xbf")),
        ("CRLF checksum", lambda target: _to_crlf(target / "assets" / bundle.CHECKSUM_NAME)),
    ]
    for index, (label, mutate) in enumerate(mutations):
        target = root / f"mutation-{index}"
        shutil.copytree(pristine, target)
        shutil.copy2(_sbom_input_path(pristine), _sbom_input_path(target))
        mutate(target)
        _expect_verify_error(bundle, label, target)

    bad_metadata = (
        ("wrong tag", STABLE_TAG, SOURCE_COMMIT, CONTROL_COMMIT, True),
        ("wrong source commit", RC_TAG, "3" * 40, CONTROL_COMMIT, True),
        ("wrong control commit", RC_TAG, SOURCE_COMMIT, "3" * 40, True),
        ("wrong prerelease", RC_TAG, SOURCE_COMMIT, CONTROL_COMMIT, False),
    )
    for label, tag, source_commit, control_commit, prerelease in bad_metadata:
        _expect_bundle_error(
            bundle,
            label,
            lambda tag=tag, source_commit=source_commit, control_commit=control_commit, prerelease=prerelease: bundle.verify_bundle(
                bundle_root=pristine,
                tag=tag,
                source_commit=source_commit,
                control_commit=control_commit,
                prerelease=prerelease,
                policy=POLICY_PATH,
                asset_contract=V3_CONTRACT_PATH,
                legal_payload_path=pristine / "assets" / _legal_name(tag),
                source_assets_root=pristine / "assets",
                sbom_input=_sbom_input_path(pristine),
                require_release_ready=False,
            ),
        )


def _test_source_zip_mutations(bundle, root: Path) -> None:
    duplicate = _release_fixture(root / "duplicate-source", RC_TAG)
    _write_duplicate_zip(_source_path(duplicate, RC_TAG, "aria2"))
    _expect_bundle_error(
        bundle,
        "duplicate source ZIP entry",
        lambda: _create_bundle(bundle, duplicate, root / "duplicate-source-output", RC_TAG, True),
    )

    traversal = _release_fixture(root / "traversal-source", RC_TAG)
    _write_source_zip(_source_path(traversal, RC_TAG, "ffmpeg"), extra_name="../escape.txt")
    _expect_bundle_error(
        bundle,
        "source ZIP traversal",
        lambda: _create_bundle(bundle, traversal, root / "traversal-source-output", RC_TAG, True),
    )


def _test_nonempty_output(bundle, root: Path) -> None:
    fixture = _release_fixture(root / "nonempty-fixture", RC_TAG)
    output = root / "nonempty-output"
    output.mkdir()
    (output / "sentinel.txt").write_bytes(b"preserve")
    _expect_bundle_error(
        bundle,
        "non-empty bundle output target",
        lambda: _create_bundle(bundle, fixture, output, RC_TAG, True),
    )
    _require((output / "sentinel.txt").read_bytes() == b"preserve", "non-empty output changed")


def _test_symlink_input(bundle, root: Path) -> None:
    fixture = _release_fixture(root / "symlink-fixture", RC_TAG)
    exe = Path(fixture["release"]) / "assets" / bundle.EXE_NAME
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
            lambda: _create_bundle(bundle, fixture, root / "symlink-output", RC_TAG, True),
        )


def _release_fixture(root: Path, tag: str) -> dict[str, Path | str]:
    release = root / "release"
    assets = release / "assets"
    sources = root / "source-assets"
    assets.mkdir(parents=True)
    sources.mkdir()
    (assets / "Youtube.Downloaderbs.exe").write_bytes(b"MZsynthetic non-executable fixture\n")
    portable = assets / f"Youtube-Downloaderbs-{tag}.zip"
    _write_zip(
        portable,
        {
            "SYNTHETIC.txt": b"synthetic portable package; not for distribution\n",
            "Youtube.Downloaderbs.exe": b"MZsynthetic packaged application fixture\n",
            "app.pyz": b"synthetic PyInstaller application archive\n",
            "data/bin/aria2c.exe": b"MZsynthetic aria2 fixture\n",
            "data/bin/deno.exe": b"MZsynthetic deno fixture\n",
            "data/bin/ffmpeg.exe": b"MZsynthetic ffmpeg fixture\n",
            "data/bin/ffprobe.exe": b"MZsynthetic ffprobe fixture\n",
            "data/bin/yt-dlp.exe": b"MZsynthetic yt-dlp fixture\n",
            "native/_tkinter.pyd": b"MZsynthetic native extension fixture\n",
            "packages/demo/__init__.py": b"VERSION = '1.0.0'\n",
            "python311.dll": b"MZsynthetic Python runtime fixture\n",
        },
    )
    notes = release / "RELEASE_NOTES.md"
    _write_release_notes(notes, portable)
    legal = assets / _legal_name(tag)
    legal_builder.create_release_legal_payload(
        control_root=REPO_ROOT,
        portable_zip=portable,
        output_zip=legal,
        release_notes=notes,
        tag=tag,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
    )
    _write_source_zip(sources / _source_name(tag, "aria2"))
    _write_source_zip(sources / _source_name(tag, "ffmpeg"))
    sbom_input = root / "synthetic-release-sbom-input.json"
    evidence = sbom_fixture.build_synthetic_input(
        release_root=release,
        legal_payload_path=legal,
        source_assets_root=sources,
        tag=tag,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=tag != STABLE_TAG,
        policy=POLICY_PATH,
        asset_contract=V3_CONTRACT_PATH,
    )
    sbom_input.write_bytes(release_sbom.canonical_json_bytes(evidence))
    return {
        "release": release,
        "sources": sources,
        "legal": legal,
        "sbom_input": sbom_input,
    }


def _write_source_zip(path: Path, *, extra_name: str | None = None) -> None:
    statement = (
        b"synthetic fixture\n"
        b"not a real source kit\n"
        b"not for distribution\n"
    )
    manifest = {
        "fixture": "synthetic fixture",
        "real_source_kit": False,
        "distribution_allowed": False,
        "notice": "not a real source kit; not for distribution",
    }
    entries = {
        "SOURCE_MANIFEST.json": (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        "SYNTHETIC_SOURCE_FIXTURE.txt": statement,
    }
    if extra_name is not None:
        entries[extra_name] = b"unsafe fixture path\n"
    _write_zip(path, entries)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _write_duplicate_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with mock.patch.object(zipfile.ZipFile, "_writecheck", lambda self, zinfo: None):
            archive.writestr("SYNTHETIC_SOURCE_FIXTURE.txt", b"synthetic fixture\n")
            archive.writestr("SYNTHETIC_SOURCE_FIXTURE.txt", b"not for distribution\n")


def _mutate_first_zip_entry(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    first = sorted(entries)[0]
    entries[first] += b"tamper\n"
    path.unlink()
    _write_zip(path, entries)


def _create_bundle(bundle, fixture, output: Path, tag: str, prerelease: bool) -> None:
    shutil.copy2(Path(fixture["sbom_input"]), _sbom_input_path(output))
    bundle.create_bundle(
        release_root=Path(fixture["release"]),
        bundle_root=output,
        tag=tag,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=prerelease,
        policy=POLICY_PATH,
        asset_contract=V3_CONTRACT_PATH,
        legal_payload_path=Path(fixture["legal"]),
        source_assets_root=Path(fixture["sources"]),
        sbom_input=_sbom_input_path(output),
    )


def _verify_bundle(bundle, root: Path, tag: str, prerelease: bool, *, require_ready: bool) -> None:
    bundle.verify_bundle(
        bundle_root=root,
        tag=tag,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
        prerelease=prerelease,
        policy=POLICY_PATH,
        asset_contract=V3_CONTRACT_PATH,
        legal_payload_path=root / "assets" / _legal_name(tag),
        source_assets_root=root / "assets",
        sbom_input=_sbom_input_path(root),
        require_release_ready=require_ready,
    )


def _create_cli_arguments(fixture, output: Path, tag: str, prerelease: bool) -> tuple[str, ...]:
    return (
        "--release-root", str(fixture["release"]),
        "--bundle-root", str(output),
        "--tag", tag,
        "--source-commit", SOURCE_COMMIT,
        "--control-commit", CONTROL_COMMIT,
        "--prerelease", str(prerelease).lower(),
        "--policy", str(POLICY_PATH),
        "--asset-contract", str(V3_CONTRACT_PATH),
        "--legal-payload", str(fixture["legal"]),
        "--source-assets-root", str(fixture["sources"]),
        "--sbom-input", str(fixture["sbom_input"]),
    )


def _v2_create_cli_arguments(fixture, output: Path, tag: str, prerelease: bool) -> tuple[str, ...]:
    return (
        "--release-root", str(fixture["release"]),
        "--bundle-root", str(output),
        "--tag", tag,
        "--source-commit", SOURCE_COMMIT,
        "--control-commit", CONTROL_COMMIT,
        "--prerelease", str(prerelease).lower(),
        "--policy", str(POLICY_PATH),
        "--asset-contract", str(V2_CONTRACT_PATH),
        "--legal-payload", str(fixture["legal"]),
        "--source-assets-root", str(fixture["sources"]),
    )


def _v2_verify_cli_arguments(
    root: Path,
    tag: str,
    prerelease: bool,
    *,
    require_ready: bool,
) -> tuple[str, ...]:
    return (
        "--bundle-root", str(root),
        "--tag", tag,
        "--source-commit", SOURCE_COMMIT,
        "--control-commit", CONTROL_COMMIT,
        "--prerelease", str(prerelease).lower(),
        "--policy", str(POLICY_PATH),
        "--asset-contract", str(V2_CONTRACT_PATH),
        "--legal-payload", str(root / "assets" / _legal_name(tag)),
        "--source-assets-root", str(root / "assets"),
        "--require-release-ready", str(require_ready).lower(),
    )


def _verify_cli_arguments(
    root: Path,
    tag: str,
    prerelease: bool,
    *,
    sbom_input: Path,
    require_ready: bool,
) -> tuple[str, ...]:
    return (
        "--bundle-root", str(root),
        "--tag", tag,
        "--source-commit", SOURCE_COMMIT,
        "--control-commit", CONTROL_COMMIT,
        "--prerelease", str(prerelease).lower(),
        "--policy", str(POLICY_PATH),
        "--asset-contract", str(V3_CONTRACT_PATH),
        "--legal-payload", str(root / "assets" / _legal_name(tag)),
        "--source-assets-root", str(root / "assets"),
        "--sbom-input", str(sbom_input),
        "--require-release-ready", str(require_ready).lower(),
    )


def _validate_generated_files(root: Path, tag: str, prerelease: bool) -> None:
    asset_names = {
        "Youtube.Downloaderbs.exe",
        f"Youtube-Downloaderbs-{tag}.zip",
        _legal_name(tag),
        _source_name(tag, "aria2"),
        _source_name(tag, "ffmpeg"),
        f"Youtube-Downloaderbs-{tag}.spdx.json",
    }
    expected = {"RELEASE_MANIFEST.json", "RELEASE_NOTES.md", "assets/SHA256SUMS.txt"}
    expected.update(f"assets/{name}" for name in asset_names)
    _require(set(_tree_bytes(root)) == expected, "generated file set is invalid")
    checksum = (root / "assets" / "SHA256SUMS.txt").read_bytes()
    _require(not checksum.startswith(b"\xef\xbb\xbf"), "checksum BOM")
    _require(b"\r" not in checksum and checksum.endswith(b"\n"), "checksum EOL")
    lines = checksum.decode("ascii").splitlines()
    _require(len(lines) == 6, "checksum line count")
    names = [line.split("  ", 1)[1] for line in lines]
    _require(names == sorted(names) == sorted(asset_names), "checksum asset set or order")
    manifest_bytes = (root / "RELEASE_MANIFEST.json").read_bytes()
    _require(b"\r" not in manifest_bytes and manifest_bytes.endswith(b"\n"), "manifest EOL")
    manifest = json.loads(manifest_bytes)
    _require(manifest["schema_version"] == 3, "manifest schema")
    _require(manifest["bundle_format"] == "s9h-release-bundle-v3", "manifest format")
    _require(manifest["release_tag"] == tag, "manifest tag")
    _require(manifest["prerelease"] is prerelease, "manifest prerelease")
    _require(manifest["source_commit"] == SOURCE_COMMIT, "manifest source commit")
    _require(manifest["control_commit"] == CONTROL_COMMIT, "manifest control commit")
    _require(manifest["release_ready"] is False, "blocked policy readiness")
    _require(manifest["legal_compliance_certified"] is False, "legal certification")
    _require(manifest["source_availability_certified"] is False, "source certification")
    _require(bool(manifest["release_blockers"]), "release blockers")
    roles = [item["role"] for item in manifest["assets"]]
    _require(
        sorted(roles) == sorted(
            [
                "application-executable",
                "portable-package",
                "legal-payload",
                "aria2-source",
                "ffmpeg-source",
                "release-sbom",
            ]
        ),
        "asset roles",
    )
    _require("timestamp" not in manifest_bytes.decode().casefold(), "manifest timestamp")
    portable_name = f"Youtube-Downloaderbs-{tag}.zip"
    recorded, _, _ = legal_builder.verifier.parse_release_notes_checksum(
        (root / "RELEASE_NOTES.md").read_bytes(),
        portable_name,
    )
    portable_record = next(item for item in manifest["assets"] if item["role"] == "portable-package")
    _require(recorded == portable_record["sha256"], "release notes/manifest portable checksum")


def _validate_v2_generated_files(root: Path, tag: str, prerelease: bool) -> None:
    asset_names = {
        "Youtube.Downloaderbs.exe",
        f"Youtube-Downloaderbs-{tag}.zip",
        _legal_name(tag),
        _source_name(tag, "aria2"),
        _source_name(tag, "ffmpeg"),
    }
    expected = {"RELEASE_MANIFEST.json", "RELEASE_NOTES.md", "assets/SHA256SUMS.txt"}
    expected.update(f"assets/{name}" for name in asset_names)
    _require(set(_tree_bytes(root)) == expected, "historical v2 file set changed")
    checksum_lines = (root / "assets" / "SHA256SUMS.txt").read_text(
        encoding="ascii"
    ).splitlines()
    _require(len(checksum_lines) == 5, "historical v2 checksum count changed")
    _require(
        [line.split("  ", 1)[1] for line in checksum_lines] == sorted(asset_names),
        "historical v2 checksum layout changed",
    )
    manifest = _manifest(root)
    _require(manifest["schema_version"] == 2, "historical v2 manifest schema changed")
    _require(
        manifest["bundle_format"] == "s9h-release-bundle-v2",
        "historical v2 manifest format changed",
    )
    _require(manifest["prerelease"] is prerelease, "historical v2 prerelease changed")
    roles = {item["role"] for item in manifest["assets"]}
    _require(
        roles
        == {
            "application-executable",
            "portable-package",
            "legal-payload",
            "aria2-source",
            "ffmpeg-source",
        },
        "historical v2 asset roles changed",
    )
    _require("release-sbom" not in roles, "historical v2 claims SBOM integration")


def _source_path(fixture, tag: str, component: str) -> Path:
    return Path(fixture["sources"]) / _source_name(tag, component)


def _write_release_notes(path: Path, portable: Path) -> None:
    path.write_bytes(
        (
            "# Synthetic release fixture\n\n"
            "## Build checksums\n\n"
            f"- `Youtube.Downloaderbs.exe`: `{'a' * 64}`\n"
            f"- `{portable.name}`: `{legal_builder.verifier.sha256_file(portable)}`\n"
        ).encode("ascii")
    )


def _set_notes_checksum(root: Path, checksum: str) -> None:
    notes = root / "RELEASE_NOTES.md"
    portable_name = _portable_name_from_manifest(root)
    _, start, end = legal_builder.verifier.parse_release_notes_checksum(notes.read_bytes(), portable_name)
    raw = notes.read_bytes()
    notes.write_bytes(raw[:start] + checksum.encode("ascii") + raw[end:])
    _sync_notes_record(root)


def _remove_notes_checksum(root: Path) -> None:
    notes = root / "RELEASE_NOTES.md"
    portable_name = _portable_name_from_manifest(root).encode("ascii")
    notes.write_bytes(
        b"".join(line for line in notes.read_bytes().splitlines(keepends=True) if portable_name not in line)
    )
    _sync_notes_record(root)


def _duplicate_notes_checksum(root: Path) -> None:
    notes = root / "RELEASE_NOTES.md"
    portable_name = _portable_name_from_manifest(root)
    checksum, _, _ = legal_builder.verifier.parse_release_notes_checksum(notes.read_bytes(), portable_name)
    notes.write_bytes(notes.read_bytes() + f"- `{portable_name}`: `{checksum}`\n".encode("ascii"))
    _sync_notes_record(root)


def _malform_notes_checksum(root: Path) -> None:
    notes = root / "RELEASE_NOTES.md"
    portable_name = _portable_name_from_manifest(root)
    _, start, end = legal_builder.verifier.parse_release_notes_checksum(notes.read_bytes(), portable_name)
    raw = notes.read_bytes()
    notes.write_bytes(raw[:start] + b"f" * 63 + raw[end:])
    _sync_notes_record(root)


def _rename_notes_checksum_target(root: Path) -> None:
    notes = root / "RELEASE_NOTES.md"
    portable_name = _portable_name_from_manifest(root)
    notes.write_bytes(notes.read_bytes().replace(portable_name.encode("ascii"), b"different.zip"))
    _sync_notes_record(root)


def _set_notes_checksum_from_asset(root: Path, asset_name: str) -> None:
    checksum = next(item["sha256"] for item in _manifest(root)["assets"] if item["name"] == asset_name)
    _set_notes_checksum(root, checksum)


def _portable_name_from_manifest(root: Path) -> str:
    return next(item["name"] for item in _manifest(root)["assets"] if item["role"] == "portable-package")


def _sync_notes_record(root: Path) -> None:
    notes = root / "RELEASE_NOTES.md"
    manifest = _manifest(root)
    manifest["release_notes"] = {
        "name": "RELEASE_NOTES.md",
        "size": notes.stat().st_size,
        "sha256": legal_builder.verifier.sha256_file(notes),
    }
    _write_manifest(root, manifest)


def _legal_name(tag: str) -> str:
    return f"Youtube-Downloaderbs-{tag}-legal.zip"


def _source_name(tag: str, component: str) -> str:
    return f"Youtube-Downloaderbs-{tag}-{component}-source.zip"


def _sbom_name(tag: str) -> str:
    return f"Youtube-Downloaderbs-{tag}.spdx.json"


def _sbom_input_path(bundle_root: Path) -> Path:
    return bundle_root.parent / f"{bundle_root.name}.sbom-input.json"


def _run_cli(*args: str) -> str:
    result = _run_cli_result(*args)
    result.check_returncode()
    _require(not result.stderr, "bundle CLI wrote stderr on success")
    return result.stdout.strip()


def _run_cli_result(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


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
        lambda: _verify_bundle(bundle, root, RC_TAG, True, require_ready=False),
    )


def _expect_bundle_error(bundle, label: str, callback) -> None:
    try:
        callback()
    except (bundle.BundleError, legal_builder.verifier.LegalPayloadError, zipfile.BadZipFile):
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


def _duplicate_role(root: Path) -> None:
    manifest = _manifest(root)
    manifest["assets"][1]["role"] = manifest["assets"][0]["role"]
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
