from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path
from typing import Any

import prepare_release_bundle as release_bundle
import release_sbom


SYNTHETIC_CREATED_UTC = "2026-01-01T00:00:00Z"
SYNTHETIC_PORTABLE_FILES = {
    "SYNTHETIC.txt": "release",
    "Youtube.Downloaderbs.exe": "application",
    "app.pyz": "application",
    "data/bin/aria2c.exe": "aria2",
    "data/bin/deno.exe": "deno",
    "data/bin/ffmpeg.exe": "ffmpeg",
    "data/bin/ffprobe.exe": "ffprobe",
    "data/bin/yt-dlp.exe": "yt-dlp",
    "native/_tkinter.pyd": "python-runtime",
    "packages/demo/__init__.py": "python-package-demo",
    "python311.dll": "python-runtime",
}
PYINSTALLER_MEMBERS = [
    "app.pyz",
    "native/_tkinter.pyd",
    "packages/demo/__init__.py",
]


class SyntheticInputError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create explicit synthetic evidence for deterministic release SBOM tests"
    )
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--legal-payload", required=True, type=Path)
    parser.add_argument("--source-assets-root", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--control-commit", required=True)
    parser.add_argument("--prerelease", required=True)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--asset-contract", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        prerelease = release_bundle._parse_boolean(args.prerelease, "prerelease")
        evidence = build_synthetic_input(
            release_root=args.release_root,
            legal_payload_path=args.legal_payload,
            source_assets_root=args.source_assets_root,
            tag=args.tag,
            source_commit=args.source_commit,
            control_commit=args.control_commit,
            prerelease=prerelease,
            policy=args.policy,
            asset_contract=args.asset_contract,
        )
        if args.output.exists():
            raise SyntheticInputError("synthetic SBOM input output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(release_sbom.canonical_json_bytes(evidence))
        release_sbom.load_input(args.output)
        print("Synthetic release SBOM input created and verified")
    except (
        OSError,
        UnicodeError,
        release_bundle.BundleError,
        release_sbom.SbomError,
        SyntheticInputError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Synthetic release SBOM input error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_synthetic_input(
    *,
    release_root: Path,
    legal_payload_path: Path,
    source_assets_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
    policy: Path,
    asset_contract: Path,
) -> dict[str, Any]:
    release_bundle._validate_metadata(tag, source_commit, control_commit, prerelease)
    control = release_bundle._load_control_state(policy, asset_contract, tag)
    if not control["requires_sbom"] or control["bundle_format"] != release_bundle.V3_BUNDLE_FORMAT:
        raise SyntheticInputError("synthetic SBOM evidence requires the release assets v3 contract")
    release_root = release_bundle._require_directory_root(release_root, "release root")
    source_assets_root = release_bundle._require_directory_root(
        source_assets_root, "source assets root"
    )
    names = release_bundle._asset_names(tag, control["contract"])
    source_files = {
        names["application-executable"]: release_root / "assets" / names["application-executable"],
        names["portable-package"]: release_root / "assets" / names["portable-package"],
        names["legal-payload"]: legal_payload_path,
        names["aria2-source"]: source_assets_root / names["aria2-source"],
        names["ffmpeg-source"]: source_assets_root / names["ffmpeg-source"],
    }
    for name, path in source_files.items():
        allowed_root = release_bundle._allowed_root_for_input(
            name,
            release_root=release_root,
            source_assets_root=source_assets_root,
            legal_payload_path=legal_payload_path,
        )
        release_bundle._require_regular_input(path, allowed_root, name)

    assets = [
        release_bundle._asset_record(source_files[name], role)
        for role, name in sorted(names.items(), key=lambda item: item[1])
        if role != "release-sbom"
    ]
    final_artifacts = [
        {
            "path": item["name"],
            "role": item["role"],
            "size": item["size"],
            "sha256": item["sha256"],
            "component_id": (
                "application" if item["role"] == "application-executable" else "release"
            ),
            "unresolved_id": None,
        }
        for item in assets
    ]
    checksum_bytes = release_bundle._checksum_bytes(assets)
    notes_path = release_root / release_bundle.NOTES_NAME
    release_bundle._require_regular_input(notes_path, release_root, release_bundle.NOTES_NAME)
    release_manifest = {
        "schema_version": control["manifest_schema_version"],
        "bundle_format": control["bundle_format"],
        "release_tag": tag,
        "prerelease": prerelease,
        "source_commit": source_commit,
        "control_commit": control_commit,
        "release_ready": False,
        "legal_compliance_certified": False,
        "source_availability_certified": False,
        "assets": assets,
        "checksum_file": _bytes_record(release_bundle.CHECKSUM_NAME, checksum_bytes),
        "release_notes": release_bundle._file_record(notes_path),
        "release_blockers": control["release_blockers"],
    }

    portable_path = source_files[names["portable-package"]]
    portable_bytes = _read_portable_files(portable_path)
    required = set(SYNTHETIC_PORTABLE_FILES)
    if not required.issubset(portable_bytes):
        missing = ", ".join(sorted(required - set(portable_bytes)))
        raise SyntheticInputError(f"synthetic portable fixture is incomplete: {missing}")
    portable_files = []
    for path, data in portable_bytes.items():
        component_id = SYNTHETIC_PORTABLE_FILES.get(path, "release")
        portable_files.append(
            {
                "path": path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "component_id": component_id,
                "unresolved_id": None,
            }
        )

    components, unresolved = _component_evidence(tag.removeprefix("v"))
    by_path = {item["path"]: item for item in portable_files}
    source_inventory = [
        {"path": path, "sha256": by_path[path]["sha256"]}
        for path in PYINSTALLER_MEMBERS
    ]
    evidence = {
        "schema_version": 1,
        "evidence_type": "synthetic-ci",
        "synthetic": True,
        "distribution_allowed": False,
        "release": {
            "product": "Youtube Downloaderbs synthetic fixture",
            "version": tag.removeprefix("v"),
            "tag": tag,
            "source_commit": source_commit,
            "control_commit": control_commit,
            "created_utc": SYNTHETIC_CREATED_UTC,
        },
        "final_executable": next(
            item for item in final_artifacts if item["role"] == "application-executable"
        ),
        "final_artifacts": final_artifacts,
        "portable_files": portable_files,
        "pyinstaller_inventory": {
            "executable_path": release_bundle.EXE_NAME,
            "carchive_members": list(PYINSTALLER_MEMBERS),
            "source_inventory": source_inventory,
        },
        "python_runtime": {
            "component_id": "python-runtime",
            "files": ["native/_tkinter.pyd", "python311.dll"],
        },
        "python_packages": [
            {
                "component_id": "python-package-demo",
                "files": ["packages/demo/__init__.py"],
            }
        ],
        "native_members": sorted(
            path
            for path in portable_bytes
            if Path(path).suffix.casefold() in {".dll", ".exe", ".pyd"}
        ),
        "external_runtimes": [
            {"component_id": "aria2", "files": ["data/bin/aria2c.exe"]},
            {"component_id": "deno", "files": ["data/bin/deno.exe"]},
            {
                "component_id": "ffmpeg",
                "files": ["data/bin/ffmpeg.exe"],
            },
            {
                "component_id": "ffprobe",
                "files": ["data/bin/ffprobe.exe"],
            },
            {"component_id": "yt-dlp", "files": ["data/bin/yt-dlp.exe"]},
        ],
        "legal_components": components,
        "release_manifest": release_manifest,
        "checksum_records": [
            {"name": item["name"], "sha256": item["sha256"]} for item in assets
        ],
        "unresolved_components": unresolved,
    }
    return release_sbom.validate_input(evidence)


def _read_portable_files(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}):
            raise SyntheticInputError("synthetic portable fixture contains duplicate paths")
        result = {}
        for info in infos:
            name = release_sbom._canonical_path(info.filename, "synthetic portable fixture")
            if info.is_dir():
                raise SyntheticInputError("synthetic portable fixture contains a directory entry")
            result[name] = archive.read(info)
    return dict(sorted(result.items()))


def _component_evidence(version: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    definitions = [
        ("release", "Youtube Downloaderbs synthetic release", version, "NOASSERTION"),
        ("application", "Youtube Downloaderbs synthetic application", version, "NOASSERTION"),
        ("python-runtime", "Python synthetic runtime", "3.11.9", "PSF-2.0"),
        ("python-package-demo", "Synthetic Python package", "1.0.0", "MIT"),
        ("aria2", "aria2 synthetic runtime", "1.37.0", "NOASSERTION"),
        ("deno", "Deno synthetic runtime", "2.7.14", "MIT"),
        ("ffmpeg", "FFmpeg synthetic runtime", "8.1.2", "NOASSERTION"),
        ("ffprobe", "ffprobe synthetic runtime", "8.1.2", "NOASSERTION"),
        ("yt-dlp", "yt-dlp synthetic runtime", "2026.03.17", "Unlicense"),
    ]
    components = []
    unresolved = []
    for component_id, name, component_version, declared_license in definitions:
        values = {
            "version": component_version,
            "supplier": "NOASSERTION",
            "origin": "NOASSERTION",
            "license_declared": declared_license,
            "license_concluded": "NOASSERTION",
            "download_location": "NOASSERTION",
            "purl": "NOASSERTION",
        }
        unresolved_fields = {}
        unresolved_field_names = []
        unresolved_id = f"{component_id}-authoritative-fields"
        for field, value in values.items():
            if value == "NOASSERTION":
                unresolved_fields[field] = unresolved_id
                unresolved_field_names.append(field)
        components.append(
            {
                "id": component_id,
                "name": name,
                **values,
                "field_provenance": {
                    field: "controlled synthetic fixture declaration"
                    for field in release_sbom.AUTHORITATIVE_FIELDS
                },
                "unresolved_fields": unresolved_fields,
            }
        )
        if unresolved_field_names:
            unresolved.append(
                {
                    "id": unresolved_id,
                    "component_id": component_id,
                    "fields": sorted(unresolved_field_names),
                    "reason": "No authoritative production value exists for synthetic fixture data",
                    "source": "Phase 7B-R1 controlled synthetic fixture",
                    "provenance": "scripts/create_synthetic_release_sbom_input.py",
                }
            )
    return components, unresolved


def _bytes_record(name: str, data: bytes) -> dict[str, Any]:
    return {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


if __name__ == "__main__":
    raise SystemExit(main())
