from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import source_compliance as compliance
import verify_release_legal_gate as gate


REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_PATH = REPO_ROOT / compliance.OWNER_PATH
POLICY_PATH = REPO_ROOT / "legal" / "release-policy.json"


def main() -> int:
    owner = compliance.load_owner(OWNER_PATH)
    aria = owner["kits"][0]
    _assert(aria["status"] == "ready" and aria["source_asset"]["sha256"], "aria2 source owner is not sealed")
    gmp = next(item for item in aria["identities"] if item["component_id"] == "gmp")
    _assert(gmp["identity_type"] == "release-archive" and gmp["immutable_ref"] is None, "GMP identity is not source-control neutral")
    _run_owner_mutations(owner)
    _run_ready_gate_matrix(owner)
    print("source compliance and ready-state gate smoke tests passed")
    return 0


def _run_owner_mutations(owner: dict) -> None:
    mutations = (
        ("missing archive hash", lambda value: value["kits"][0]["identities"][0].__setitem__("archive_sha256", None)),
        ("zero archive hash", lambda value: value["kits"][0]["identities"][0].__setitem__("archive_sha256", "0" * 64)),
        ("mutable Git ref", lambda value: value["kits"][0]["identities"][0].__setitem__("immutable_ref", "main")),
        ("fabricated release archive ref", lambda value: next(item for item in value["kits"][0]["identities"] if item["component_id"] == "gmp").__setitem__("immutable_ref", "fake-git-sha")),
        ("ready blocker", lambda value: value["kits"][0]["blockers"].append("synthetic-blocker")),
        ("ready unresolved input", lambda value: value["kits"][0]["identities"][0].__setitem__("resolved", False)),
        ("local path", lambda value: value["kits"][0]["identities"][0].__setitem__("evidence", "C:\\private\\source")),
        ("secret-like data", lambda value: value["kits"][0]["identities"][0].__setitem__("evidence", "ghp_" + "A" * 32)),
    )
    for label, mutation in mutations:
        candidate = copy.deepcopy(owner)
        mutation(candidate)
        try:
            compliance.validate_owner(candidate)
        except compliance.SourceComplianceError:
            continue
        raise AssertionError(f"source owner mutation was accepted: {label}")


def _run_ready_gate_matrix(source_owner: dict) -> None:
    # The ready fixture must exercise the real legal payload contract, never a
    # nonempty ZIP stand-in. Import lazily to keep the shared ZIP helper usable.
    import smoke_release_ready_state
    smoke_release_ready_state.main()


def _write_synthetic_source_asset(root: Path, kit: dict) -> dict[str, object]:
    notice = f"synthetic {kit['id']} source fixture; not for distribution\n".encode()
    files = {"NOTICE.txt": (notice, "notice"), "build/SYNTHETIC.txt": (notice, "build-script"),
             "licenses/SYNTHETIC.txt": (notice, "license")}
    for identity in kit["identities"]:
        data = f"synthetic archive fixture for {identity['component_id']}; not for distribution\n".encode()
        identity["archive_size"] = len(data)
        identity["archive_sha256"] = hashlib.sha256(data).hexdigest()
        files["sources/" + identity["archive_filename"]] = (data, "source-archive")
        if "runtime_build" in kit:
            # Caller scopes this mutation with mock.patch.dict. Never a CLI
            # bypass: synthetic bytes cannot satisfy the production input pins.
            import project_ffmpeg
            project_ffmpeg.INPUTS[identity["component_id"]] = {
                **project_ffmpeg.INPUTS[identity["component_id"]],
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            }
    if "runtime_build" in kit:
        runtime = kit["runtime_build"]
        for recipe in runtime["recipe_files"]:
            data = (REPO_ROOT / recipe["name"]).read_bytes()
            recipe.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
            files["build/" + recipe["name"]] = (data, "build-script")
        for name in ("FFmpeg-COPYING.LGPLv2.1", "FFmpeg-LICENSE.md", "LAME-COPYING", "LAME-LICENSE"):
            files["licenses/" + name] = (notice, "license")
        files["licenses/MinGW-w64-14.0.0-COPYING.txt"] = (
            (REPO_ROOT / "legal/licenses/MinGW-w64-14.0.0-COPYING.txt").read_bytes(), "license")
        files["BINARY_SOURCE_MAPPING.json"] = (compliance.canonical_json_bytes(runtime), "notice")
    records = [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "role": role}
               for name, (data, role) in sorted(files.items())]
    manifest = {
        "schema_version": 1,
        "package_id": kit["id"],
        "release_tag": "v1.3.2",
        "binary_package": kit["binary_package"],
        "source_identities": kit["identities"],
        "files": records,
        "non_claims": ["byte-identical-rebuild", "legal-advice", "reproducible-build"],
    }
    manifest_bytes = compliance.canonical_json_bytes(manifest)
    filename = f"Youtube-Downloaderbs-v1.3.2-{kit['id']}-source.zip"
    path = root / filename
    _write_zip(path, {"SOURCE_MANIFEST.json": manifest_bytes, **{name: data for name, (data, _) in files.items()}})
    return {
        "filename": filename,
        "size": path.stat().st_size,
        "sha256": compliance.sha256_file(path),
        "entry_count": len(files) + 1,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, compliance.FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = compliance.FIXED_FILE_MODE
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
