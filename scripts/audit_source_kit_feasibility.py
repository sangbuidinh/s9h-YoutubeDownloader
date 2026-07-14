from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import verify_source_correspondence as source_correspondence


BASELINE_COMMIT = "89df08602591184dc6501240e5d50738197f8db4"
TARGET_PHASE = "6B2B2A"
INVENTORY_SCOPE = "pinned-gpl-runtime-source-kit-input-feasibility"

INVENTORY_KEYS = (
    "schema_version",
    "target_phase",
    "baseline_commit",
    "inventory_scope",
    "release_gate_reconsideration_allowed",
    "legal_compliance_certified",
    "source_assets_created",
    "packages",
)
PACKAGE_KEYS = (
    "id",
    "binary_package_sha256",
    "core_source",
    "external_components",
    "toolchain",
    "build_orchestration",
    "package_status",
    "blockers",
)
CORE_SOURCE_KEYS = (
    "repository",
    "commit",
    "archive_sha256",
    "license_path",
    "evidence",
    "status",
    "blockers",
)
EXTERNAL_COMPONENT_KEYS = (
    "id",
    "linkage",
    "provider_version",
    "version_status",
    "upstream_repository",
    "immutable_ref",
    "source_archive_sha256",
    "evidence",
    "resolution_status",
    "blockers",
)
EVIDENCE_KEYS = ("kind", "authority", "locator", "claim", "status")
TOOLCHAIN_KEYS = (
    "host",
    "compiler",
    "compiler_version",
    "supporting_tools",
    "evidence",
    "status",
    "blockers",
)
BUILD_ORCHESTRATION_KEYS = (
    "provider_repository",
    "immutable_ref",
    "exact_configuration",
    "patch_status",
    "reproducible_entrypoint",
    "evidence",
    "status",
    "blockers",
)
FEASIBILITY_KEYS = (
    "schema_version",
    "target_phase",
    "baseline_commit",
    "release_gate_reconsideration_allowed",
    "legal_compliance_certified",
    "source_assets_created",
    "source_kits_ready",
    "assembly_authorized",
    "packages",
    "overall_status",
    "next_phase",
)
FEASIBILITY_PACKAGE_KEYS = (
    "id",
    "binary_package_sha256",
    "total_external_components",
    "static_components",
    "system_components",
    "verified_immutable_inputs",
    "partially_identified_inputs",
    "unresolved_inputs",
    "toolchain_status",
    "build_orchestration_status",
    "source_kit_status",
    "blockers",
    "next_actions",
)

VERSION_STATUSES = {"verified", "provider-identified", "unresolved", "not-applicable"}
RESOLUTION_STATUSES = {
    "verified-immutable-input",
    "identified-version-only",
    "identified-name-only",
    "system-component-candidate",
    "unresolved",
}
EVIDENCE_STATUSES = {"verified", "partial", "unresolved"}
ASSESSMENT_STATUSES = {"verified", "partial", "unresolved"}
PARTIAL_RESOLUTIONS = {
    "identified-version-only",
    "identified-name-only",
    "system-component-candidate",
}
MUTABLE_REFS = {"head", "latest", "main", "master", "nightly", "release", "stable"}

HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
TIMESTAMP_RE = re.compile(r"(?i)\b(?:19|20)\d{2}-\d{2}-\d{2}[t ][0-9]{2}:[0-9]{2}")
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)
UNSUPPORTED_CLAIM_RES = (
    re.compile(r"(?i)\bsource kits? (?:is|are) (?:complete|ready)\b"),
    re.compile(r"(?i)\bcorresponding source (?:is )?complete\b"),
    re.compile(r"(?i)\blegal compliance (?:is )?certified\b"),
    re.compile(r"(?i)\brelease (?:is )?(?:approved|ready)\b"),
    re.compile(r"(?i)\bsource assets? (?:is|are|were) (?:published|assembled)\b"),
    re.compile(r"(?i)\bbuild (?:is )?reproducible\b"),
)


class SourceKitFeasibilityError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an offline source-kit feasibility assessment"
    )
    parser.add_argument("--source-correspondence", required=True, type=Path)
    parser.add_argument("--source-kit-requirements", required=True, type=Path)
    parser.add_argument("--source-input-inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        correspondence, requirements, inventory = load_inputs(
            args.source_correspondence,
            args.source_kit_requirements,
            args.source_input_inventory,
        )
        document = generate_feasibility(correspondence, requirements, inventory)
        payload = canonical_json_bytes(document)
        _write_atomic(
            args.output,
            payload,
            protected_inputs={
                _lexical_absolute(args.source_correspondence),
                _lexical_absolute(args.source_kit_requirements),
                _lexical_absolute(args.source_input_inventory),
            },
        )
    except (
        SourceKitFeasibilityError,
        source_correspondence.SourceCorrespondenceError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"source kit feasibility audit failed: {exc}", file=sys.stderr)
        return 1
    print("source kit feasibility audit generated")
    return 0


def load_inputs(
    correspondence_path: Path,
    requirements_path: Path,
    inventory_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    correspondence, correspondence_raw = _load_json(
        correspondence_path, "source correspondence"
    )
    source_correspondence.validate_correspondence_document(correspondence)
    _require(
        correspondence_raw == canonical_json_bytes(correspondence),
        "source correspondence JSON is not canonical",
    )

    requirements, requirements_raw = _load_json(
        requirements_path, "source kit requirements"
    )
    source_correspondence.validate_kit_document(requirements, correspondence)
    _require(
        requirements_raw == canonical_json_bytes(requirements),
        "source kit requirements JSON is not canonical",
    )

    inventory, inventory_raw = _load_json(inventory_path, "source input inventory")
    validate_inventory_document(inventory, correspondence, requirements)
    _require(
        inventory_raw == canonical_json_bytes(inventory),
        "source input inventory JSON is not canonical",
    )
    return correspondence, requirements, inventory


def load_feasibility(
    path: Path,
    correspondence: dict[str, Any],
    requirements: dict[str, Any],
    inventory: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    document, raw = _load_json(path, "source kit feasibility")
    validate_feasibility_document(document, correspondence, requirements, inventory)
    _require(raw == canonical_json_bytes(document), "source kit feasibility JSON is not canonical")
    return document, raw


def validate_inventory_document(
    document: Any,
    correspondence: dict[str, Any],
    requirements: dict[str, Any],
) -> dict[str, Any]:
    _require(isinstance(document, dict), "source input inventory root must be an object")
    _require(
        tuple(document) == INVENTORY_KEYS,
        "source input inventory schema or field order is invalid",
    )
    _require(document["schema_version"] == 1, "inventory schema_version must be 1")
    _require(document["target_phase"] == TARGET_PHASE, "inventory target phase is invalid")
    _require(document["baseline_commit"] == BASELINE_COMMIT, "inventory baseline is invalid")
    _require(document["inventory_scope"] == INVENTORY_SCOPE, "inventory scope is invalid")
    for field in (
        "release_gate_reconsideration_allowed",
        "legal_compliance_certified",
        "source_assets_created",
    ):
        _require(document[field] is False, f"{field} must remain false")

    packages = document["packages"]
    _require(isinstance(packages, list), "inventory packages must be an array")
    package_ids = [_record_id(item, "inventory package") for item in packages]
    correspondence_packages = {
        item["id"]: item for item in correspondence["packages"]
    }
    requirement_kits = {item["id"]: item for item in requirements["kits"]}
    expected_ids = sorted(correspondence_packages)
    _require(
        package_ids == expected_ids,
        "inventory package IDs are missing, duplicated, or unsorted",
    )
    for package in packages:
        package_id = package["id"]
        _validate_inventory_package(
            package,
            correspondence_packages[package_id],
            requirement_kits[package_id],
        )
    _verify_hygiene(document, "source input inventory")
    return document


def _validate_inventory_package(
    package: dict[str, Any],
    correspondence_package: dict[str, Any],
    requirements: dict[str, Any],
) -> None:
    package_id = package["id"]
    _require(
        tuple(package) == PACKAGE_KEYS,
        f"{package_id} inventory package schema or field order is invalid",
    )
    _require(
        package["binary_package_sha256"]
        == correspondence_package["binary_package"]["sha256"]
        == requirements["binary_package_sha256"],
        f"{package_id} binary package hash is inconsistent",
    )
    _validate_core_source(package_id, package["core_source"], correspondence_package)

    components = package["external_components"]
    _require(isinstance(components, list), f"{package_id} external components must be an array")
    component_ids = [_record_id(item, f"{package_id} external component") for item in components]
    expected_components = {
        item["id"]: item for item in correspondence_package["external_components"]
    }
    _require(
        component_ids == sorted(expected_components),
        f"{package_id} external components are missing, invented, duplicated, or unsorted",
    )
    for component in components:
        _validate_external_component(
            package_id,
            component,
            expected_components[component["id"]],
            correspondence_package["provider"]["name"],
        )

    required_external = {
        item.removeprefix("external-source:")
        for item in requirements["required_source_items"]
        if item.startswith("external-source:")
    }
    static_components = {
        item["id"]
        for item in correspondence_package["external_components"]
        if item["linkage"] == "static"
    }
    _require(
        required_external == static_components,
        f"{package_id} required static source items are inconsistent",
    )
    _require(
        required_external.issubset(set(component_ids)),
        f"{package_id} required source item disappeared from inventory",
    )
    expected_core = f"core-source:{package_id}@{correspondence_package['core_source']['commit']}"
    _require(
        expected_core in requirements["required_source_items"],
        f"{package_id} exact core source requirement is missing",
    )

    _validate_toolchain(package_id, package["toolchain"])
    _validate_build_orchestration(package_id, package["build_orchestration"])
    _require(package["package_status"] == "blocked", f"{package_id} package must remain blocked")
    _require(
        _sorted_nonempty_strings(package["blockers"]),
        f"{package_id} package blockers are missing, duplicated, or unsorted",
    )


def _validate_core_source(
    package_id: str,
    core: Any,
    correspondence_package: dict[str, Any],
) -> None:
    expected = correspondence_package["core_source"]
    _require(
        isinstance(core, dict) and tuple(core) == CORE_SOURCE_KEYS,
        f"{package_id} core source schema or field order is invalid",
    )
    for field in ("repository", "commit", "archive_sha256", "license_path"):
        _require(core[field] == expected[field], f"{package_id} core source {field} changed")
    _require(COMMIT_RE.fullmatch(core["commit"]) is not None, f"{package_id} core commit is not immutable")
    _require(HASH_RE.fullmatch(core["archive_sha256"]) is not None, f"{package_id} core archive hash is invalid")
    _validate_evidence(core["evidence"], f"{package_id} core source")
    _require(
        any(item["status"] == "verified" for item in core["evidence"]),
        f"{package_id} core source lacks verified evidence",
    )
    _require(core["status"] == "verified", f"{package_id} core source must remain verified")
    _require(core["blockers"] == [], f"{package_id} verified core source must not retain blockers")


def _validate_external_component(
    package_id: str,
    component: Any,
    expected: dict[str, Any],
    provider_name: str,
) -> None:
    _require(isinstance(component, dict), f"{package_id} external component must be an object")
    component_id = component["id"]
    _require(
        tuple(component) == EXTERNAL_COMPONENT_KEYS,
        f"{package_id} external component schema is invalid: {component_id}",
    )
    _require(ID_RE.fullmatch(component_id) is not None, f"{package_id} component ID is invalid")
    _require(component["linkage"] == expected["linkage"], f"{package_id} linkage changed: {component_id}")
    provider_version = component["provider_version"]
    expected_version = expected["version"]
    if expected_version == "unverified":
        _require(provider_version == "unresolved", f"{package_id} guessed component version: {component_id}")
        _require(component["version_status"] == "unresolved", f"{package_id} version status is unsupported: {component_id}")
    else:
        _require(provider_version == expected_version, f"{package_id} provider version changed: {component_id}")
        _require(
            component["version_status"] in {"provider-identified", "verified"},
            f"{package_id} provider version status is invalid: {component_id}",
        )
    _require(component["version_status"] in VERSION_STATUSES, f"{package_id} version status is invalid: {component_id}")

    repository = component["upstream_repository"]
    immutable_ref = component["immutable_ref"]
    archive_hash = component["source_archive_sha256"]
    for value, label in (
        (repository, "upstream repository"),
        (immutable_ref, "immutable ref"),
        (archive_hash, "source archive hash"),
    ):
        _require(isinstance(value, str) and value, f"{package_id} {label} is invalid: {component_id}")
    _require(immutable_ref.casefold() not in MUTABLE_REFS, f"{package_id} mutable ref is forbidden: {component_id}")
    if immutable_ref not in {"unresolved", "not-applicable"}:
        _require(COMMIT_RE.fullmatch(immutable_ref) is not None, f"{package_id} immutable ref is malformed: {component_id}")
    if archive_hash not in {"unresolved", "not-applicable"}:
        _require(HASH_RE.fullmatch(archive_hash) is not None, f"{package_id} source archive hash is invalid: {component_id}")

    _validate_evidence(component["evidence"], f"{package_id} component {component_id}")
    _require(
        any(item["authority"] == provider_name for item in component["evidence"]),
        f"{package_id} provider evidence is missing: {component_id}",
    )
    if provider_version != "unresolved":
        _require(
            any(provider_version in item["claim"] for item in component["evidence"]),
            f"{package_id} provider version lacks primary evidence: {component_id}",
        )

    resolution = component["resolution_status"]
    _require(resolution in RESOLUTION_STATUSES, f"{package_id} resolution status is invalid: {component_id}")
    blockers = component["blockers"]
    if resolution == "verified-immutable-input":
        _require(component["version_status"] == "verified", f"{package_id} verified input lacks verified version: {component_id}")
        _require(repository not in {"unresolved", "not-applicable"}, f"{package_id} verified repository is missing: {component_id}")
        _require(COMMIT_RE.fullmatch(immutable_ref) is not None, f"{package_id} verified immutable ref is missing: {component_id}")
        _require(any(item["status"] == "verified" for item in component["evidence"]), f"{package_id} verified primary evidence is missing: {component_id}")
        _require(blockers == [], f"{package_id} verified input must not retain blockers: {component_id}")
    else:
        _require(
            _sorted_nonempty_strings(blockers),
            f"{package_id} partial or unresolved input requires blockers: {component_id}",
        )
        _require(repository == "unresolved", f"{package_id} unverified repository claim is unsupported: {component_id}")
        _require(immutable_ref == "unresolved", f"{package_id} unverified ref claim is unsupported: {component_id}")
        _require(archive_hash == "unresolved", f"{package_id} unverified archive hash claim is unsupported: {component_id}")
    if resolution == "identified-version-only":
        _require(component["version_status"] == "provider-identified", f"{package_id} version-only input is inconsistent: {component_id}")
    if resolution == "identified-name-only":
        _require(component["version_status"] == "unresolved", f"{package_id} name-only input has a guessed version: {component_id}")
    if resolution == "system-component-candidate":
        _require(component["linkage"] == "system", f"{package_id} system candidate linkage is invalid: {component_id}")


def _validate_toolchain(package_id: str, toolchain: Any) -> None:
    _require(
        isinstance(toolchain, dict) and tuple(toolchain) == TOOLCHAIN_KEYS,
        f"{package_id} toolchain schema or field order is invalid",
    )
    for field in ("host", "compiler", "compiler_version"):
        _require(isinstance(toolchain[field], str) and toolchain[field], f"{package_id} toolchain {field} is invalid")
    _require(_sorted_strings(toolchain["supporting_tools"]), f"{package_id} supporting tools are duplicated or unsorted")
    _validate_evidence(toolchain["evidence"], f"{package_id} toolchain")
    _validate_assessment_status(package_id, "toolchain", toolchain)


def _validate_build_orchestration(package_id: str, build: Any) -> None:
    _require(
        isinstance(build, dict) and tuple(build) == BUILD_ORCHESTRATION_KEYS,
        f"{package_id} build orchestration schema or field order is invalid",
    )
    for field in (
        "provider_repository",
        "immutable_ref",
        "exact_configuration",
        "patch_status",
        "reproducible_entrypoint",
    ):
        _require(isinstance(build[field], str) and build[field], f"{package_id} build orchestration {field} is invalid")
    _require(build["immutable_ref"].casefold() not in MUTABLE_REFS, f"{package_id} build orchestration uses a mutable ref")
    if build["immutable_ref"] not in {"unresolved", "not-applicable"}:
        _require(COMMIT_RE.fullmatch(build["immutable_ref"]) is not None, f"{package_id} build orchestration ref is malformed")
    _validate_evidence(build["evidence"], f"{package_id} build orchestration")
    _validate_assessment_status(package_id, "build orchestration", build)


def _validate_assessment_status(package_id: str, label: str, record: dict[str, Any]) -> None:
    status_value = record["status"]
    _require(status_value in ASSESSMENT_STATUSES, f"{package_id} {label} status is invalid")
    if status_value == "verified":
        _require(record["blockers"] == [], f"{package_id} verified {label} must not retain blockers")
    else:
        _require(_sorted_nonempty_strings(record["blockers"]), f"{package_id} {label} blockers are missing")


def _validate_evidence(evidence: Any, label: str) -> None:
    _require(isinstance(evidence, list) and evidence, f"{label} evidence is missing")
    sort_keys: list[tuple[str, str, str, str, str]] = []
    for record in evidence:
        _require(isinstance(record, dict) and tuple(record) == EVIDENCE_KEYS, f"{label} evidence schema is invalid")
        for field in ("kind", "authority", "locator", "claim"):
            _require(isinstance(record[field], str) and record[field], f"{label} evidence {field} is invalid")
        _require(record["status"] in EVIDENCE_STATUSES, f"{label} evidence status is invalid")
        _verify_hygiene(record, f"{label} evidence")
        sort_keys.append(tuple(record[field] for field in EVIDENCE_KEYS))
    _require(sort_keys == sorted(set(sort_keys)), f"{label} evidence is duplicated or unsorted")


def generate_feasibility(
    correspondence: dict[str, Any],
    requirements: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    validate_inventory_document(inventory, correspondence, requirements)
    package_results = []
    for package in inventory["packages"]:
        components = package["external_components"]
        static_components = sorted(item["id"] for item in components if item["linkage"] == "static")
        system_components = sorted(item["id"] for item in components if item["linkage"] == "system")
        verified = sorted(
            item["id"]
            for item in components
            if item["resolution_status"] == "verified-immutable-input"
        )
        partial = sorted(
            item["id"]
            for item in components
            if item["resolution_status"] in PARTIAL_RESOLUTIONS
        )
        unresolved = sorted(
            item["id"]
            for item in components
            if item["resolution_status"] == "unresolved"
        )
        _require(
            len(verified) + len(partial) + len(unresolved) == len(components),
            f"{package['id']} feasibility classification is incomplete",
        )
        blockers = sorted(
            set(
                package["blockers"]
                + package["toolchain"]["blockers"]
                + package["build_orchestration"]["blockers"]
            )
        )
        next_actions = sorted(
            {
                "Document patch or explicit no-modification evidence and source-archive manifests.",
                "Request separate Phase 6B2B2B authorization only after every blocker is resolved.",
                "Resolve exact toolchain versions and reproducible build orchestration.",
                "Resolve partial and unresolved external inputs using primary-source evidence.",
            }
        )
        package_results.append(
            {
                "id": package["id"],
                "binary_package_sha256": package["binary_package_sha256"],
                "total_external_components": len(components),
                "static_components": static_components,
                "system_components": system_components,
                "verified_immutable_inputs": verified,
                "partially_identified_inputs": partial,
                "unresolved_inputs": unresolved,
                "toolchain_status": package["toolchain"]["status"],
                "build_orchestration_status": package["build_orchestration"]["status"],
                "source_kit_status": "not-ready",
                "blockers": blockers,
                "next_actions": next_actions,
            }
        )
    result = {
        "schema_version": 1,
        "target_phase": TARGET_PHASE,
        "baseline_commit": BASELINE_COMMIT,
        "release_gate_reconsideration_allowed": False,
        "legal_compliance_certified": False,
        "source_assets_created": False,
        "source_kits_ready": False,
        "assembly_authorized": False,
        "packages": package_results,
        "overall_status": "blocked-inventory-recorded",
        "next_phase": "6B2B2B-not-authorized",
    }
    _verify_hygiene(result, "generated source kit feasibility")
    return result


def validate_feasibility_document(
    document: Any,
    correspondence: dict[str, Any],
    requirements: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    _require(isinstance(document, dict), "source kit feasibility root must be an object")
    _require(tuple(document) == FEASIBILITY_KEYS, "source kit feasibility schema or field order is invalid")
    _require(document["schema_version"] == 1, "feasibility schema_version must be 1")
    _require(document["target_phase"] == TARGET_PHASE, "feasibility target phase is invalid")
    _require(document["baseline_commit"] == BASELINE_COMMIT, "feasibility baseline is invalid")
    for field in (
        "release_gate_reconsideration_allowed",
        "legal_compliance_certified",
        "source_assets_created",
        "source_kits_ready",
        "assembly_authorized",
    ):
        _require(document[field] is False, f"feasibility {field} must remain false")
    _require(document["overall_status"] == "blocked-inventory-recorded", "feasibility overall status is invalid")
    _require(document["next_phase"] == "6B2B2B-not-authorized", "feasibility next phase is invalid")
    packages = document["packages"]
    _require(isinstance(packages, list), "feasibility packages must be an array")
    _require(
        [_record_id(item, "feasibility package") for item in packages] == ["aria2", "ffmpeg"],
        "feasibility package IDs are missing, duplicated, or unsorted",
    )
    for package in packages:
        _require(
            tuple(package) == FEASIBILITY_PACKAGE_KEYS,
            f"{package['id']} feasibility package schema or field order is invalid",
        )
        for field in (
            "static_components",
            "system_components",
            "verified_immutable_inputs",
            "partially_identified_inputs",
            "unresolved_inputs",
            "blockers",
            "next_actions",
        ):
            _require(_sorted_strings(package[field]), f"{package['id']} feasibility {field} is duplicated or unsorted")
        _require(package["source_kit_status"] == "not-ready", f"{package['id']} source kit must remain not ready")
        _require(package["blockers"], f"{package['id']} feasibility blockers are missing")
    expected = generate_feasibility(correspondence, requirements, inventory)
    _require(document == expected, "committed source kit feasibility is stale or inconsistent")
    _verify_hygiene(document, "source kit feasibility")
    return document


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    resolved = _require_regular_input(path, label)
    raw = resolved.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{label} contains a UTF-8 BOM")
    _require(b"\r" not in raw, f"{label} must use LF line endings")
    _require(b"\0" not in raw, f"{label} contains NUL")
    decoded = raw.decode("utf-8")
    _require(decoded.endswith("\n") and not decoded.endswith("\n\n"), f"{label} must have exactly one final newline")
    document = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    _require(isinstance(document, dict), f"{label} root must be an object")
    return document, raw


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceKitFeasibilityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _write_atomic(path: Path, payload: bytes, *, protected_inputs: set[Path]) -> None:
    output = _validate_output_path(path)
    _require(output not in protected_inputs, "output path collides with a protected input")
    handle = tempfile.NamedTemporaryFile(
        prefix=".source-kit-feasibility-",
        suffix=".tmp",
        dir=output.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _require_regular_input(path: Path, label: str) -> Path:
    _require(".." not in path.parts, f"{label} path contains parent traversal")
    candidate = _lexical_absolute(path)
    _require(candidate.is_file(), f"{label} is unavailable")
    _require(not _path_or_existing_parent_is_reparse(candidate), f"{label} uses a symlink or reparse point")
    return candidate


def _validate_output_path(path: Path) -> Path:
    _require(".." not in path.parts, "output path contains parent traversal")
    output = _lexical_absolute(path)
    _require(output.parent.is_dir(), "output parent is unavailable")
    _require(not _path_or_existing_parent_is_reparse(output.parent), "output parent uses a symlink or reparse point")
    if output.exists():
        _require(output.is_file(), "output path is not a regular file")
        _require(not _is_reparse(output), "output path uses a symlink or reparse point")
    return output


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _path_or_existing_parent_is_reparse(path: Path) -> bool:
    current = path
    while True:
        if current.exists() and _is_reparse(current):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _verify_hygiene(value: Any, label: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    _require(LOCAL_PATH_RE.search(serialized) is None, f"local absolute path in {label}")
    _require(TIMESTAMP_RE.search(serialized) is None, f"timestamp in {label}")
    _require(not any(pattern.search(serialized) for pattern in SECRET_RES), f"secret-like value in {label}")
    _require(not any(pattern.search(serialized) for pattern in UNSUPPORTED_CLAIM_RES), f"unsupported readiness or compliance claim in {label}")


def _record_id(record: Any, label: str) -> str:
    _require(isinstance(record, dict), f"{label} record must be an object")
    value = record.get("id")
    _require(isinstance(value, str), f"{label} ID is invalid")
    return value


def _sorted_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    )


def _sorted_nonempty_strings(value: Any) -> bool:
    return bool(value) and _sorted_strings(value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceKitFeasibilityError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
