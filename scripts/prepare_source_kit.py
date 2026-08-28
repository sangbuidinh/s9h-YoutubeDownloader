from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import sys
import tarfile
import zipfile
import subprocess
from urllib.parse import urlsplit
from pathlib import Path

import source_compliance as compliance


ARIA2_BUILD_FILES = (
    "aria2-1.37.0/Dockerfile.mingw",
    "aria2-1.37.0/mingw-build-memo",
    "aria2-1.37.0/mingw-config",
    "aria2-1.37.0/mingw-release",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify a deterministic source-compliance asset")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--owner", required=True, type=Path)
    create.add_argument("--downloads-root", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--license", required=True, type=Path)
    project = subparsers.add_parser("create-ffmpeg")
    project.add_argument("--owner", required=True, type=Path)
    project.add_argument("--downloads-root", required=True, type=Path)
    project.add_argument("--repo-root", required=True, type=Path)
    project.add_argument("--runtime-root", required=True, type=Path)
    project.add_argument("--output", required=True, type=Path)
    runtime = subparsers.add_parser("verify-runtime")
    runtime.add_argument("--owner", required=True, type=Path)
    runtime.add_argument("--runtime-root", required=True, type=Path)
    runtime.add_argument("--repo-root", required=True, type=Path)
    acquire = subparsers.add_parser("acquire-aria2")
    acquire.add_argument("--owner", required=True, type=Path)
    acquire.add_argument("--downloads-root", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--owner", required=True, type=Path)
    verify.add_argument("--package", required=True, choices=("aria2", "ffmpeg"))
    verify.add_argument("--asset", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            owner = compliance.load_owner(args.owner, allow_unsealed_asset=True)
            result = create_aria2_source_kit(owner, args.downloads_root, args.output, args.license)
            print(json.dumps(result, sort_keys=True))
        elif args.command == "create-ffmpeg":
            owner = compliance.load_owner(args.owner, allow_unsealed_asset=True)
            result = create_ffmpeg_source_kit(owner, args.downloads_root, args.repo_root, args.runtime_root, args.output)
            print(json.dumps(result, sort_keys=True))
        elif args.command == "acquire-aria2":
            owner = compliance.load_owner(args.owner)
            acquire_aria2_sources(owner, args.downloads_root)
            print("All seven exact aria2 source inputs verified")
        elif args.command == "verify-runtime":
            owner = compliance.load_owner(args.owner)
            verify_project_runtime(owner, args.runtime_root, args.repo_root)
            print("Project FFmpeg runtime and recipe identities verified")
        else:
            owner = compliance.load_owner(args.owner)
            manifest = compliance.verify_source_asset(owner, args.package, args.asset)
            print(f"{args.package} source asset verified: {len(manifest['files']) + 1} entries")
    except (compliance.SourceComplianceError, OSError, UnicodeError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"Source kit error: {exc}", file=sys.stderr)
        return 1
    return 0


def acquire_aria2_sources(owner: dict, downloads: Path) -> None:
    """Acquire the accepted owner inputs, never regenerate or relax their pins."""
    compliance.validate_owner(owner)
    downloads.mkdir(parents=True, exist_ok=True)
    for identity in owner["kits"][0]["identities"]:
        target = downloads / identity["archive_filename"]
        if not target.exists():
            partial = downloads / (target.name + ".partial")
            url = identity["archive_url"]
            for hop in range(2):
                result = subprocess.run([
                    "curl.exe", "--silent", "--show-error", "--proto", "=https",
                    "--max-time", "600", "--max-filesize", str(identity["archive_size"]),
                    "--output", str(partial), "--write-out", "%{http_code}\n%{redirect_url}", url,
                ], capture_output=True, timeout=610, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                compliance._require(result.returncode == 0, "bounded aria2 source acquisition failed")
                status, redirect = result.stdout.decode("ascii").split("\n", 1)
                if status == "200":
                    compliance._require(partial.stat().st_size == identity["archive_size"]
                                         and compliance.sha256_file(partial) == identity["archive_sha256"], "aria2 downloaded source identity mismatch")
                    partial.rename(target)
                    break
                parsed = urlsplit(redirect)
                compliance._require(hop == 0 and status in {"301", "302", "303", "307", "308"}
                                     and urlsplit(url).hostname == "github.com"
                                     and parsed.scheme == "https" and parsed.hostname == "release-assets.githubusercontent.com"
                                     and parsed.port in (None, 443) and not parsed.username and not parsed.password
                                     and not parsed.fragment, "unexpected aria2 source redirect")
                url = redirect
        compliance._require(target.is_file() and not target.is_symlink()
                             and target.stat().st_size == identity["archive_size"]
                             and compliance.sha256_file(target) == identity["archive_sha256"], "aria2 source input identity mismatch")


def verify_project_runtime(owner: dict, runtime_root: Path, repo: Path) -> None:
    import project_ffmpeg
    compliance.validate_owner(owner, allow_unsealed_asset=True)
    kit = owner["kits"][1]
    compliance._require("runtime_build" in kit and kit["status"] == "ready", "project runtime owner is not ready")
    for binary in kit["runtime_build"]["binaries"]:
        path = runtime_root / binary["filename"]
        compliance._require(path.is_file() and not path.is_symlink(), "project runtime must be a regular file")
        compliance._require(project_ffmpeg.file_identity(path) == binary, "runtime binary/source-owner mismatch")
    for recipe in kit["runtime_build"]["recipe_files"]:
        data = (repo / recipe["name"]).read_bytes()
        compliance._require(len(data) == recipe["size"] and hashlib.sha256(data).hexdigest() == recipe["sha256"],
                             "project recipe/source-owner mismatch")


def create_ffmpeg_source_kit(owner: dict, downloads: Path, repo: Path, runtime_root: Path, output: Path) -> dict:
    import project_ffmpeg
    compliance.validate_owner(owner, allow_unsealed_asset=True)
    kit = owner["kits"][1]
    compliance._require(kit["status"] == "ready" and "runtime_build" in kit, "project FFmpeg owner is not ready")
    runtime = kit["runtime_build"]
    verify_project_runtime(owner, runtime_root, repo)
    for binary in runtime["binaries"]:
        compliance._require(project_ffmpeg.file_identity(runtime_root / binary["filename"]) == binary,
                             "runtime binary/source-owner mismatch")
    files = {}
    for key in ("ffmpeg", "lame"):
        pin = project_ffmpeg.INPUTS[key]
        source = downloads / pin["filename"]
        try:
            project_ffmpeg.verify_input(source, key)
        except project_ffmpeg.ProjectFFmpegError as exc:
            raise compliance.SourceComplianceError(str(exc)) from exc
        files["sources/" + source.name] = (source.read_bytes(), "source-archive")
        prefix = f"FFmpeg-{project_ffmpeg.FFMPEG_COMMIT}" if key == "ffmpeg" else "lame-3.100"
        licenses = ("COPYING.LGPLv2.1", "LICENSE.md") if key == "ffmpeg" else ("COPYING", "LICENSE")
        with tarfile.open(source, "r:gz") as archive:
            for name in licenses:
                member = archive.getmember(prefix + "/" + name)
                compliance._require(member.isfile(), "source license is not a regular file")
                stream = archive.extractfile(member)
                compliance._require(stream is not None, "source license is unreadable")
                label = "FFmpeg" if key == "ffmpeg" else "LAME"
                files[f"licenses/{label}-{name}"] = (stream.read(), "license")
    for recipe in runtime["recipe_files"]:
        data = (repo / recipe["name"]).read_bytes()
        compliance._require(len(data) == recipe["size"] and hashlib.sha256(data).hexdigest() == recipe["sha256"], "project recipe/source-owner mismatch")
        files["build/" + recipe["name"]] = (data, "build-script")
    mingw_notice = (repo / "legal/licenses/MinGW-w64-14.0.0-COPYING.txt").read_bytes()
    compliance._require(hashlib.sha256(mingw_notice).hexdigest() == compliance.MINGW_NOTICE_SHA256,
                         "MinGW runtime notice identity mismatch")
    files["licenses/MinGW-w64-14.0.0-COPYING.txt"] = (mingw_notice, "license")
    files["BINARY_SOURCE_MAPPING.json"] = (compliance.canonical_json_bytes(runtime), "notice")
    files["NO_SOURCE_PATCHES.txt"] = (b"No upstream FFmpeg or LAME source patch is applied. Builds are out of tree.\n", "notice")
    files["NOTICE.txt"] = (
        b"Youtube Downloaderbs v1.3.2 project-controlled FFmpeg 8.1.2 source kit.\n"
        b"FFmpeg LGPL-2.1-or-later; LAME 3.100 LGPL-2.0-or-later. See exact source notices.\n"
        b"This software is based in part on the work of the Independent JPEG Group.\n"
        b"No changes were made to FFmpeg's incorporated IJG-derived source files.\n"
        b"LAME is acknowledged at https://lame.sourceforge.io/ .\n"
        b"Build: python build/scripts/build_project_ffmpeg.py --work-root <new-path-without-spaces> "
        b"--downloads-root <verified-archives> --jobs 4\n"
        b"General-purpose tool binaries are not embedded; exact public pins are in the recipe.\n"
        b"Historical Gyan correspondence is not evidence for this runtime.\n"
        b"Technical evidence only: no legal authorization or byte-identical-rebuild claim.\n", "notice")
    records = [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "role": role}
               for name, (data, role) in sorted(files.items())]
    manifest = {"schema_version": 1, "package_id": "ffmpeg", "release_tag": "v1.3.2",
                "binary_package": kit["binary_package"], "source_identities": kit["identities"],
                "files": records, "non_claims": ["byte-identical-rebuild", "legal-advice", "reproducible-build"]}
    manifest_bytes = compliance.canonical_json_bytes(manifest)
    entries = {"SOURCE_MANIFEST.json": manifest_bytes, **{n: d for n, (d, _) in files.items()}}
    compliance._validate_embedded_manifest(manifest, entries, kit)
    return _write_source_zip(output, entries, manifest_bytes)


def _write_source_zip(output: Path, entries: dict[str, bytes], manifest: bytes) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    compliance._require(not output.exists(), "refusing to replace an existing project source kit")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, compliance.FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = compliance.FIXED_FILE_MODE
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return {"filename": output.name, "size": output.stat().st_size,
            "sha256": compliance.sha256_file(output), "entry_count": len(entries),
            "manifest_sha256": hashlib.sha256(manifest).hexdigest()}


def create_aria2_source_kit(owner: dict, downloads_root: Path, output: Path, license_path: Path) -> dict[str, object]:
    compliance.validate_owner(owner, allow_unsealed_asset=True)
    kit = next(item for item in owner["kits"] if item["id"] == "aria2")
    if kit["status"] != "ready":
        raise compliance.SourceComplianceError("aria2 source kit is not marked ready")
    downloads_root = downloads_root.resolve(strict=True)
    files: dict[str, tuple[bytes, str]] = {}
    for identity in kit["identities"]:
        source = downloads_root / identity["archive_filename"]
        if not source.is_file():
            raise compliance.SourceComplianceError(f"source archive is unavailable: {identity['component_id']}")
        data = source.read_bytes()
        if len(data) != identity["archive_size"] or hashlib.sha256(data).hexdigest() != identity["archive_sha256"]:
            raise compliance.SourceComplianceError(f"source archive identity mismatch: {identity['component_id']}")
        files[f"sources/{identity['archive_filename']}"] = (data, "source-archive")
    aria2_identity = next(item for item in kit["identities"] if item["component_id"] == "aria2")
    aria2_archive = downloads_root / aria2_identity["archive_filename"]
    with lzma.open(aria2_archive, "rb") as stream:
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            members = {member.name: member for member in archive.getmembers()}
            for source_name in ARIA2_BUILD_FILES:
                member = members.get(source_name)
                if member is None or not member.isfile():
                    raise compliance.SourceComplianceError(f"aria2 build script is missing: {source_name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise compliance.SourceComplianceError(f"aria2 build script is unreadable: {source_name}")
                files[f"build/{Path(source_name).name}"] = (extracted.read(), "build-script")
    license_data = license_path.read_bytes()
    if not license_data:
        raise compliance.SourceComplianceError("aria2 license is empty")
    files["licenses/aria2-1.37.0-GPLv2.txt"] = (license_data, "license")
    notice = (
        "Youtube Downloaderbs v1.3.2 aria2 source asset\n"
        "\n"
        "This deterministic archive maps the distributed aria2 Windows package to the\n"
        "authoritative aria2 1.37.0 source release, its six exact external source\n"
        "archives, and the package-specific MinGW build scripts contained in that\n"
        "release. General-purpose compiler binaries are not embedded.\n"
        "\n"
        "This is technical source-compliance evidence, not legal advice and not a\n"
        "claim of a byte-identical or reproducible rebuild.\n"
    ).encode("utf-8")
    files["NOTICE.txt"] = (notice, "notice")
    records = [
        {
            "name": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "role": role,
        }
        for name, (data, role) in sorted(files.items())
    ]
    manifest = {
        "schema_version": 1,
        "package_id": "aria2",
        "release_tag": owner["release_tag"],
        "binary_package": kit["binary_package"],
        "source_identities": kit["identities"],
        "files": records,
        "non_claims": ["byte-identical-rebuild", "legal-advice", "reproducible-build"],
    }
    manifest_bytes = compliance.canonical_json_bytes(manifest)
    all_files = {"SOURCE_MANIFEST.json": manifest_bytes, **{name: data for name, (data, _) in files.items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(all_files.items()):
            info = zipfile.ZipInfo(name, compliance.FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = compliance.FIXED_FILE_MODE
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    output.write_bytes(buffer.getvalue())
    return {
        "filename": output.name,
        "size": output.stat().st_size,
        "sha256": compliance.sha256_file(output),
        "entry_count": len(all_files),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
