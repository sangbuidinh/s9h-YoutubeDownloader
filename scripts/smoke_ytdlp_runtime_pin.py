from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026.08.18.122307"
SHA256 = "652e154bce7170070d0f26415c9a3c35c121f5a7903cb8cde6d31c4577517fb9"
SHA256_UPPER = SHA256.upper()
BINARY_REPOSITORY = "yt-dlp/yt-dlp-nightly-builds"
BINARY_URL = (
    "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/download/"
    f"{VERSION}/yt-dlp.exe"
)
SOURCE_REPOSITORY = "yt-dlp/yt-dlp"
SOURCE_COMMIT = "5d5b634d8e6b41dc2891847a5ea7a5a3f569a28c"
LICENSE_PATH = f"legal/licenses/yt-dlp-{VERSION}-UNLICENSE.txt"
LICENSE_BLOB_SHA1 = "68a49daad8ff7e35068f2b7a97d643aab440eaec"

PATHS = (
    "VERSION",
    ".github/workflows/release-v1.3.2.yml",
    "scripts/build_release_v1_3_2.ps1",
    "scripts/build_release_v1_3_1.ps1",
    "scripts/create_synthetic_release_sbom_input.py",
    "scripts/verify_legal_notices.py",
    "scripts/verify_release_legal_payload.py",
    "legal/components.json",
    "legal/release-assets-v2.json",
    "legal/release-assets-v3.json",
    "legal/README.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/release_notes_v1.3.1.md",
    LICENSE_PATH,
)


class RuntimePinError(AssertionError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimePinError(message)


def _blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def _text(files: dict[str, bytes], path: str) -> str:
    raw = files[path]
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{path} contains a BOM")
    _require(b"\r" not in raw, f"{path} contains CR bytes")
    return raw.decode("utf-8")


def _validate(files: dict[str, bytes]) -> None:
    _require(set(files) == set(PATHS), "runtime pin fixture paths changed")
    _require(_text(files, "VERSION").strip() == "1.3.2", "active application version changed")

    build = _text(files, "scripts/build_release_v1_3_2.ps1")
    for required in (
        '$ReleaseVersion = "1.3.2"',
        f'$YtDlpUrl = "{BINARY_URL}"',
        f'$YtDlpSha256 = "{SHA256_UPPER}"',
        f'if ($YtDlpVersion -ne "{VERSION}")',
        r'docs\release_notes_v1.3.2.md',
    ):
        _require(required in build, f"active release build pin missing: {required}")
    _require("2026.03.17" not in build, "active release build retained the old yt-dlp pin")
    _require("/releases/latest/" not in build, "active release build uses a mutable asset URL")
    runtime_cleanup = "Remove-Item -LiteralPath $TempRoot -Recurse -Force"
    _require(runtime_cleanup in build, "active release build does not clean verified runtime archives")
    _require(
        build.index(runtime_cleanup) < build.index('Write-Host "== Source validation =="'),
        "active release build cleans runtime archives after source validation",
    )

    workflow = _text(files, ".github/workflows/release-v1.3.2.yml")
    for required in (
        "name: Release v1.3.2",
        "group: release-v1.3.2",
        "--tag v1.3.2",
        "ref: v1.3.2",
        r".\scripts\build_release_v1_3_2.ps1 -PreparePinnedRuntime",
    ):
        _require(required in workflow, f"active release workflow pin missing: {required}")

    components = json.loads(_text(files, "legal/components.json"))
    matches = [item for item in components["components"] if item.get("id") == "yt-dlp"]
    _require(len(matches) == 1, "yt-dlp legal component is not unique")
    component = matches[0]
    expected = {
        "version": VERSION,
        "upstream_repository": SOURCE_REPOSITORY,
        "upstream_ref": SOURCE_COMMIT,
        "upstream_license_path": "LICENSE",
        "upstream_license_blob_sha1": LICENSE_BLOB_SHA1,
        "local_license_path": LICENSE_PATH,
    }
    for field, value in expected.items():
        _require(component.get(field) == value, f"yt-dlp legal {field} drifted")
    notes = "\n".join(component.get("notes", ()))
    for required in (BINARY_REPOSITORY, VERSION, "yt-dlp.exe", SHA256, SOURCE_REPOSITORY, SOURCE_COMMIT):
        _require(required in notes, f"yt-dlp provenance note missing: {required}")

    for path in ("legal/release-assets-v2.json", "legal/release-assets-v3.json"):
        contract = json.loads(_text(files, path))
        payload = contract["legal_payload_files"]
        _require(payload.count(LICENSE_PATH) == 1, f"{path} active yt-dlp license is not unique")
        _require(
            "legal/licenses/yt-dlp-2026.03.17-UNLICENSE.txt" not in payload,
            f"{path} retained the historical license in the active payload",
        )

    notices = _text(files, "THIRD_PARTY_NOTICES.md")
    for required in (BINARY_REPOSITORY, VERSION, SHA256, SOURCE_REPOSITORY, SOURCE_COMMIT, LICENSE_PATH):
        _require(required in notices, f"active third-party notice missing: {required}")
    legal_readme = _text(files, "legal/README.md")
    for required in (BINARY_REPOSITORY, VERSION, SOURCE_REPOSITORY, SOURCE_COMMIT):
        _require(required in legal_readme, f"legal README provenance missing: {required}")

    license_bytes = files[LICENSE_PATH]
    _require(_blob_sha1(license_bytes) == LICENSE_BLOB_SHA1, "active yt-dlp license bytes drifted")

    for path in (
        "scripts/create_synthetic_release_sbom_input.py",
        "scripts/verify_legal_notices.py",
        "scripts/verify_release_legal_payload.py",
    ):
        text = _text(files, path)
        _require(VERSION in text, f"active verifier metadata missing from {path}")
    _require(LICENSE_PATH in _text(files, "scripts/verify_release_legal_payload.py"), "legal payload verifier license drifted")

    historical_build = _text(files, "scripts/build_release_v1_3_1.ps1")
    historical_notes = _text(files, "docs/release_notes_v1.3.1.md")
    for historical in (historical_build, historical_notes):
        _require("2026.03.17" in historical, "v1.3.1 historical runtime record changed")
    _require(
        "3DB811B366B2DA47337D2FCFDFE5BBD9A258DAD3F350C54974F005DF115A1545"
        in historical_build,
        "v1.3.1 historical yt-dlp hash changed",
    )


def _files() -> dict[str, bytes]:
    return {path: (REPO_ROOT / path).read_bytes() for path in PATHS}


def _expect_rejection(label: str, mutation) -> None:
    files = copy.deepcopy(_files())
    mutation(files)
    try:
        _validate(files)
    except RuntimePinError:
        return
    raise AssertionError(f"runtime pin mutation was accepted: {label}")


def _replace(files: dict[str, bytes], path: str, old: bytes, new: bytes) -> None:
    _require(old in files[path], f"mutation source missing: {path}")
    files[path] = files[path].replace(old, new, 1)


def main() -> int:
    _validate(_files())
    mutations = (
        (
            "release version drift",
            lambda files: _replace(files, "scripts/build_release_v1_3_2.ps1", VERSION.encode(), b"2026.08.18.122308"),
        ),
        (
            "release hash drift",
            lambda files: _replace(files, "scripts/build_release_v1_3_2.ps1", SHA256_UPPER.encode(), ("0" * 64).encode()),
        ),
        (
            "source commit drift",
            lambda files: _replace(files, "legal/components.json", SOURCE_COMMIT.encode(), ("1" * 40).encode()),
        ),
        (
            "binary provenance drift",
            lambda files: _replace(files, "legal/components.json", BINARY_REPOSITORY.encode(), b"yt-dlp/yt-dlp"),
        ),
        (
            "v2 active license payload drift",
            lambda files: _replace(files, "legal/release-assets-v2.json", LICENSE_PATH.encode(), b"legal/licenses/yt-dlp-wrong.txt"),
        ),
        (
            "v3 active license payload drift",
            lambda files: _replace(files, "legal/release-assets-v3.json", LICENSE_PATH.encode(), b"legal/licenses/yt-dlp-wrong.txt"),
        ),
        (
            "license bytes drift",
            lambda files: files.__setitem__(LICENSE_PATH, files[LICENSE_PATH] + b"drift\n"),
        ),
        (
            "pre-smoke runtime archive cleanup drift",
            lambda files: _replace(
                files,
                "scripts/build_release_v1_3_2.ps1",
                b"Remove-Item -LiteralPath $TempRoot -Recurse -Force",
                b"Write-Host 'runtime temp retained'",
            ),
        ),
    )
    for label, mutation in mutations:
        _expect_rejection(label, mutation)
    print("yt-dlp runtime pin smoke passed: 1 positive contract, 8 negative drift mutations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
