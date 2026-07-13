from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path


SCHEMA_VERSION = 1
BUNDLE_FORMAT = "s9h-release-bundle-v1"
EXE_NAME = "Youtube.Downloaderbs.exe"
CHECKSUM_NAME = "SHA256SUMS.txt"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
NOTES_NAME = "RELEASE_NOTES.md"
TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BundleError(RuntimeError):
    pass


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        prerelease = _parse_boolean(args.prerelease)
        common = {
            "bundle_root": args.bundle_root,
            "tag": args.tag,
            "source_commit": args.source_commit,
            "control_commit": args.control_commit,
            "prerelease": prerelease,
        }
        if args.command == "create":
            create_bundle(release_root=args.release_root, **common)
            print("Release bundle created and verified")
        else:
            verify_bundle(**common)
            print("Release bundle verified")
    except BundleError as exc:
        print(f"Release bundle error: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or verify a release bundle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--release-root", required=True, type=Path)
    _add_common_arguments(create)
    verify = subparsers.add_parser("verify")
    _add_common_arguments(verify)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--control-commit", required=True)
    parser.add_argument("--prerelease", required=True)


def create_bundle(
    *,
    release_root: Path,
    bundle_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
) -> None:
    _validate_metadata(tag, source_commit, control_commit, prerelease)
    release_root = _require_directory_root(release_root, "release root")
    bundle_root = _prepare_bundle_root(bundle_root)
    zip_name = _zip_name(tag)

    source_files = {
        EXE_NAME: release_root / "assets" / EXE_NAME,
        zip_name: release_root / "assets" / zip_name,
        NOTES_NAME: release_root / NOTES_NAME,
    }
    for name, path in source_files.items():
        _require_regular_input(path, release_root, name)

    _require_nonempty(source_files[EXE_NAME], EXE_NAME)
    if not _starts_with_mz(source_files[EXE_NAME]):
        raise BundleError(f"{EXE_NAME} does not begin with MZ")
    _verify_zip(source_files[zip_name], zip_name)
    _require_nonempty(source_files[NOTES_NAME], NOTES_NAME)

    assets_root = bundle_root / "assets"
    assets_root.mkdir()
    _copy_binary(source_files[EXE_NAME], assets_root / EXE_NAME)
    _copy_binary(source_files[zip_name], assets_root / zip_name)
    _copy_binary(source_files[NOTES_NAME], bundle_root / NOTES_NAME)

    assets = [_asset_record(assets_root / name) for name in sorted((EXE_NAME, zip_name))]
    checksum_bytes = _checksum_bytes(assets)
    checksum_path = assets_root / CHECKSUM_NAME
    checksum_path.write_bytes(checksum_bytes)
    notes_path = bundle_root / NOTES_NAME
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_format": BUNDLE_FORMAT,
        "release_tag": tag,
        "prerelease": prerelease,
        "source_commit": source_commit,
        "control_commit": control_commit,
        "assets": assets,
        "checksum_file": _file_record(checksum_path),
        "release_notes": _file_record(notes_path),
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    (bundle_root / MANIFEST_NAME).write_bytes(manifest_bytes)

    verify_bundle(
        bundle_root=bundle_root,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
        prerelease=prerelease,
    )


def verify_bundle(
    *,
    bundle_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
) -> None:
    _validate_metadata(tag, source_commit, control_commit, prerelease)
    bundle_root = _require_directory_root(bundle_root, "bundle root")
    _require_no_reparse_tree(bundle_root)
    zip_name = _zip_name(tag)
    expected_files = {
        MANIFEST_NAME,
        NOTES_NAME,
        f"assets/{EXE_NAME}",
        f"assets/{zip_name}",
        f"assets/{CHECKSUM_NAME}",
    }
    actual_files, actual_directories = _bundle_inventory(bundle_root)
    if actual_files != expected_files or actual_directories != {"assets"}:
        raise BundleError("bundle file set is not exact")

    manifest_path = bundle_root / MANIFEST_NAME
    manifest_bytes = manifest_path.read_bytes()
    _require_utf8_lf(manifest_bytes, MANIFEST_NAME)
    manifest = _load_strict_json(manifest_bytes)
    _validate_manifest(
        manifest,
        bundle_root=bundle_root,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
        prerelease=prerelease,
    )

    assets = manifest["assets"]
    checksum_path = bundle_root / "assets" / CHECKSUM_NAME
    checksum_bytes = checksum_path.read_bytes()
    _require_utf8_lf(checksum_bytes, CHECKSUM_NAME)
    expected_checksum = _checksum_bytes(assets)
    if checksum_bytes != expected_checksum:
        raise BundleError("checksum file content is invalid")

    exe_path = bundle_root / "assets" / EXE_NAME
    if not _starts_with_mz(exe_path):
        raise BundleError(f"{EXE_NAME} does not begin with MZ")
    _verify_zip(bundle_root / "assets" / zip_name, zip_name)


def _validate_manifest(
    manifest: object,
    *,
    bundle_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
) -> None:
    if not isinstance(manifest, dict):
        raise BundleError("release manifest must be an object")
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "bundle_format",
            "release_tag",
            "prerelease",
            "source_commit",
            "control_commit",
            "assets",
            "checksum_file",
            "release_notes",
        },
        "release manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BundleError("release manifest schema is invalid")
    if manifest["bundle_format"] != BUNDLE_FORMAT:
        raise BundleError("release manifest bundle format is invalid")
    if manifest["release_tag"] != tag:
        raise BundleError("release manifest tag is invalid")
    if type(manifest["prerelease"]) is not bool or manifest["prerelease"] != prerelease:
        raise BundleError("release manifest prerelease flag is invalid")
    if manifest["source_commit"] != source_commit:
        raise BundleError("release manifest source commit is invalid")
    if manifest["control_commit"] != control_commit:
        raise BundleError("release manifest control commit is invalid")

    zip_name = _zip_name(tag)
    expected_names = sorted((EXE_NAME, zip_name))
    assets = manifest["assets"]
    if not isinstance(assets, list) or len(assets) != 2:
        raise BundleError("release manifest assets are invalid")
    for item in assets:
        _validate_record(item, "asset")
    names = [item["name"] for item in assets]
    if names != expected_names or len(set(names)) != 2:
        raise BundleError("release manifest asset names are invalid")
    for item in assets:
        _verify_record(bundle_root / "assets" / item["name"], item)

    checksum = manifest["checksum_file"]
    _validate_record(checksum, "checksum file")
    if checksum["name"] != CHECKSUM_NAME:
        raise BundleError("release manifest checksum filename is invalid")
    _verify_record(bundle_root / "assets" / CHECKSUM_NAME, checksum)

    notes = manifest["release_notes"]
    _validate_record(notes, "release notes")
    if notes["name"] != NOTES_NAME:
        raise BundleError("release manifest notes filename is invalid")
    _verify_record(bundle_root / NOTES_NAME, notes)


def _validate_record(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise BundleError(f"{label} record is invalid")
    _require_exact_keys(value, {"name", "size", "sha256"}, f"{label} record")
    if not isinstance(value["name"], str) or not value["name"]:
        raise BundleError(f"{label} name is invalid")
    if type(value["size"]) is not int or value["size"] <= 0:
        raise BundleError(f"{label} size is invalid")
    if not isinstance(value["sha256"], str) or not SHA256_PATTERN.fullmatch(
        value["sha256"]
    ):
        raise BundleError(f"{label} SHA-256 is invalid")


def _verify_record(path: Path, record: dict) -> None:
    if not path.is_file() or _is_reparse(path):
        raise BundleError(f"{record['name']} is unavailable")
    if path.stat().st_size != record["size"]:
        raise BundleError(f"{record['name']} size does not match")
    if _sha256(path) != record["sha256"]:
        raise BundleError(f"{record['name']} SHA-256 does not match")


def _validate_metadata(
    tag: str, source_commit: str, control_commit: str, prerelease: bool
) -> None:
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise BundleError("release tag is invalid")
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(source_commit):
        raise BundleError("source commit is invalid")
    if not isinstance(control_commit, str) or not COMMIT_PATTERN.fullmatch(control_commit):
        raise BundleError("control commit is invalid")
    if type(prerelease) is not bool:
        raise BundleError("prerelease flag is invalid")


def _parse_boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise BundleError("prerelease must be true or false")


def _zip_name(tag: str) -> str:
    return f"Youtube-Downloaderbs-{tag}.zip"


def _prepare_bundle_root(path: Path) -> Path:
    path = path.expanduser().resolve(strict=False)
    if path.exists():
        if _is_reparse(path) or not path.is_dir():
            raise BundleError("bundle root is not a regular directory")
        try:
            next(path.iterdir())
        except StopIteration:
            return path
        raise BundleError("bundle root must be empty")
    path.mkdir(parents=True)
    return path


def _require_directory_root(path: Path, label: str) -> Path:
    path = path.expanduser().resolve(strict=False)
    if not path.is_dir() or _is_reparse(path):
        raise BundleError(f"{label} is unavailable")
    return path


def _require_regular_input(path: Path, root: Path, name: str) -> None:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BundleError(f"{name} escapes the release root") from exc
    _require_no_reparse_path(path, root)
    if not path.is_file():
        raise BundleError(f"{name} is unavailable")


def _require_no_reparse_path(path: Path, root: Path) -> None:
    current = root
    if _is_reparse(current):
        raise BundleError("release root cannot be a reparse point")
    for part in path.relative_to(root).parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise BundleError("release input cannot use a reparse point")


def _require_no_reparse_tree(root: Path) -> None:
    if _is_reparse(root):
        raise BundleError("bundle root cannot be a reparse point")
    for path in root.rglob("*"):
        if _is_reparse(path):
            raise BundleError("bundle cannot contain a reparse point")


def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _bundle_inventory(root: Path) -> tuple[set[str], set[str]]:
    files = set()
    directories = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            directories.add(relative)
        elif path.is_file():
            files.add(relative)
        else:
            raise BundleError("bundle contains an unsupported filesystem entry")
    return files, directories


def _require_nonempty(path: Path, name: str) -> None:
    if path.stat().st_size <= 0:
        raise BundleError(f"{name} is empty")


def _starts_with_mz(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(2) == b"MZ"


def _verify_zip(path: Path, name: str) -> None:
    _require_nonempty(path, name)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if archive.testzip() is not None:
                raise BundleError(f"{name} contains corrupt data")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleError(f"{name} is not a readable ZIP") from exc


def _copy_binary(source: Path, destination: Path) -> None:
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _asset_record(path: Path) -> dict[str, object]:
    return _file_record(path)


def _checksum_bytes(assets: list[dict]) -> bytes:
    lines = [f"{item['sha256']}  {item['name']}" for item in assets]
    if lines != sorted(lines, key=lambda value: value.split("  ", 1)[1]):
        raise BundleError("asset records are not sorted by filename")
    return ("\n".join(lines) + "\n").encode("ascii")


def _require_utf8_lf(data: bytes, name: str) -> None:
    if data.startswith(b"\xef\xbb\xbf"):
        raise BundleError(f"{name} contains a BOM")
    if b"\0" in data:
        raise BundleError(f"{name} contains NUL")
    if b"\r" in data or not data.endswith(b"\n"):
        raise BundleError(f"{name} must use LF with one trailing newline")
    if data.endswith(b"\n\n"):
        raise BundleError(f"{name} contains more than one trailing newline")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{name} is not UTF-8") from exc


def _load_strict_json(data: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise BundleError("release manifest contains a duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("release manifest JSON is invalid") from exc


def _require_exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise BundleError(f"{label} fields are invalid")


if __name__ == "__main__":
    raise SystemExit(main())
