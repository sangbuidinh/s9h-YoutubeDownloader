from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


OWNER_PATH = "legal/source-compliance-v1.3.2.json"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_MODE = 0o100644 << 16
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE_TYPES = {
    "git-commit",
    "mercurial-changeset",
    "fossil-id",
    "release-archive",
}
CLASSIFICATIONS = {
    "REQUIRED_FOR_CORRESPONDING_SOURCE",
    "REQUIRED_FOR_REPRODUCIBILITY_ASSURANCE",
    "OPTIONAL_ASSURANCE",
    "SYSTEM_LIBRARY_CANDIDATE",
}
REQUIRED_OUTPUTS = {
    "binary-to-source-manifest",
    "complete-source-archive",
    "independent-source-asset-checksums",
    "source-asset-license-and-notice-set",
}
REQUIRED_DISTRIBUTION_CONTROLS = {
    "binary-to-source-mapping-published",
    "immutable-exact-source-refs",
    "release-notes-identify-source-asset-names",
    "release-verifier-checks-all-source-assets",
    "retention-policy-requires-owner-legal-review",
    "source-assets-independently-checksummed",
    "source-downloadable-from-same-release-location-as-binary",
}
REQUIRED_BUILD_EVIDENCE = {
    "binary-to-source-mapping",
    "complete-build-orchestration-script",
    "exact-configuration-or-configure-command",
    "local-modifications-or-exact-patches",
}
EXPECTED_COMPONENTS = {
    "aria2": {"aria2", "c-ares", "expat", "gmp", "libssh2", "sqlite", "zlib"},
    "ffmpeg": {"ffmpeg", "media-autobuild-suite"},
}
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
)


class SourceComplianceError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_owner(path: Path, *, allow_unsealed_asset: bool = False) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), "source owner contains a UTF-8 BOM")
    _require(b"\r" not in raw, "source owner must use LF line endings")
    _require(b"\0" not in raw, "source owner contains NUL")
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceComplianceError("source owner JSON is malformed") from exc
    validate_owner(value, allow_unsealed_asset=allow_unsealed_asset)
    _require(raw == canonical_json_bytes(value), "source owner JSON is not canonical")
    return value


def validate_owner(value: Any, *, allow_unsealed_asset: bool = False) -> dict[str, Any]:
    _require(isinstance(value, dict), "source owner root must be an object")
    _require(
        tuple(value)
        == (
            "schema_version",
            "target_phase",
            "release_tag",
            "technical_compliance_not_legal_advice",
            "release_gate_reconsideration_allowed",
            "legal_compliance_certified",
            "source_availability_certified",
            "classification_model",
            "immutable_identity_types",
            "kits",
        ),
        "source owner top-level schema or field order is invalid",
    )
    _require(value["schema_version"] == 2, "source owner schema_version must be 2")
    _require(value["target_phase"] == "6B2", "source owner phase is invalid")
    _require(value["release_tag"] == "v1.3.2", "source owner tag is invalid")
    _require(value["technical_compliance_not_legal_advice"] is True, "source owner disclaimer is missing")
    _require(value["release_gate_reconsideration_allowed"] is True, "ready-state review is disabled")
    _require(type(value["legal_compliance_certified"]) is bool, "legal certification flag is invalid")
    _require(type(value["source_availability_certified"]) is bool, "source certification flag is invalid")
    _require(value["classification_model"] == sorted(CLASSIFICATIONS), "classification model is invalid")
    _require(value["immutable_identity_types"] == sorted(IMMUTABLE_TYPES), "identity type model is invalid")
    kits = value["kits"]
    _require(isinstance(kits, list) and [item.get("id") for item in kits] == ["aria2", "ffmpeg"], "source kits are invalid")
    for kit in kits:
        _validate_kit(kit, allow_unsealed_asset=allow_unsealed_asset)
    all_ready = all(kit["status"] == "ready" for kit in kits)
    if all_ready:
        _require(value["source_availability_certified"] is True, "ready source kits are not certified")
        _require(value["legal_compliance_certified"] is True, "ready source checklist is not certified")
    else:
        _require(value["source_availability_certified"] is False, "incomplete source kits were certified")
        _require(value["legal_compliance_certified"] is False, "incomplete source checklist was certified")
    _verify_hygiene(value)
    return value


def _validate_kit(kit: Any, *, allow_unsealed_asset: bool) -> None:
    _require(isinstance(kit, dict), "source kit record must be an object")
    required = {
        "id",
        "binary_package",
        "required_outputs",
        "required_source_items",
        "required_build_evidence",
        "required_distribution_controls",
        "identities",
        "build_evidence",
        "source_asset",
        "status",
        "blockers",
    }
    _require(set(kit) == required, f"source kit record fields are invalid: {kit.get('id')}")
    package_id = kit["id"]
    _validate_file_identity(kit["binary_package"], f"{package_id} binary package")
    for field in (
        "required_outputs",
        "required_source_items",
        "required_build_evidence",
        "required_distribution_controls",
        "build_evidence",
        "blockers",
    ):
        items = kit[field]
        _require(isinstance(items, list) and all(isinstance(item, str) and item for item in items), f"{package_id} {field} is invalid")
        _require(items == sorted(set(items)), f"{package_id} {field} is not sorted and unique")
    _require(set(kit["required_outputs"]) == REQUIRED_OUTPUTS, f"{package_id} required outputs are incomplete")
    _require(set(kit["required_distribution_controls"]) == REQUIRED_DISTRIBUTION_CONTROLS, f"{package_id} distribution controls are incomplete")
    expected_build = set(REQUIRED_BUILD_EVIDENCE)
    if package_id == "aria2":
        expected_build.remove("local-modifications-or-exact-patches")
        expected_build.add("local-modifications-or-explicit-no-modification-evidence")
    _require(set(kit["required_build_evidence"]) == expected_build, f"{package_id} required build evidence is incomplete")
    identities = kit["identities"]
    _require(isinstance(identities, list) and identities, f"{package_id} identities are missing")
    for identity in identities:
        _validate_source_identity(identity)
    identity_ids = [identity["component_id"] for identity in identities]
    _require(identity_ids == sorted(set(identity_ids)), f"{package_id} identities are not sorted and unique")
    _require(set(identity_ids) == EXPECTED_COMPONENTS[package_id], f"{package_id} identity set is incomplete")
    status = kit["status"]
    _require(status in {"blocked", "ready"}, f"{package_id} source status is invalid")
    if status == "ready":
        _require(not kit["blockers"], f"{package_id} ready source kit has blockers")
        _validate_source_asset(kit["source_asset"], package_id, allow_unsealed=allow_unsealed_asset)
        _require(all(identity["resolved"] is True for identity in identities), f"{package_id} ready source kit has unresolved inputs")
    else:
        _require(bool(kit["blockers"]), f"{package_id} blocked source kit has no blockers")
        _require(kit["source_asset"] is None, f"{package_id} blocked source kit claims an asset")


def _validate_file_identity(value: Any, label: str) -> None:
    _require(isinstance(value, dict) and set(value) == {"filename", "size", "sha256"}, f"{label} fields are invalid")
    _safe_filename(value["filename"], label)
    _require(type(value["size"]) is int and value["size"] > 0, f"{label} size is invalid")
    _require(isinstance(value["sha256"], str) and SHA256_RE.fullmatch(value["sha256"]), f"{label} SHA-256 is invalid")


def _validate_source_identity(identity: Any) -> None:
    expected = {
        "component_id",
        "classification",
        "identity_type",
        "project",
        "version",
        "repository",
        "immutable_ref",
        "archive_url",
        "archive_filename",
        "archive_size",
        "archive_sha256",
        "resolved",
        "evidence",
    }
    _require(isinstance(identity, dict) and set(identity) == expected, "source identity fields are invalid")
    component = identity["component_id"]
    _require(isinstance(component, str) and re.fullmatch(r"[a-z0-9][a-z0-9+._-]*", component), "source component id is invalid")
    _require(identity["classification"] in CLASSIFICATIONS, f"source classification is invalid: {component}")
    identity_type = identity["identity_type"]
    _require(identity_type in IMMUTABLE_TYPES, f"source identity type is invalid: {component}")
    for field in ("project", "version", "repository", "archive_url", "archive_filename", "evidence"):
        _require(isinstance(identity[field], str) and identity[field], f"source identity {field} is invalid: {component}")
    _validate_https_url(identity["repository"], f"{component} repository")
    _validate_https_url(identity["archive_url"], f"{component} archive URL")
    _safe_filename(identity["archive_filename"], f"{component} archive")
    _require(type(identity["archive_size"]) is int and identity["archive_size"] > 0, f"source archive size is invalid: {component}")
    _require(isinstance(identity["archive_sha256"], str) and SHA256_RE.fullmatch(identity["archive_sha256"]), f"source archive SHA-256 is invalid: {component}")
    _require(identity["archive_sha256"] != "0" * 64, f"source archive SHA-256 is unsealed: {component}")
    _require(type(identity["resolved"]) is bool, f"source resolution flag is invalid: {component}")
    immutable_ref = identity["immutable_ref"]
    if identity_type == "git-commit":
        _require(isinstance(immutable_ref, str) and GIT_RE.fullmatch(immutable_ref), f"Git identity is not an exact commit: {component}")
    elif identity_type == "mercurial-changeset":
        _require(isinstance(immutable_ref, str) and re.fullmatch(r"[0-9a-f]{40}", immutable_ref), f"Mercurial identity is not an exact changeset: {component}")
    elif identity_type == "fossil-id":
        _require(isinstance(immutable_ref, str) and re.fullmatch(r"[0-9a-f]{40,64}", immutable_ref), f"Fossil identity is invalid: {component}")
    else:
        _require(immutable_ref is None, f"release archive identity has a fabricated VCS ref: {component}")


def _validate_source_asset(value: Any, package_id: str, *, allow_unsealed: bool) -> None:
    _require(isinstance(value, dict) and set(value) == {"filename", "size", "sha256", "entry_count", "manifest_sha256"}, f"{package_id} source asset fields are invalid")
    _safe_filename(value["filename"], f"{package_id} source asset")
    if allow_unsealed and value["size"] is None and value["sha256"] is None and value["entry_count"] is None:
        _require(value["manifest_sha256"] is None, f"{package_id} unsealed manifest hash is invalid")
        return
    _require(type(value["size"]) is int and value["size"] > 0, f"{package_id} source asset size is invalid")
    _require(isinstance(value["sha256"], str) and SHA256_RE.fullmatch(value["sha256"]), f"{package_id} source asset SHA-256 is invalid")
    _require(type(value["entry_count"]) is int and value["entry_count"] > 0, f"{package_id} source asset entry count is invalid")
    _require(isinstance(value["manifest_sha256"], str) and SHA256_RE.fullmatch(value["manifest_sha256"]), f"{package_id} manifest SHA-256 is invalid")


def verify_source_asset(owner: dict[str, Any], package_id: str, asset_path: Path) -> dict[str, Any]:
    validate_owner(owner)
    kit = next((item for item in owner["kits"] if item["id"] == package_id), None)
    _require(kit is not None and kit["status"] == "ready", f"{package_id} source kit is not ready")
    expected = kit["source_asset"]
    _require(asset_path.is_file(), f"{package_id} source asset is unavailable")
    _require(asset_path.name == expected["filename"], f"{package_id} source asset filename is invalid")
    _require(asset_path.stat().st_size == expected["size"], f"{package_id} source asset size does not match")
    _require(sha256_file(asset_path) == expected["sha256"], f"{package_id} source asset SHA-256 does not match")
    entries = _read_deterministic_zip(asset_path, f"{package_id} source asset")
    _require(len(entries) == expected["entry_count"], f"{package_id} source asset entry count does not match")
    manifest = entries.get("SOURCE_MANIFEST.json")
    _require(manifest is not None, f"{package_id} source manifest is missing")
    _require(hashlib.sha256(manifest).hexdigest() == expected["manifest_sha256"], f"{package_id} source manifest SHA-256 does not match")
    value = json.loads(manifest.decode("utf-8"))
    _require(manifest == canonical_json_bytes(value), f"{package_id} source manifest is not canonical")
    _validate_embedded_manifest(value, entries, kit)
    return value


def _validate_embedded_manifest(value: Any, entries: dict[str, bytes], kit: dict[str, Any]) -> None:
    _require(isinstance(value, dict) and tuple(value) == ("schema_version", "package_id", "release_tag", "binary_package", "source_identities", "files", "non_claims"), "source manifest schema is invalid")
    _require(value["schema_version"] == 1 and value["package_id"] == kit["id"] and value["release_tag"] == "v1.3.2", "source manifest identity is invalid")
    _require(value["binary_package"] == kit["binary_package"], "source manifest binary mapping is invalid")
    _require(value["source_identities"] == kit["identities"], "source manifest identities do not match owner")
    _require(value["non_claims"] == ["byte-identical-rebuild", "legal-advice", "reproducible-build"], "source manifest non-claims are invalid")
    records = value["files"]
    _require(isinstance(records, list), "source manifest file records are invalid")
    expected_names = sorted(name for name in entries if name != "SOURCE_MANIFEST.json")
    _require([item.get("name") for item in records] == expected_names, "source manifest file set is invalid")
    for record in records:
        _require(isinstance(record, dict) and set(record) == {"name", "size", "sha256", "role"}, "source manifest file record is invalid")
        data = entries[record["name"]]
        _require(record["size"] == len(data) and record["sha256"] == hashlib.sha256(data).hexdigest(), f"source manifest record does not match: {record['name']}")
        _require(record["role"] in {"build-script", "license", "notice", "source-archive"}, f"source manifest role is invalid: {record['name']}")


def _read_deterministic_zip(path: Path, label: str) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            _require(names == sorted(names), f"{label} entries are not sorted")
            _require(len(names) == len(set(names)) == len({name.casefold() for name in names}), f"{label} entries collide")
            entries: dict[str, bytes] = {}
            for info in infos:
                _validate_zip_info(info, label)
                _require(not info.is_dir(), f"{label} contains a directory entry")
                entries[info.filename] = archive.read(info)
            _require(archive.testzip() is None, f"{label} is corrupt")
            return entries
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceComplianceError(f"{label} is not a readable ZIP") from exc


def _validate_zip_info(info: zipfile.ZipInfo, label: str) -> None:
    name = info.filename
    _require(name and "\\" not in name, f"{label} contains an invalid path")
    pure = PurePosixPath(name)
    _require(not pure.is_absolute() and ".." not in pure.parts and "." not in pure.parts, f"{label} contains path traversal")
    _require(info.date_time == FIXED_ZIP_TIMESTAMP, f"{label} timestamp is not deterministic")
    _require(info.flag_bits & 0x1 == 0, f"{label} contains encrypted data")
    _require(info.create_system == 3 and info.external_attr == FIXED_FILE_MODE, f"{label} metadata is not deterministic")


def _validate_https_url(value: str, label: str) -> None:
    parts = urlsplit(value)
    _require(parts.scheme == "https" and bool(parts.netloc) and not parts.username and not parts.password, f"{label} is not a canonical HTTPS URL")
    _require(not parts.query and not parts.fragment, f"{label} contains mutable query or fragment data")


def _safe_filename(value: Any, label: str) -> None:
    _require(isinstance(value, str) and value and Path(value).name == value and "/" not in value and "\\" not in value, f"{label} filename is invalid")


def _verify_hygiene(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False)
    _require(LOCAL_PATH_RE.search(text) is None, "source owner contains a local absolute path")
    _require(not any(pattern.search(text) for pattern in SECRET_RES), "source owner contains a secret-like value")
    _require("googlevideo.com" not in text.casefold(), "source owner contains a direct media URL")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceComplianceError(message)
