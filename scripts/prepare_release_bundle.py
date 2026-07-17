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
from typing import Any

import verify_release_legal_payload as legal_payload


SCHEMA_VERSION = 2
BUNDLE_FORMAT = "s9h-release-bundle-v2"
EXE_NAME = "Youtube.Downloaderbs.exe"
CHECKSUM_NAME = "SHA256SUMS.txt"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
NOTES_NAME = "RELEASE_NOTES.md"
POLICY_PATH = "legal/release-policy.json"
SOURCE_KITS_PATH = "legal/source-kit-requirements.json"
TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_ROLE_BY_ID = {
    "aria2": "aria2-source",
    "ffmpeg": "ffmpeg-source",
}


class BundleError(RuntimeError):
    pass


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        prerelease = _parse_boolean(args.prerelease, "prerelease")
        common = {
            "bundle_root": args.bundle_root,
            "tag": args.tag,
            "source_commit": args.source_commit,
            "control_commit": args.control_commit,
            "prerelease": prerelease,
            "policy": args.policy,
            "asset_contract": args.asset_contract,
            "legal_payload_path": args.legal_payload,
            "source_assets_root": args.source_assets_root,
        }
        if args.command == "create":
            create_bundle(release_root=args.release_root, **common)
            print("Release bundle v2 created and verified")
        else:
            verify_bundle(
                require_release_ready=_parse_boolean(
                    args.require_release_ready,
                    "require-release-ready",
                ),
                **common,
            )
            print("Release bundle v2 verified")
    except (BundleError, legal_payload.LegalPayloadError, OSError, UnicodeError) as exc:
        print(f"Release bundle error: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or verify a release bundle v2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--release-root", required=True, type=Path)
    _add_common_arguments(create)
    verify = subparsers.add_parser("verify")
    _add_common_arguments(verify)
    verify.add_argument("--require-release-ready", required=True)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--control-commit", required=True)
    parser.add_argument("--prerelease", required=True)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--asset-contract", required=True, type=Path)
    parser.add_argument("--legal-payload", required=True, type=Path)
    parser.add_argument("--source-assets-root", required=True, type=Path)


def create_bundle(
    *,
    release_root: Path,
    bundle_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
    policy: Path,
    asset_contract: Path,
    legal_payload_path: Path,
    source_assets_root: Path,
) -> None:
    _validate_metadata(tag, source_commit, control_commit, prerelease)
    control = _load_control_state(policy, asset_contract, tag)
    release_root = _require_directory_root(release_root, "release root")
    source_assets_root = _require_directory_root(source_assets_root, "source assets root")
    bundle_root = _prepare_bundle_root(bundle_root)
    names = _asset_names(tag, control["contract"])

    source_files = {
        names["application-executable"]: release_root / "assets" / names["application-executable"],
        names["portable-package"]: release_root / "assets" / names["portable-package"],
        names["legal-payload"]: legal_payload_path,
        names["aria2-source"]: source_assets_root / names["aria2-source"],
        names["ffmpeg-source"]: source_assets_root / names["ffmpeg-source"],
        NOTES_NAME: release_root / NOTES_NAME,
    }
    if legal_payload_path.name != names["legal-payload"]:
        raise BundleError("legal payload filename is invalid")
    for name, path in source_files.items():
        allowed_root = _allowed_root_for_input(
            name,
            release_root=release_root,
            source_assets_root=source_assets_root,
            legal_payload_path=legal_payload_path,
        )
        _require_regular_input(path, allowed_root, name)

    _require_nonempty(source_files[EXE_NAME], EXE_NAME)
    if not _starts_with_mz(source_files[EXE_NAME]):
        raise BundleError(f"{EXE_NAME} does not begin with MZ")
    _require_nonempty(source_files[NOTES_NAME], NOTES_NAME)
    _verify_source_zip(source_files[names["aria2-source"]], names["aria2-source"])
    _verify_source_zip(source_files[names["ffmpeg-source"]], names["ffmpeg-source"])
    legal_payload.verify_release_legal_payload(
        control_root=control["control_root"],
        portable_zip=source_files[names["portable-package"]],
        legal_zip=source_files[names["legal-payload"]],
        release_notes=source_files[NOTES_NAME],
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
    )

    assets_root = bundle_root / "assets"
    assets_root.mkdir()
    for role, name in sorted(names.items(), key=lambda item: item[1]):
        _copy_binary(source_files[name], assets_root / name)
    _copy_binary(source_files[NOTES_NAME], bundle_root / NOTES_NAME)

    assets = [
        _asset_record(assets_root / name, role)
        for role, name in sorted(names.items(), key=lambda item: item[1])
    ]
    checksum_path = assets_root / CHECKSUM_NAME
    checksum_path.write_bytes(_checksum_bytes(assets))
    notes_path = bundle_root / NOTES_NAME
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_format": BUNDLE_FORMAT,
        "release_tag": tag,
        "prerelease": prerelease,
        "source_commit": source_commit,
        "control_commit": control_commit,
        "release_ready": False,
        "legal_compliance_certified": False,
        "source_availability_certified": False,
        "assets": assets,
        "checksum_file": _file_record(checksum_path),
        "release_notes": _file_record(notes_path),
        "release_blockers": control["release_blockers"],
    }
    (bundle_root / MANIFEST_NAME).write_bytes(_canonical_json_bytes(manifest))

    verify_bundle(
        bundle_root=bundle_root,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
        prerelease=prerelease,
        policy=policy,
        asset_contract=asset_contract,
        legal_payload_path=assets_root / names["legal-payload"],
        source_assets_root=assets_root,
        require_release_ready=False,
    )


def verify_bundle(
    *,
    bundle_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
    policy: Path,
    asset_contract: Path,
    legal_payload_path: Path,
    source_assets_root: Path,
    require_release_ready: bool,
) -> None:
    _validate_metadata(tag, source_commit, control_commit, prerelease)
    if type(require_release_ready) is not bool:
        raise BundleError("require-release-ready flag is invalid")
    control = _load_control_state(policy, asset_contract, tag)
    bundle_root = _require_directory_root(bundle_root, "bundle root")
    source_assets_root = _require_directory_root(source_assets_root, "source assets root")
    _require_no_reparse_tree(bundle_root)
    names = _asset_names(tag, control["contract"])
    expected_files = {
        MANIFEST_NAME,
        NOTES_NAME,
        f"assets/{CHECKSUM_NAME}",
        *(f"assets/{name}" for name in names.values()),
    }
    actual_files, actual_directories = _bundle_inventory(bundle_root)
    if actual_files != expected_files or actual_directories != {"assets"}:
        raise BundleError("bundle file set is not exact")

    manifest_path = bundle_root / MANIFEST_NAME
    manifest_bytes = manifest_path.read_bytes()
    _require_utf8_lf(manifest_bytes, MANIFEST_NAME)
    manifest = _load_strict_json(manifest_bytes)
    if manifest_bytes != _canonical_json_bytes(manifest):
        raise BundleError("release manifest is not canonical")
    _validate_manifest(
        manifest,
        bundle_root=bundle_root,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
        prerelease=prerelease,
        names=names,
        release_blockers=control["release_blockers"],
    )
    _verify_release_notes_portable_checksum(
        bundle_root,
        manifest,
        names["portable-package"],
    )

    assets = manifest["assets"]
    checksum_path = bundle_root / "assets" / CHECKSUM_NAME
    checksum_bytes = checksum_path.read_bytes()
    _require_utf8_lf(checksum_bytes, CHECKSUM_NAME)
    if checksum_bytes != _checksum_bytes(assets):
        raise BundleError("checksum file content is invalid")

    assets_root = bundle_root / "assets"
    if not _starts_with_mz(assets_root / EXE_NAME):
        raise BundleError(f"{EXE_NAME} does not begin with MZ")
    _require_regular_input(
        legal_payload_path,
        legal_payload_path.parent.resolve(strict=False),
        names["legal-payload"],
    )
    if legal_payload_path.name != names["legal-payload"]:
        raise BundleError("legal payload filename is invalid")
    legal_payload.verify_release_legal_payload(
        control_root=control["control_root"],
        portable_zip=assets_root / names["portable-package"],
        legal_zip=legal_payload_path,
        release_notes=bundle_root / NOTES_NAME,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
    )
    _require_same_file_bytes(
        legal_payload_path,
        assets_root / names["legal-payload"],
        names["legal-payload"],
    )
    for role in ("aria2-source", "ffmpeg-source"):
        source = source_assets_root / names[role]
        _require_regular_input(source, source_assets_root, names[role])
        _verify_source_zip(source, names[role])
        _require_same_file_bytes(source, assets_root / names[role], names[role])

    if require_release_ready:
        if not (
            manifest["release_ready"] is True
            and manifest["legal_compliance_certified"] is True
            and manifest["source_availability_certified"] is True
            and manifest["release_blockers"] == []
        ):
            raise BundleError("release bundle is not approved for publishing")


def _verify_release_notes_portable_checksum(
    bundle_root: Path,
    manifest: dict[str, Any],
    portable_name: str,
) -> None:
    notes_path = bundle_root / NOTES_NAME
    recorded, _, _ = legal_payload.parse_release_notes_checksum(
        notes_path.read_bytes(),
        portable_name,
    )
    matches = [
        item for item in manifest["assets"]
        if item["role"] == "portable-package" and item["name"] == portable_name
    ]
    if len(matches) != 1:
        raise BundleError("portable package asset record is invalid")
    actual = _sha256(bundle_root / "assets" / portable_name)
    if recorded != matches[0]["sha256"] or recorded != actual:
        raise BundleError("release notes portable checksum does not match the release asset")


def _load_control_state(policy_path: Path, asset_contract_path: Path, tag: str) -> dict[str, Any]:
    asset_contract_path = asset_contract_path.expanduser().resolve(strict=False)
    control_root = asset_contract_path.parent.parent
    expected_contract = control_root / legal_payload.CONTRACT_PATH
    expected_policy = control_root / POLICY_PATH
    expected_source_kits = control_root / SOURCE_KITS_PATH
    if asset_contract_path != expected_contract.resolve(strict=False):
        raise BundleError("release asset contract path is invalid")
    if policy_path.expanduser().resolve(strict=False) != expected_policy.resolve(strict=False):
        raise BundleError("release policy path is invalid")
    contract = legal_payload.load_asset_contract(asset_contract_path)
    policy = _load_canonical_json_file(expected_policy, "release policy")
    source_kits = _load_canonical_json_file(expected_source_kits, "source kit requirements")
    _validate_blocked_control_state(policy, source_kits, tag)
    blockers = sorted(
        set(contract["release_blockers"])
        | set(_policy_release(policy, tag)["reason_codes"])
    )
    if not blockers:
        raise BundleError("release blockers are unavailable")
    return {
        "control_root": control_root.resolve(),
        "contract": contract,
        "release_blockers": blockers,
    }


def _validate_blocked_control_state(policy: object, source_kits: object, tag: str) -> None:
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "policy_mode",
        "legal_compliance_certified",
        "source_availability_certified",
        "release_payload_integrated",
        "releases",
    }:
        raise BundleError("release policy fields are invalid")
    if (
        policy["schema_version"] != 1
        or policy["policy_mode"] != "fail-closed"
        or policy["legal_compliance_certified"] is not False
        or policy["source_availability_certified"] is not False
        or policy["release_payload_integrated"] is not False
    ):
        raise BundleError("release policy is not fail-closed")
    release = _policy_release(policy, tag)
    if release.get("status") != "blocked" or not release.get("reason_codes"):
        raise BundleError("release policy does not block the requested tag")
    if not isinstance(source_kits, dict) or set(source_kits) != {
        "schema_version",
        "target_phase",
        "release_gate_reconsideration_allowed",
        "legal_compliance_certified",
        "kits",
    }:
        raise BundleError("source kit requirement fields are invalid")
    kits = source_kits["kits"]
    if (
        source_kits["schema_version"] != 1
        or source_kits["release_gate_reconsideration_allowed"] is not False
        or source_kits["legal_compliance_certified"] is not False
        or not isinstance(kits, list)
        or [kit.get("id") for kit in kits] != ["aria2", "ffmpeg"]
        or any(kit.get("status") != "blocked" or not kit.get("blockers") for kit in kits)
    ):
        raise BundleError("source kit requirements do not remain blocked")


def _policy_release(policy: dict[str, Any], tag: str) -> dict[str, Any]:
    releases = policy.get("releases")
    if not isinstance(releases, list):
        raise BundleError("release policy releases are invalid")
    matches = [release for release in releases if isinstance(release, dict) and release.get("tag") == tag]
    if not matches:
        return {
            "tag": tag,
            "status": "blocked",
            "reason_codes": ["release-policy-tag-not-reviewed"],
        }
    if len(matches) != 1:
        raise BundleError("release policy contains duplicate requested tags")
    return matches[0]


def _asset_names(tag: str, contract: dict[str, Any]) -> dict[str, str]:
    names = {
        "application-executable": EXE_NAME,
        "portable-package": _zip_name(tag),
        "legal-payload": _expand_asset_template(contract["legal_payload_asset_template"], tag),
    }
    for item in contract["required_source_asset_templates"]:
        role = SOURCE_ROLE_BY_ID.get(item["id"])
        if role is None:
            raise BundleError("source asset role is invalid")
        names[role] = _expand_asset_template(item["filename"], tag)
    if set(names) != {
        "application-executable",
        "portable-package",
        "legal-payload",
        "aria2-source",
        "ffmpeg-source",
    } or len(set(names.values())) != 5:
        raise BundleError("release asset names are invalid")
    return names


def _expand_asset_template(template: object, tag: str) -> str:
    if not isinstance(template, str) or template.count("{tag}") != 1:
        raise BundleError("release asset template is invalid")
    if any(character in template for character in "*?[]"):
        raise BundleError("release asset template contains a wildcard")
    name = template.replace("{tag}", tag)
    if Path(name).name != name or "/" in name or "\\" in name:
        raise BundleError("release asset template is not a filename")
    return name


def _validate_manifest(
    manifest: object,
    *,
    bundle_root: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
    prerelease: bool,
    names: dict[str, str],
    release_blockers: list[str],
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
            "release_ready",
            "legal_compliance_certified",
            "source_availability_certified",
            "assets",
            "checksum_file",
            "release_notes",
            "release_blockers",
        },
        "release manifest",
    )
    checks = (
        (manifest["schema_version"] == SCHEMA_VERSION, "release manifest schema is invalid"),
        (manifest["bundle_format"] == BUNDLE_FORMAT, "release manifest bundle format is invalid"),
        (manifest["release_tag"] == tag, "release manifest tag is invalid"),
        (type(manifest["prerelease"]) is bool and manifest["prerelease"] == prerelease, "release manifest prerelease flag is invalid"),
        (manifest["source_commit"] == source_commit, "release manifest source commit is invalid"),
        (manifest["control_commit"] == control_commit, "release manifest control commit is invalid"),
        (manifest["release_ready"] is False, "release manifest readiness is invalid"),
        (manifest["legal_compliance_certified"] is False, "release manifest legal certification is invalid"),
        (manifest["source_availability_certified"] is False, "release manifest source certification is invalid"),
        (manifest["release_blockers"] == release_blockers, "release manifest blockers are invalid"),
    )
    for condition, message in checks:
        if not condition:
            raise BundleError(message)

    assets = manifest["assets"]
    if not isinstance(assets, list) or len(assets) != 5:
        raise BundleError("release manifest assets are invalid")
    for item in assets:
        _validate_asset_record(item)
    expected_pairs = sorted((name, role) for role, name in names.items())
    actual_pairs = [(item["name"], item["role"]) for item in assets]
    if actual_pairs != expected_pairs:
        raise BundleError("release manifest asset names or roles are invalid")
    if len({item["role"] for item in assets}) != 5:
        raise BundleError("release manifest asset roles are not unique")
    for item in assets:
        _verify_record(bundle_root / "assets" / item["name"], item)

    checksum = manifest["checksum_file"]
    _validate_file_record(checksum, "checksum file")
    if checksum["name"] != CHECKSUM_NAME:
        raise BundleError("release manifest checksum filename is invalid")
    _verify_record(bundle_root / "assets" / CHECKSUM_NAME, checksum)
    notes = manifest["release_notes"]
    _validate_file_record(notes, "release notes")
    if notes["name"] != NOTES_NAME:
        raise BundleError("release manifest notes filename is invalid")
    _verify_record(bundle_root / NOTES_NAME, notes)


def _validate_asset_record(value: object) -> None:
    if not isinstance(value, dict):
        raise BundleError("asset record is invalid")
    _require_exact_keys(value, {"name", "role", "size", "sha256"}, "asset record")
    if value["role"] not in {
        "application-executable",
        "portable-package",
        "legal-payload",
        "aria2-source",
        "ffmpeg-source",
    }:
        raise BundleError("asset role is invalid")
    _validate_record_values(value, "asset")


def _validate_file_record(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise BundleError(f"{label} record is invalid")
    _require_exact_keys(value, {"name", "size", "sha256"}, f"{label} record")
    _validate_record_values(value, label)


def _validate_record_values(value: dict[str, Any], label: str) -> None:
    if not isinstance(value["name"], str) or not value["name"]:
        raise BundleError(f"{label} name is invalid")
    if type(value["size"]) is not int or value["size"] <= 0:
        raise BundleError(f"{label} size is invalid")
    if not isinstance(value["sha256"], str) or not SHA256_PATTERN.fullmatch(value["sha256"]):
        raise BundleError(f"{label} SHA-256 is invalid")


def _verify_record(path: Path, record: dict[str, Any]) -> None:
    if not path.is_file() or _is_reparse(path):
        raise BundleError(f"{record['name']} is unavailable")
    if path.stat().st_size != record["size"]:
        raise BundleError(f"{record['name']} size does not match")
    if _sha256(path) != record["sha256"]:
        raise BundleError(f"{record['name']} SHA-256 does not match")


def _validate_metadata(tag: str, source_commit: str, control_commit: str, prerelease: bool) -> None:
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise BundleError("release tag is invalid")
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(source_commit):
        raise BundleError("source commit is invalid")
    if not isinstance(control_commit, str) or not COMMIT_PATTERN.fullmatch(control_commit):
        raise BundleError("control commit is invalid")
    if type(prerelease) is not bool:
        raise BundleError("prerelease flag is invalid")


def _parse_boolean(value: str, label: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise BundleError(f"{label} must be true or false")


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


def _allowed_root_for_input(
    name: str,
    *,
    release_root: Path,
    source_assets_root: Path,
    legal_payload_path: Path,
) -> Path:
    if name.endswith("-aria2-source.zip") or name.endswith("-ffmpeg-source.zip"):
        return source_assets_root
    if name.endswith("-legal.zip"):
        return legal_payload_path.parent.resolve(strict=False)
    return release_root


def _require_regular_input(path: Path, root: Path, name: str) -> None:
    resolved = path.expanduser().resolve(strict=False)
    root = root.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BundleError(f"{name} escapes its allowed root") from exc
    _require_no_reparse_path(resolved, root)
    if not resolved.is_file():
        if name.endswith("-source.zip"):
            raise BundleError(f"required source asset is unavailable: {name}")
        raise BundleError(f"{name} is unavailable")


def _require_no_reparse_path(path: Path, root: Path) -> None:
    current = root
    if _is_reparse(current):
        raise BundleError("input root cannot be a reparse point")
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
    files: set[str] = set()
    directories: set[str] = set()
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


def _verify_source_zip(path: Path, name: str) -> None:
    _require_nonempty(path, name)
    try:
        legal_payload.read_zip_entries(path, name, deterministic=False)
    except legal_payload.LegalPayloadError as exc:
        raise BundleError(str(exc)) from exc


def _require_same_file_bytes(first: Path, second: Path, name: str) -> None:
    if first.stat().st_size != second.stat().st_size or _sha256(first) != _sha256(second):
        raise BundleError(f"{name} does not match the bundled asset")


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
    return {"name": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}


def _asset_record(path: Path, role: str) -> dict[str, object]:
    return {
        "name": path.name,
        "role": role,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _checksum_bytes(assets: list[dict[str, Any]]) -> bytes:
    names = [item["name"] for item in assets]
    if names != sorted(names) or len(names) != len(set(names)):
        raise BundleError("asset records are not sorted by filename")
    return ("\n".join(f"{item['sha256']}  {item['name']}" for item in assets) + "\n").encode("ascii")


def _require_utf8_lf(data: bytes, name: str) -> None:
    if data.startswith(b"\xef\xbb\xbf"):
        raise BundleError(f"{name} contains a BOM")
    if b"\0" in data:
        raise BundleError(f"{name} contains NUL")
    if b"\r" in data or not data.endswith(b"\n") or data.endswith(b"\n\n"):
        raise BundleError(f"{name} must use LF with one trailing newline")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{name} is not UTF-8") from exc


def _load_canonical_json_file(path: Path, label: str) -> Any:
    legal_payload.require_regular_file(path, path.parent.parent.resolve(strict=False), label)
    raw = path.read_bytes()
    _require_utf8_lf(raw, label)
    value = _load_strict_json(raw)
    if raw != _canonical_json_bytes(value):
        raise BundleError(f"{label} is not canonical")
    return value


def _load_strict_json(data: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BundleError("release JSON contains a duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("release JSON is invalid") from exc


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise BundleError(f"{label} fields are invalid")


if __name__ == "__main__":
    raise SystemExit(main())
