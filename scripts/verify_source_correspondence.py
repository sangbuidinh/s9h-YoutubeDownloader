from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import verify_release_legal_gate as release_gate


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "c097cc4fb34d2a08fba5b86540be4c36341b38d4"
CORRESPONDENCE_PATH = "legal/source-correspondence.json"
KIT_PATH = "legal/source-kit-requirements.json"

TOP_LEVEL_KEYS = (
    "schema_version",
    "audit_scope",
    "baseline_commit",
    "legal_compliance_certified",
    "corresponding_source_complete",
    "release_gate_status",
    "packages",
)
PACKAGE_KEYS = (
    "id",
    "binary_package",
    "distributed_binaries",
    "provider",
    "core_source",
    "pe_imports",
    "external_components",
    "build_recipe_status",
    "source_kit_status",
    "blockers",
)
BINARY_PACKAGE_KEYS = (
    "filename",
    "sha256",
    "archive_manifest_sha256",
    "provider_metadata_files",
)
METADATA_FILE_KEYS = (
    "path",
    "size",
    "sha256",
    "encoding",
    "bom",
    "line_endings",
    "contains",
)
METADATA_CONTENT_KEYS = (
    "binary_version",
    "source_reference",
    "configure_line",
    "external_library_versions",
    "build_toolchain",
    "build_date",
    "license_label",
)
DISTRIBUTED_BINARY_KEYS = ("name", "size", "sha256", "machine")
PROVIDER_KEYS = (
    "name",
    "release_identity",
    "source_reference_evidence",
    "configuration_evidence",
    "metadata_status",
)
CORE_SOURCE_KEYS = ("repository", "commit", "archive_sha256", "license_path", "status")
PE_IMPORT_KEYS = (
    "binary",
    "machine",
    "pe_format",
    "dynamic_imports",
    "delay_imports",
    "duplicate_imports",
    "system_dynamic_imports",
    "non_system_dynamic_imports",
    "limitation",
)
EXTERNAL_COMPONENT_KEYS = (
    "id",
    "name",
    "version",
    "evidence",
    "linkage",
    "source_repository",
    "source_ref",
    "license_status",
    "source_status",
)
KIT_TOP_LEVEL_KEYS = (
    "schema_version",
    "target_phase",
    "release_gate_reconsideration_allowed",
    "legal_compliance_certified",
    "kits",
)
KIT_KEYS = (
    "id",
    "binary_package_sha256",
    "required_outputs",
    "required_source_items",
    "required_build_evidence",
    "required_distribution_controls",
    "status",
    "blockers",
)

EXPECTED_PACKAGES = {
    "aria2": {
        "archive_name": "aria2-1.37.0-win-64bit-build1.zip",
        "archive_sha256": "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288",
        "archive_manifest_sha256": "16cd9afd9ba2e7b9d65a1530c2c48604c17f83729d60367a06023637480ab5e6",
        "source_archive_sha256": "f0839604196b45959f72f766fdfa9df5eae6c292d48020c6475ca7eacd5d15e7",
        "repository": "aria2/aria2",
        "commit": "02f2d0d8472b3c38c29b4dba8c75ebd5fdd2899a",
        "license_path": "COPYING",
        "binaries": {
            "aria2c.exe": "be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2"
        },
        "binary_sizes": {"aria2c.exe": 5649408},
        "metadata_sha256": {
            "AUTHORS": "674f3d71beaed6015a9fc189fb28835e3bc538aa30dd5c9b4c7effd7024250c1",
            "ChangeLog": "cb407c10210b919b398cfb846b0f8250a489e430c3cd1cf87b639eb6a3323022",
            "COPYING": "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643",
            "LICENSE.OpenSSL": "8bf8790acc763bbae4e03f90fd28ee25acdf6daadd3b2adc90101365a403ed07",
            "NEWS": "3d1c0eca6005ab666cfe531ea8a3902350e87491450f84186ef3f6a5058ad580",
            "README.html": "edcd2c4e4ed1e87b6ca1f4f820f3f078a86561a07fcbe6cc28ffb52c47118e33",
            "README.mingw": "a27706a20cbdd5af86b137441afd9e2d55504df18aa4906235aa73489ffe5e23",
        },
    },
    "ffmpeg": {
        "archive_name": "ffmpeg-8.1.2-essentials_build.zip",
        "archive_sha256": "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
        "archive_manifest_sha256": "096317051589689f3613db597e0acceed3380d4f076c3150da541a0ed13f6076",
        "source_archive_sha256": "c3453fbfc7ca25423f4984a83ceda01949d458a8bc04f9d68fab7c392f75b3ab",
        "repository": "FFmpeg/FFmpeg",
        "commit": "38b88335f99e76ed89ff3c93f877fdefce736c13",
        "license_path": "COPYING.GPLv3",
        "binaries": {
            "ffmpeg.exe": "1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e",
            "ffprobe.exe": "b49ccc7c6547b141ad5a2f6ec69cc04323d7133d7704d70b331b904c63eecb07",
        },
        "binary_sizes": {"ffmpeg.exe": 101897728, "ffprobe.exe": 101692928},
        "metadata_sha256": {
            "LICENSE": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
            "README.txt": "9172433fb251059a58d2ff11ba8c6132e04819136ed96e809563911ff0d13816",
        },
    },
}

PE_LIMITATION = (
    "PE imports describe dynamic dependencies only and do not enumerate "
    "statically linked source components."
)
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
    "binary-rebuild-mapping",
    "complete-build-orchestration-script",
    "exact-configuration-or-configure-command",
    "local-modifications-or-explicit-no-modification-evidence",
    "toolchain-and-package-versions",
}
REQUIRED_OUTPUTS = {
    "binary-to-source-manifest",
    "complete-source-archive",
    "independent-source-asset-checksums",
    "source-asset-license-and-notice-set",
}

HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LOCAL_PATH_RE = re.compile(r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)")
TIMESTAMP_RE = re.compile(r"(?i)\b(?:19|20)\d{2}-\d{2}-\d{2}[t ][0-9]{2}:[0-9]{2}")
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)
MUTABLE_REFS = {"head", "latest", "main", "master", "nightly", "release", "stable"}
FORBIDDEN_KEYS = {
    "allow",
    "allowed",
    "approved",
    "bypass",
    "created_at",
    "date",
    "expires",
    "generated_at",
    "override",
    "timestamp",
}


class SourceCorrespondenceError(AssertionError):
    pass


def main() -> int:
    try:
        verify_repository(REPO_ROOT)
    except (
        SourceCorrespondenceError,
        release_gate.ReleaseLegalGateError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"source correspondence verification failed: {exc}", file=sys.stderr)
        return 1
    print("source correspondence verified")
    return 0


def verify_repository(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(root)
    correspondence = load_correspondence(root / CORRESPONDENCE_PATH)
    kits = load_kit_requirements(root / KIT_PATH, correspondence)
    policy = release_gate.load_policy(root / "legal/release-policy.json")
    _require(policy["policy_mode"] == "fail-closed", "release policy is not fail-closed")
    _require(all(item["status"] == "blocked" for item in policy["releases"]), "a release policy entry is not blocked")
    _verify_documentation(root)
    _verify_repository_hygiene(root, correspondence, kits)
    return correspondence, kits


def load_correspondence(path: Path) -> dict[str, Any]:
    document, raw = _load_json(path, "source correspondence")
    validate_correspondence_document(document)
    _require(raw == canonical_json_bytes(document), "source correspondence JSON is not deterministic")
    return document


def load_kit_requirements(path: Path, correspondence: dict[str, Any]) -> dict[str, Any]:
    document, raw = _load_json(path, "source kit requirements")
    validate_kit_document(document, correspondence)
    _require(raw == canonical_json_bytes(document), "source kit requirements JSON is not deterministic")
    return document


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate_correspondence_document(document: Any) -> dict[str, Any]:
    _require(isinstance(document, dict), "source correspondence root must be an object")
    _require(tuple(document) == TOP_LEVEL_KEYS, "source correspondence top-level schema or field order is invalid")
    _require(document["schema_version"] == 1, "source correspondence schema_version must be 1")
    _require(document["audit_scope"] == "pinned-distributed-gpl-runtime-packages", "audit scope is invalid")
    _require(document["baseline_commit"] == BASELINE_COMMIT, "baseline commit is invalid")
    _require(document["legal_compliance_certified"] is False, "legal compliance must remain uncertified")
    _require(document["corresponding_source_complete"] is False, "Corresponding Source must remain incomplete")
    _require(document["release_gate_status"] == "fail-closed", "release gate status must remain fail-closed")

    packages = document["packages"]
    _require(isinstance(packages, list), "packages must be an array")
    ids = [_record_id(item, "package") for item in packages]
    _require(ids == sorted(EXPECTED_PACKAGES), "package IDs are missing, duplicated, or unsorted")
    for package in packages:
        _validate_package(package)
    _verify_hygiene(document, "source correspondence")
    return document


def _validate_package(package: dict[str, Any]) -> None:
    package_id = package["id"]
    expected = EXPECTED_PACKAGES[package_id]
    _require(tuple(package) == PACKAGE_KEYS, f"{package_id} package schema or field order is invalid")

    binary_package = package["binary_package"]
    _require(isinstance(binary_package, dict) and tuple(binary_package) == BINARY_PACKAGE_KEYS, f"{package_id} binary package schema is invalid")
    _require(binary_package["filename"] == expected["archive_name"], f"{package_id} archive filename is invalid")
    _require(binary_package["sha256"] == expected["archive_sha256"], f"{package_id} archive hash is invalid")
    _require(binary_package["archive_manifest_sha256"] == expected["archive_manifest_sha256"], f"{package_id} archive manifest hash is invalid")
    metadata = binary_package["provider_metadata_files"]
    _require(isinstance(metadata, list) and metadata, f"{package_id} package metadata evidence is missing")
    metadata_paths: list[str] = []
    for record in metadata:
        _require(isinstance(record, dict) and tuple(record) == METADATA_FILE_KEYS, f"{package_id} metadata record schema is invalid")
        _require(_is_relative_archive_path(record["path"]), f"{package_id} metadata path is invalid")
        _require(isinstance(record["size"], int) and record["size"] > 0, f"{package_id} metadata size is invalid")
        _require(HASH_RE.fullmatch(record["sha256"]) is not None, f"{package_id} metadata hash is invalid")
        _require(record["encoding"] == "utf-8", f"{package_id} metadata encoding is invalid")
        _require(record["bom"] in {"none", "utf-8"}, f"{package_id} metadata BOM status is invalid")
        _require(record["line_endings"] in {"none", "lf", "cr", "crlf", "mixed"}, f"{package_id} metadata line-ending status is invalid")
        contains = record["contains"]
        _require(isinstance(contains, dict) and tuple(contains) == METADATA_CONTENT_KEYS, f"{package_id} metadata content flags are invalid")
        _require(all(isinstance(value, bool) for value in contains.values()), f"{package_id} metadata content flag is not boolean")
        metadata_paths.append(record["path"])
    _require(_sorted_unique_strings(metadata_paths, casefold=True), f"{package_id} metadata paths are duplicated or unsorted")
    metadata_by_name = {Path(record["path"]).name: record for record in metadata}
    _require(set(metadata_by_name) == set(expected["metadata_sha256"]), f"{package_id} metadata file set is incomplete")
    for name, expected_hash in expected["metadata_sha256"].items():
        _require(metadata_by_name[name]["sha256"] == expected_hash, f"{package_id} metadata hash is invalid: {name}")
    _require(any(record["contains"]["binary_version"] for record in metadata), f"{package_id} binary version metadata evidence is missing")
    _require(any(record["contains"]["source_reference"] for record in metadata), f"{package_id} source reference metadata evidence is missing")
    _require(any(record["contains"]["build_toolchain"] for record in metadata), f"{package_id} build toolchain metadata evidence is missing")

    binaries = package["distributed_binaries"]
    _require(isinstance(binaries, list), f"{package_id} binaries must be an array")
    names: list[str] = []
    for record in binaries:
        _require(isinstance(record, dict) and tuple(record) == DISTRIBUTED_BINARY_KEYS, f"{package_id} binary record schema is invalid")
        name = record["name"]
        _require(name in expected["binaries"], f"{package_id} binary name is invalid")
        _require(record["size"] == expected["binary_sizes"][name], f"{package_id} binary size is invalid")
        _require(record["sha256"] == expected["binaries"][name], f"{package_id} binary hash is invalid: {name}")
        _require(record["machine"] == "x86_64", f"{package_id} binary machine is invalid: {name}")
        names.append(name)
    _require(names == sorted(expected["binaries"], key=str.casefold), f"{package_id} binary set is missing, duplicated, or unsorted")

    provider = package["provider"]
    _require(isinstance(provider, dict) and tuple(provider) == PROVIDER_KEYS, f"{package_id} provider schema is invalid")
    for field in ("name", "release_identity"):
        _require(isinstance(provider[field], str) and provider[field], f"{package_id} provider {field} is missing")
    for field in ("source_reference_evidence", "configuration_evidence"):
        _require(_nonempty_unique_strings(provider[field]), f"{package_id} provider {field} is missing or duplicated")
    _require(provider["metadata_status"] in {"verified", "partial"}, f"{package_id} provider metadata status is invalid")

    core = package["core_source"]
    _require(isinstance(core, dict) and tuple(core) == CORE_SOURCE_KEYS, f"{package_id} core source schema is invalid")
    _require(core["repository"] == expected["repository"], f"{package_id} core repository is invalid")
    _require(core["commit"] == expected["commit"] and COMMIT_RE.fullmatch(core["commit"]) is not None, f"{package_id} core commit must be exact and immutable")
    _require(core["archive_sha256"] == expected["source_archive_sha256"], f"{package_id} core source archive hash is invalid")
    _require(core["license_path"] == expected["license_path"], f"{package_id} core license path is invalid")
    _require(core["status"] == "core-source-identified", f"{package_id} core source status is invalid")

    imports = package["pe_imports"]
    _require(isinstance(imports, list) and len(imports) == len(binaries), f"{package_id} PE import records are incomplete")
    import_names: list[str] = []
    for record in imports:
        _require(isinstance(record, dict) and tuple(record) == PE_IMPORT_KEYS, f"{package_id} PE import schema is invalid")
        _require(record["binary"] in expected["binaries"], f"{package_id} PE import binary is invalid")
        import_names.append(record["binary"])
        _require(record["machine"] == "x86_64", f"{package_id} PE import machine is invalid")
        _require(record["pe_format"] == "PE32+", f"{package_id} PE format is invalid")
        for field in (
            "dynamic_imports",
            "delay_imports",
            "duplicate_imports",
            "system_dynamic_imports",
            "non_system_dynamic_imports",
        ):
            _require(_sorted_unique_strings(record[field], casefold=True), f"{package_id} {field} is duplicated or unsorted")
        all_imports = set(record["dynamic_imports"] + record["delay_imports"])
        _require(set(record["duplicate_imports"]).issubset(all_imports), f"{package_id} duplicate imports are inconsistent")
        _require(set(record["system_dynamic_imports"]).issubset(all_imports), f"{package_id} system imports are inconsistent")
        _require(set(record["non_system_dynamic_imports"]).issubset(all_imports), f"{package_id} non-system imports are inconsistent")
        _require(not set(record["system_dynamic_imports"]) & set(record["non_system_dynamic_imports"]), f"{package_id} PE import classifications overlap")
        _require(set(record["system_dynamic_imports"] + record["non_system_dynamic_imports"]) == all_imports, f"{package_id} PE import classification is incomplete")
        _require(record["limitation"] == PE_LIMITATION, f"{package_id} PE import limitation is missing")
    _require(import_names == names, f"{package_id} PE import records are duplicated or unsorted")

    components = package["external_components"]
    _require(isinstance(components, list) and components, f"{package_id} external component matrix is missing")
    component_ids: list[str] = []
    for component in components:
        _require(isinstance(component, dict) and tuple(component) == EXTERNAL_COMPONENT_KEYS, f"{package_id} external component schema is invalid")
        component_id = component["id"]
        _require(ID_RE.fullmatch(component_id) is not None, f"{package_id} external component ID is invalid")
        component_ids.append(component_id)
        _require(isinstance(component["name"], str) and component["name"], f"{package_id} external component name is missing")
        version = component["version"]
        evidence = component["evidence"]
        _require(_nonempty_unique_strings(evidence), f"{package_id} external component evidence is missing: {component_id}")
        _require(isinstance(version, str) and version, f"{package_id} external component version is invalid: {component_id}")
        if version != "unverified":
            _require(any(version in item for item in evidence), f"{package_id} external component version lacks evidence: {component_id}")
        _require(component["linkage"] in {"static", "dynamic", "system", "unverified"}, f"{package_id} external linkage is invalid: {component_id}")
        _verify_source_reference(package_id, component)
        _require(component["license_status"] in {"identified", "partial", "unresolved"}, f"{package_id} external license status is invalid: {component_id}")
        _require(component["source_status"] in {"identified", "partial", "unresolved"}, f"{package_id} external source status is invalid: {component_id}")
    _require(component_ids == sorted(set(component_ids), key=str.casefold), f"{package_id} external components are duplicated or unsorted")
    _require(package["build_recipe_status"] == "partial", f"{package_id} build recipe status must remain partial")
    _require(package["source_kit_status"] == "not-ready", f"{package_id} source kit status must remain not-ready")
    _require(_nonempty_unique_strings(package["blockers"]), f"{package_id} blockers are missing")
    if any(component["source_status"] != "identified" for component in components):
        _require(bool(package["blockers"]), f"{package_id} unresolved source items require blockers")


def validate_kit_document(document: Any, correspondence: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(document, dict), "source kit root must be an object")
    _require(tuple(document) == KIT_TOP_LEVEL_KEYS, "source kit top-level schema or field order is invalid")
    _require(document["schema_version"] == 1, "source kit schema_version must be 1")
    _require(document["target_phase"] == "6B2B", "source kit target phase is invalid")
    _require(document["release_gate_reconsideration_allowed"] is False, "release gate reconsideration must remain false")
    _require(document["legal_compliance_certified"] is False, "source kit must not certify compliance")
    kits = document["kits"]
    _require(isinstance(kits, list), "kits must be an array")
    kit_ids = [_record_id(kit, "kit") for kit in kits]
    _require(kit_ids == sorted(EXPECTED_PACKAGES), "source kit IDs are missing, duplicated, or unsorted")
    packages = {package["id"]: package for package in correspondence["packages"]}
    for kit in kits:
        _validate_kit(kit, packages[kit["id"]])
    _verify_hygiene(document, "source kit requirements")
    return document


def _validate_kit(kit: dict[str, Any], package: dict[str, Any]) -> None:
    kit_id = kit["id"]
    _require(tuple(kit) == KIT_KEYS, f"{kit_id} source kit schema or field order is invalid")
    _require(kit["binary_package_sha256"] == package["binary_package"]["sha256"], f"{kit_id} binary package mapping is invalid")
    for field in (
        "required_outputs",
        "required_source_items",
        "required_build_evidence",
        "required_distribution_controls",
    ):
        _require(_sorted_unique_strings(kit[field]), f"{kit_id} {field} is missing, duplicated, or unsorted")
    _require(set(kit["required_outputs"]) == REQUIRED_OUTPUTS, f"{kit_id} required outputs are incomplete")
    _require(set(kit["required_build_evidence"]) == REQUIRED_BUILD_EVIDENCE, f"{kit_id} build evidence requirements are incomplete")
    _require(set(kit["required_distribution_controls"]) == REQUIRED_DISTRIBUTION_CONTROLS, f"{kit_id} distribution controls are incomplete")
    source_items = set(kit["required_source_items"])
    core_item = f"core-source:{kit_id}@{package['core_source']['commit']}"
    _require(core_item in source_items, f"{kit_id} exact core source requirement is missing")
    static_components = {
        f"external-source:{component['id']}"
        for component in package["external_components"]
        if component["linkage"] == "static"
    }
    _require(static_components.issubset(source_items), f"{kit_id} external component source requirements are incomplete")
    common = {
        "licenses-and-notices",
        "local-modifications-or-explicit-no-modification-evidence",
        "source-archive-manifest",
    }
    _require(common.issubset(source_items), f"{kit_id} common source requirements are incomplete")
    if kit_id == "ffmpeg":
        required = {"build-and-configuration-scripts", "exact-configure-command", "toolchain-and-package-information"}
    else:
        required = {"configuration-options", "windows-build-scripts-and-toolchain-details"}
    _require(required.issubset(source_items), f"{kit_id} package build source requirements are incomplete")
    _require(kit["status"] == "blocked", f"{kit_id} source kit must remain blocked")
    _require(_nonempty_unique_strings(kit["blockers"]), f"{kit_id} source kit blockers are missing")


def _verify_documentation(root: Path) -> None:
    required = (
        "## Phase 6B2A source correspondence audit",
        "legal/source-correspondence.json",
        "legal/source-kit-requirements.json",
        "source correspondence partially identified",
        "source kit not ready",
        "no compliance certification",
        "all five workflows remain fail-closed",
        "does not enable publishing",
        "existing releases are not retroactively certified",
    )
    for relative in ("README.md", "THIRD_PARTY_NOTICES.md", "legal/README.md"):
        text = _read_authored_text(root / relative, relative)
        for phrase in required:
            _require(phrase in text, f"{relative} is missing Phase 6B2A documentation: {phrase}")


def _verify_repository_hygiene(root: Path, correspondence: dict[str, Any], kits: dict[str, Any]) -> None:
    _verify_hygiene(correspondence, CORRESPONDENCE_PATH)
    _verify_hygiene(kits, KIT_PATH)
    policy = json.loads((root / "legal/release-policy.json").read_text(encoding="utf-8"))
    _require(policy["legal_compliance_certified"] is False, "release policy compliance certification changed")
    _require(policy["source_availability_certified"] is False, "release policy source certification changed")
    _require(policy["release_payload_integrated"] is False, "release policy payload status changed")


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    _require(path.is_file() and not path.is_symlink(), f"required regular file is missing: {path.name}")
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{label} contains a UTF-8 BOM")
    _require(b"\r" not in raw, f"{label} must use LF line endings")
    _require(b"\0" not in raw, f"{label} contains NUL")
    text = raw.decode("utf-8")
    _require(text.endswith("\n") and not text.endswith("\n\n"), f"{label} must have one final newline")
    document = json.loads(text)
    return document, raw


def _read_authored_text(path: Path, relative: str) -> str:
    _require(path.is_file(), f"required documentation is missing: {relative}")
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf") and b"\0" not in raw, f"{relative} encoding is invalid")
    normalized = raw.replace(b"\r\n", b"\n")
    _require(b"\r" not in normalized, f"{relative} line endings are invalid")
    return normalized.decode("utf-8")


def _verify_source_reference(package_id: str, component: dict[str, Any]) -> None:
    repository = component["source_repository"]
    source_ref = component["source_ref"]
    _require(isinstance(repository, str) and repository, f"{package_id} source repository is invalid")
    _require(isinstance(source_ref, str) and source_ref, f"{package_id} source ref is invalid")
    _require(source_ref.casefold() not in MUTABLE_REFS, f"{package_id} external component uses a mutable source ref")
    _require((repository == "unverified") == (source_ref == "unverified"), f"{package_id} external source evidence is inconsistent")
    if source_ref != "unverified":
        _require(COMMIT_RE.fullmatch(source_ref) is not None, f"{package_id} external source ref must be immutable")


def _verify_hygiene(value: Any, label: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    _require(LOCAL_PATH_RE.search(serialized) is None, f"local absolute path in {label}")
    _require(TIMESTAMP_RE.search(serialized) is None, f"generated timestamp in {label}")
    _require(not any(pattern.search(serialized) for pattern in SECRET_RES), f"secret-like value in {label}")
    for key in _walk_keys(value):
        _require(key.casefold() not in FORBIDDEN_KEYS, f"forbidden allow, bypass, or generated metadata in {label}: {key}")


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def _record_id(record: Any, label: str) -> str:
    _require(isinstance(record, dict), f"{label} record must be an object")
    value = record.get("id")
    _require(isinstance(value, str), f"{label} ID is invalid")
    return value


def _is_relative_archive_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value
        and "\\" not in value
        and not value.startswith("/")
        and ".." not in value.split("/")
        and LOCAL_PATH_RE.search(value) is None
    )


def _nonempty_unique_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value) and len(value) == len(set(value))


def _sorted_unique_strings(value: Any, *, casefold: bool = False) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        return False
    if casefold:
        return len(value) == len({item.casefold() for item in value}) and value == sorted(
            value, key=str.casefold
        )
    return len(value) == len(set(value)) and value == sorted(value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceCorrespondenceError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
