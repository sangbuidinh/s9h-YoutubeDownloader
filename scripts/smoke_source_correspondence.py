from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import verify_source_correspondence as verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
Mutation = Callable[[Path], None]


def main() -> int:
    correspondence, kits = verifier.verify_repository(REPO_ROOT)
    _assert(correspondence["baseline_commit"] == verifier.BASELINE_COMMIT, "baseline changed")
    _assert([item["id"] for item in correspondence["packages"]] == ["aria2", "ffmpeg"], "package IDs changed")
    _assert(all(item["source_kit_status"] == "not-ready" for item in correspondence["packages"]), "source kit became ready")
    _assert(correspondence["corresponding_source_complete"] is False, "source completion claim appeared")
    _assert(all(kit["status"] == "blocked" for kit in kits["kits"]), "source kit was unblocked")
    for package in correspondence["packages"]:
        expected = verifier.EXPECTED_PACKAGES[package["id"]]
        _assert(package["binary_package"]["sha256"] == expected["archive_sha256"], "archive hash changed")
        _assert(package["core_source"]["commit"] == expected["commit"], "source commit changed")
        _assert(all(item["limitation"] == verifier.PE_LIMITATION for item in package["pe_imports"]), "PE limitation missing")

    mutations: tuple[tuple[str, Mutation], ...] = (
        ("wrong FFmpeg archive hash", lambda root: _edit_correspondence(root, lambda d: _package(d, "ffmpeg")["binary_package"].update(sha256="0" * 64))),
        ("wrong aria2 archive hash", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["binary_package"].update(sha256="1" * 64))),
        ("wrong binary hash", lambda root: _edit_correspondence(root, lambda d: _package(d, "ffmpeg")["distributed_binaries"][0].update(sha256="2" * 64))),
        ("wrong source archive hash", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["core_source"].update(archive_sha256="3" * 64))),
        ("abbreviated source commit", lambda root: _edit_correspondence(root, lambda d: _package(d, "ffmpeg")["core_source"].update(commit="38b88335f9"))),
        ("mutable source ref", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["core_source"].update(commit="main"))),
        ("duplicate package ID", _duplicate_package),
        ("unsorted packages", lambda root: _edit_correspondence(root, lambda d: d["packages"].reverse())),
        ("duplicate external component", _duplicate_external_component),
        ("guessed version without evidence", _guess_version_without_evidence),
        ("missing blocker", lambda root: _edit_correspondence(root, lambda d: _package(d, "ffmpeg").update(blockers=[]))),
        ("complete source true", lambda root: _edit_correspondence(root, lambda d: d.update(corresponding_source_complete=True))),
        ("compliance true", lambda root: _edit_correspondence(root, lambda d: d.update(legal_compliance_certified=True))),
        ("release gate not fail-closed", lambda root: _edit_correspondence(root, lambda d: d.update(release_gate_status="open"))),
        ("local path", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["provider"].update(name="C:" + r"\Users\builder\aria2"))),
        ("timestamp", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["blockers"].append("Generated 2026-07-14T15:30"))),
        ("secret-like value", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["blockers"].append("S" + "ID=not-a-real-secret"))),
        ("missing PE import limitation", lambda root: _edit_correspondence(root, lambda d: _package(d, "ffmpeg")["pe_imports"][0].update(limitation=""))),
        ("missing package metadata", lambda root: _edit_correspondence(root, lambda d: _package(d, "aria2")["binary_package"].update(provider_metadata_files=[]))),
        ("malformed JSON", _malformed_json),
        ("BOM", _bom_json),
        ("CRLF", _crlf_json),
    )
    for label, mutation in mutations:
        _expect_failure(label, mutation)
    print("source correspondence smoke tests passed")
    return 0


def _copy_fixture(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "legal").mkdir()
    for relative in (
        "legal/source-correspondence.json",
        "legal/source-kit-requirements.json",
        "legal/release-policy.json",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "legal/README.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)


def _expect_failure(label: str, mutation: Mutation) -> None:
    with tempfile.TemporaryDirectory(prefix="source-correspondence-smoke-") as temp:
        root = Path(temp) / "repo"
        _copy_fixture(root)
        mutation(root)
        try:
            verifier.verify_repository(root)
        except (
            verifier.SourceCorrespondenceError,
            verifier.release_gate.ReleaseLegalGateError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            return
        raise AssertionError(f"mutation was not rejected: {label}")


def _load(root: Path) -> dict:
    return json.loads((root / verifier.CORRESPONDENCE_PATH).read_text(encoding="utf-8"))


def _write(root: Path, document: dict) -> None:
    (root / verifier.CORRESPONDENCE_PATH).write_bytes(verifier.canonical_json_bytes(document))


def _edit_correspondence(root: Path, mutation: Callable[[dict], None]) -> None:
    document = _load(root)
    mutation(document)
    _write(root, document)


def _package(document: dict, package_id: str) -> dict:
    return next(package for package in document["packages"] if package["id"] == package_id)


def _duplicate_package(root: Path) -> None:
    def mutate(document: dict) -> None:
        document["packages"].insert(1, copy.deepcopy(document["packages"][0]))

    _edit_correspondence(root, mutate)


def _duplicate_external_component(root: Path) -> None:
    def mutate(document: dict) -> None:
        components = _package(document, "aria2")["external_components"]
        components.insert(1, copy.deepcopy(components[0]))

    _edit_correspondence(root, mutate)


def _guess_version_without_evidence(root: Path) -> None:
    def mutate(document: dict) -> None:
        component = _package(document, "ffmpeg")["external_components"][0]
        component["version"] = "9.9.9"
        component["evidence"] = ["provider library name only"]

    _edit_correspondence(root, mutate)


def _malformed_json(root: Path) -> None:
    (root / verifier.CORRESPONDENCE_PATH).write_bytes(b"{not-json}\n")


def _bom_json(root: Path) -> None:
    path = root / verifier.CORRESPONDENCE_PATH
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _crlf_json(root: Path) -> None:
    path = root / verifier.CORRESPONDENCE_PATH
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
