from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


GENERATOR_ID = "s9h-project-owned-deterministic-spdx-generator"
GENERATOR_VERSION = "1.0.0"
SPDX_VERSION = "SPDX-2.3"
DATA_LICENSE = "CC0-1.0"
PREDICATE_TYPE = "https://spdx.dev/Document/v2.3"
SCHEMA_BLOB_SHA1 = "ee61e6686e885f8139c132647fd0b4f483b8fb81"
SCHEMA_REPOSITORY = "spdx/spdx-spec"
SCHEMA_TAG = "v2.3"
SCHEMA_COMMIT = "aadf3b0b8dbbabdb4d880b0fc714255fea436ff7"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "spdx-2.3" / "spdx-schema.json"

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
REQUIRED_EXTERNAL_RUNTIMES = {"aria2", "deno", "ffmpeg", "ffprobe", "yt-dlp"}
AUTHORITATIVE_FIELDS = {
    "version",
    "supplier",
    "origin",
    "license_declared",
    "license_concluded",
    "download_location",
    "purl",
}
INPUT_KEYS = {
    "schema_version",
    "evidence_type",
    "synthetic",
    "distribution_allowed",
    "release",
    "final_executable",
    "final_artifacts",
    "portable_files",
    "pyinstaller_inventory",
    "python_runtime",
    "python_packages",
    "native_members",
    "external_runtimes",
    "legal_components",
    "release_manifest",
    "checksum_records",
    "unresolved_components",
}
RELEASE_KEYS = {
    "product",
    "version",
    "tag",
    "source_commit",
    "control_commit",
    "created_utc",
}
FILE_KEYS = {"path", "size", "sha256", "component_id", "unresolved_id"}
ARTIFACT_KEYS = FILE_KEYS | {"role"}
COMPONENT_KEYS = {
    "id",
    "name",
    *AUTHORITATIVE_FIELDS,
    "field_provenance",
    "unresolved_fields",
}
UNRESOLVED_KEYS = {"id", "component_id", "fields", "reason", "source", "provenance"}


class SbomError(RuntimeError):
    pass


def load_input(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    _require_utf8_lf(raw, path.name)
    value = _load_strict_json(raw)
    if raw != canonical_json_bytes(value):
        raise SbomError("SBOM input is not canonical JSON")
    return validate_input(value)


def validate_input(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != INPUT_KEYS:
        raise SbomError("SBOM input fields are invalid")
    if value["schema_version"] != 1:
        raise SbomError("SBOM input schema version is invalid")
    if value["evidence_type"] not in {"synthetic-ci", "production-release"}:
        raise SbomError("SBOM evidence type is invalid")
    if type(value["synthetic"]) is not bool or type(value["distribution_allowed"]) is not bool:
        raise SbomError("SBOM evidence flags are invalid")
    if value["synthetic"]:
        if value["evidence_type"] != "synthetic-ci" or value["distribution_allowed"]:
            raise SbomError("synthetic fixture cannot be represented as production evidence")
    elif value["evidence_type"] != "production-release" or not value["distribution_allowed"]:
        raise SbomError("production evidence flags are invalid")

    release = _require_object(value["release"], RELEASE_KEYS, "release")
    _validate_release(release)
    components = _validate_components(value["legal_components"])
    unresolved = _validate_unresolved(value["unresolved_components"], components)

    final_artifacts = _validate_file_records(
        value["final_artifacts"],
        "final artifact inventory",
        components,
        unresolved,
        artifact=True,
    )
    if not final_artifacts:
        raise SbomError("final package inventory is missing")
    executable = _validate_file_record(
        value["final_executable"],
        "final executable",
        components,
        unresolved,
        artifact=True,
    )
    executable_matches = [
        item for item in final_artifacts
        if item["path"] == executable["path"] and item["role"] == "application-executable"
    ]
    if executable_matches != [executable]:
        raise SbomError("final executable identity does not match the artifact inventory")

    portable_files = _validate_file_records(
        value["portable_files"],
        "portable package inventory",
        components,
        unresolved,
        artifact=False,
    )
    if not portable_files:
        raise SbomError("portable package inventory is missing")
    portable_by_path = {item["path"]: item for item in portable_files}
    if not value["synthetic"] and any(
        path.casefold() == "synthetic.txt" for path in portable_by_path
    ):
        raise SbomError("synthetic fixture cannot be represented as production evidence")

    pyinstaller = _require_object(
        value["pyinstaller_inventory"],
        {"executable_path", "carchive_members", "source_inventory"},
        "PyInstaller inventory",
    )
    if pyinstaller["executable_path"] != executable["path"]:
        raise SbomError("PyInstaller executable inventory does not match the final executable")
    carchive = _validate_path_list(pyinstaller["carchive_members"], "CArchive members")
    source_inventory = _validate_source_inventory(pyinstaller["source_inventory"])
    if not carchive or carchive != [item["path"] for item in source_inventory]:
        raise SbomError("PyInstaller inventory/source mismatch")
    for item in source_inventory:
        portable = portable_by_path.get(item["path"])
        if portable is None or portable["sha256"] != item["sha256"]:
            raise SbomError("PyInstaller inventory/source mismatch")

    python_runtime = _validate_component_files(
        value["python_runtime"], "Python runtime", components, portable_by_path
    )
    if python_runtime["component_id"] != "python-runtime":
        raise SbomError("Python runtime is omitted")
    python_packages = _validate_component_file_list(
        value["python_packages"], "Python packages", components, portable_by_path
    )
    if not python_packages:
        raise SbomError("distributed Python package inventory is missing")
    native_members = _validate_path_list(value["native_members"], "native members")
    expected_native = sorted(
        path
        for path in portable_by_path
        if PurePosixPath(path).suffix.casefold() in {".dll", ".exe", ".pyd"}
    )
    if sorted(native_members) != expected_native:
        raise SbomError("native member inventory is invalid")

    runtimes = _validate_component_file_list(
        value["external_runtimes"], "external runtimes", components, portable_by_path
    )
    runtime_ids = {item["component_id"] for item in runtimes}
    if runtime_ids != REQUIRED_EXTERNAL_RUNTIMES:
        raise SbomError("required external runtime is omitted")

    associated_paths: dict[str, str] = {}
    for item in [python_runtime, *python_packages, *runtimes]:
        for path in item["files"]:
            previous = associated_paths.setdefault(path, item["component_id"])
            if previous != item["component_id"]:
                raise SbomError("portable file has conflicting component associations")
    for record in portable_files:
        path = record["path"]
        expected = associated_paths.get(path)
        if record["component_id"] is not None:
            if expected is not None and expected != record["component_id"]:
                raise SbomError("portable file component association is inconsistent")
        elif record["unresolved_id"] is None:
            raise SbomError("unaccounted distributed file")

    _validate_release_manifest(
        value["release_manifest"],
        release,
        final_artifacts,
    )
    _validate_checksum_records(value["checksum_records"], final_artifacts)

    normalized = _normalize_input(value)
    return normalized


def generate_document(value: object) -> dict[str, Any]:
    evidence = validate_input(value)
    release = evidence["release"]
    components = {item["id"]: item for item in evidence["legal_components"]}
    unresolved = {item["id"]: item for item in evidence["unresolved_components"]}
    input_digest = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()

    package_ids = {
        component_id: _spdx_id("Package", component_id)
        for component_id in components
    }
    packages = [
        _component_package(component, package_ids[component["id"]], evidence)
        for component in evidence["legal_components"]
    ]

    file_records: list[tuple[str, dict[str, Any], str]] = []
    for record in evidence["final_artifacts"]:
        file_records.append(("artifact", record, record["component_id"] or "release"))
    for record in evidence["portable_files"]:
        owner = record["component_id"]
        if owner is None:
            owner = f"unresolved:{record['unresolved_id']}"
        file_records.append(("portable", record, owner))

    files = []
    file_ids: dict[tuple[str, str], str] = {}
    for boundary, record, owner in file_records:
        key = (boundary, record["path"])
        spdx_id = _spdx_id("File", f"{boundary}:{record['path']}")
        file_ids[key] = spdx_id
        comment = f"boundary={boundary}; component={owner}"
        if record["unresolved_id"] is not None:
            item = unresolved[record["unresolved_id"]]
            comment += (
                f"; unresolved_reason={item['reason']}; unresolved_source={item['source']};"
                f" unresolved_provenance={item['provenance']}"
            )
        files.append(
            {
                "SPDXID": spdx_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": record["sha256"]}],
                "comment": comment,
                "copyrightText": "NOASSERTION",
                "fileName": f"./{boundary}/{record['path']}",
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
            }
        )

    release_id = package_ids["release"]
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": release_id,
        }
    ]
    for component_id in sorted(package_ids):
        if component_id != "release":
            relationships.append(
                {
                    "spdxElementId": release_id,
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": package_ids[component_id],
                }
            )
    for boundary, record, owner in file_records:
        container = release_id
        if record["component_id"] is not None:
            container = package_ids[record["component_id"]]
        relationships.append(
            {
                "spdxElementId": container,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_ids[(boundary, record["path"])],
            }
        )

    document_name = expected_filename(release["version"])
    namespace = (
        "https://spdx.org/spdxdocs/"
        f"s9h-youtube-downloaderbs-{release['version']}-{release['source_commit'][:12]}-"
        f"{input_digest[:20]}"
    )
    comment = canonical_json_text(
        {
            "control_commit": release["control_commit"],
            "evidence_type": evidence["evidence_type"],
            "generator": f"{GENERATOR_ID}@{GENERATOR_VERSION}",
            "input_sha256": input_digest,
            "predicate_type": PREDICATE_TYPE,
            "release_tag": release["tag"],
            "source_commit": release["source_commit"],
            "synthetic": evidence["synthetic"],
        }
    ).rstrip("\n")
    document = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "comment": comment,
        "creationInfo": {
            "created": release["created_utc"],
            "creators": [f"Tool: {GENERATOR_ID}-{GENERATOR_VERSION}"],
        },
        "dataLicense": DATA_LICENSE,
        "documentNamespace": namespace,
        "files": sorted(files, key=lambda item: item["SPDXID"]),
        "name": document_name,
        "packages": sorted(packages, key=lambda item: item["SPDXID"]),
        "relationships": sorted(
            relationships,
            key=lambda item: (
                item["spdxElementId"],
                item["relationshipType"],
                item["relatedSpdxElement"],
            ),
        ),
        "spdxVersion": SPDX_VERSION,
    }
    return document


def generate_bytes(value: object, *, schema_path: Path = SCHEMA_PATH) -> bytes:
    document = generate_document(value)
    validate_schema(document, schema_path=schema_path)
    result = canonical_json_bytes(document)
    if result != canonical_json_bytes(generate_document(value)):
        raise SbomError("SBOM output is nondeterministic")
    return result


def verify_document(
    sbom_bytes: bytes,
    value: object,
    *,
    schema_path: Path = SCHEMA_PATH,
    final_manifest: object | None = None,
    final_checksum_bytes: bytes | None = None,
) -> dict[str, Any]:
    _require_utf8_lf(sbom_bytes, "SPDX document")
    document = _load_strict_json(sbom_bytes)
    if sbom_bytes != canonical_json_bytes(document):
        raise SbomError("SPDX document is not canonical JSON")
    validate_schema(document, schema_path=schema_path)
    evidence = validate_input(value)
    expected = generate_document(evidence)
    if document != expected:
        raise SbomError("SPDX semantic reconciliation failed")
    _verify_semantic_relationships(document, evidence)
    if final_manifest is not None or final_checksum_bytes is not None:
        if final_manifest is None or final_checksum_bytes is None:
            raise SbomError("release manifest and checksum reconciliation must be available together")
        reconcile_final_bundle(sbom_bytes, evidence, final_manifest, final_checksum_bytes)
    return document


def validate_schema(document: object, *, schema_path: Path = SCHEMA_PATH) -> None:
    schema_bytes = schema_path.read_bytes()
    if _git_blob_sha1(schema_bytes) != SCHEMA_BLOB_SHA1:
        raise SbomError("SPDX schema immutable identity is invalid")
    try:
        import fastjsonschema
    except ImportError as exc:
        raise SbomError("real SPDX schema validation capability is unavailable") from exc
    try:
        validator = fastjsonschema.compile(json.loads(schema_bytes.decode("utf-8")))
        validator(document)
    except (ValueError, TypeError, fastjsonschema.JsonSchemaException) as exc:
        raise SbomError(f"SPDX schema validation failed: {exc}") from exc


def reconcile_final_bundle(
    sbom_bytes: bytes,
    evidence: dict[str, Any],
    manifest: object,
    checksum_bytes: bytes,
) -> None:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), list):
        raise SbomError("release manifest mismatch")
    checksum_records = _parse_checksum_bytes(checksum_bytes)
    base_manifest = evidence["release_manifest"]
    if set(manifest) != set(base_manifest):
        raise SbomError("release manifest mismatch")
    for key in set(base_manifest) - {"assets", "checksum_file"}:
        if manifest[key] != base_manifest[key]:
            raise SbomError("release manifest mismatch")
    checksum_record = manifest["checksum_file"]
    if (
        not isinstance(checksum_record, dict)
        or set(checksum_record) != {"name", "size", "sha256"}
        or checksum_record["name"] != "SHA256SUMS.txt"
        or checksum_record["size"] != len(checksum_bytes)
        or checksum_record["sha256"] != hashlib.sha256(checksum_bytes).hexdigest()
    ):
        raise SbomError("release manifest mismatch")
    asset_names = [
        item.get("name") if isinstance(item, dict) else None
        for item in manifest["assets"]
    ]
    if (
        any(not isinstance(name, str) for name in asset_names)
        or asset_names != sorted(asset_names)
        or len(asset_names) != len(set(asset_names))
    ):
        raise SbomError("release manifest mismatch")
    sbom_name = expected_filename(evidence["release"]["version"])
    matches = [
        item for item in manifest["assets"]
        if isinstance(item, dict)
        and item.get("name") == sbom_name
        and item.get("role") == "release-sbom"
    ]
    if len(matches) != 1:
        raise SbomError("SBOM is missing from the integrated release bundle")
    record = matches[0]
    digest = hashlib.sha256(sbom_bytes).hexdigest()
    if (
        record.get("sha256") != digest
        or record.get("size") != len(sbom_bytes)
        or checksum_records.get(sbom_name) != digest
    ):
        raise SbomError("SBOM bytes differ from manifest or checksum evidence")
    expected_base = base_manifest["assets"]
    actual_base = [item for item in manifest["assets"] if item.get("role") != "release-sbom"]
    if actual_base != expected_base:
        raise SbomError("release manifest mismatch")
    if {
        name: digest_value
        for name, digest_value in checksum_records.items()
        if name != sbom_name
    } != {item["name"]: item["sha256"] for item in evidence["checksum_records"]}:
        raise SbomError("checksum input does not match the integrated release bundle")


def expected_filename(version: str) -> str:
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise SbomError("release version is invalid")
    return f"Youtube-Downloaderbs-v{version}.spdx.json"


def canonical_json_bytes(value: object) -> bytes:
    return canonical_json_text(value).encode("utf-8")


def canonical_json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _normalize_input(value: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(value))
    for key in (
        "final_artifacts",
        "portable_files",
        "native_members",
        "legal_components",
        "unresolved_components",
        "checksum_records",
    ):
        normalized[key] = sorted(normalized[key], key=_sort_key)
    for key in ("python_packages", "external_runtimes"):
        for item in normalized[key]:
            item["files"] = sorted(item["files"], key=str.casefold)
        normalized[key] = sorted(normalized[key], key=lambda item: item["component_id"])
    normalized["python_runtime"]["files"] = sorted(
        normalized["python_runtime"]["files"], key=str.casefold
    )
    normalized["pyinstaller_inventory"]["carchive_members"] = sorted(
        normalized["pyinstaller_inventory"]["carchive_members"], key=str.casefold
    )
    normalized["pyinstaller_inventory"]["source_inventory"] = sorted(
        normalized["pyinstaller_inventory"]["source_inventory"],
        key=lambda item: item["path"].casefold(),
    )
    normalized["release_manifest"]["assets"] = sorted(
        normalized["release_manifest"]["assets"], key=lambda item: item["name"]
    )
    normalized["release_manifest"]["release_blockers"] = sorted(
        normalized["release_manifest"]["release_blockers"]
    )
    return normalized


def _sort_key(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        key = value.get("path", value.get("id", value.get("name", "")))
        return (str(key).casefold(), canonical_json_text(value))
    return (str(value).casefold(), str(value))


def _validate_release(release: dict[str, Any]) -> None:
    for key in RELEASE_KEYS:
        if not isinstance(release[key], str) or not release[key]:
            raise SbomError(f"release {key} is invalid")
    if VERSION_PATTERN.fullmatch(release["version"]) is None:
        raise SbomError("release version is invalid")
    if release["tag"] != f"v{release['version']}" or TAG_PATTERN.fullmatch(release["tag"]) is None:
        raise SbomError("release tag is invalid")
    if COMMIT_PATTERN.fullmatch(release["source_commit"]) is None:
        raise SbomError("source commit is invalid")
    if COMMIT_PATTERN.fullmatch(release["control_commit"]) is None:
        raise SbomError("control commit is invalid")
    try:
        created = datetime.fromisoformat(release["created_utc"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise SbomError("release creation time is invalid") from exc
    if not release["created_utc"].endswith("Z") or created.utcoffset().total_seconds() != 0:
        raise SbomError("release creation time must be explicit UTC evidence")


def _validate_components(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SbomError("authoritative legal-component evidence is missing")
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        component = _require_object(item, COMPONENT_KEYS, "component")
        component_id = _canonical_id(component["id"], "component")
        if component_id in result:
            raise SbomError("component IDs are duplicated")
        if not isinstance(component["name"], str) or not component["name"]:
            raise SbomError("component name is invalid")
        provenance = _require_object(
            component["field_provenance"], AUTHORITATIVE_FIELDS, "field provenance"
        )
        unresolved = component["unresolved_fields"]
        if not isinstance(unresolved, dict):
            raise SbomError("component unresolved fields are invalid")
        for field in AUTHORITATIVE_FIELDS:
            field_value = component[field]
            if not isinstance(field_value, str) or not field_value:
                raise SbomError(f"component {field} is invalid")
            if not isinstance(provenance[field], str) or not provenance[field]:
                raise SbomError(f"component {field} lacks authoritative provenance")
            if field_value == "NOASSERTION":
                unresolved_id = unresolved.get(field)
                if not isinstance(unresolved_id, str) or not unresolved_id:
                    raise SbomError(f"component {field} lacks an unresolved record")
            elif field in unresolved:
                raise SbomError(f"component {field} has a spurious unresolved record")
        if set(unresolved) - AUTHORITATIVE_FIELDS:
            raise SbomError("component unresolved field name is invalid")
        result[component_id] = component
    required = {"release", "application", "python-runtime", *REQUIRED_EXTERNAL_RUNTIMES}
    if not required.issubset(result):
        raise SbomError("required component evidence is missing")
    return result


def _validate_unresolved(
    value: object,
    components: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise SbomError("unresolved-component records are invalid")
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        record = _require_object(item, UNRESOLVED_KEYS, "unresolved record")
        record_id = _canonical_id(record["id"], "unresolved record")
        if record_id in result:
            raise SbomError("unresolved records are duplicated")
        component_id = record["component_id"]
        if component_id is not None and component_id not in components:
            raise SbomError("unresolved record references an unknown component")
        fields = record["fields"]
        if not isinstance(fields, list) or not fields or any(
            not isinstance(field, str) or not field for field in fields
        ):
            raise SbomError("unresolved record fields are invalid")
        if len(fields) != len(set(fields)):
            raise SbomError("unresolved record fields are duplicated")
        for key in ("reason", "source", "provenance"):
            if not isinstance(record[key], str) or not record[key]:
                raise SbomError("unresolved record reason/source/provenance is required")
        result[record_id] = record
    for component in components.values():
        for field, unresolved_id in component["unresolved_fields"].items():
            record = result.get(unresolved_id)
            if (
                record is None
                or record["component_id"] != component["id"]
                or field not in record["fields"]
            ):
                raise SbomError("component unresolved field provenance is inconsistent")
    return result


def _validate_file_records(
    value: object,
    label: str,
    components: dict[str, dict[str, Any]],
    unresolved: dict[str, dict[str, Any]],
    *,
    artifact: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SbomError(f"{label} is invalid")
    result = [
        _validate_file_record(item, label, components, unresolved, artifact=artifact)
        for item in value
    ]
    _require_unique_paths(result, label)
    return result


def _validate_file_record(
    value: object,
    label: str,
    components: dict[str, dict[str, Any]],
    unresolved: dict[str, dict[str, Any]],
    *,
    artifact: bool,
) -> dict[str, Any]:
    expected = ARTIFACT_KEYS if artifact else FILE_KEYS
    record = _require_object(value, expected, label)
    _canonical_path(record["path"], label)
    if type(record["size"]) is not int or record["size"] <= 0:
        raise SbomError(f"{label} size is invalid")
    if not isinstance(record["sha256"], str) or SHA256_PATTERN.fullmatch(record["sha256"]) is None:
        raise SbomError(f"{label} SHA-256 is invalid")
    component_id = record["component_id"]
    unresolved_id = record["unresolved_id"]
    if component_id is not None and unresolved_id is not None:
        raise SbomError(f"{label} has conflicting component and unresolved associations")
    if artifact and component_id is None and unresolved_id is None:
        raise SbomError(f"{label} lacks a component or unresolved association")
    if component_id is not None and component_id not in components:
        raise SbomError(f"{label} references an unknown component")
    if unresolved_id is not None:
        unresolved_record = unresolved.get(unresolved_id)
        if (
            unresolved_record is None
            or unresolved_record["component_id"] is not None
            or "component_association" not in unresolved_record["fields"]
        ):
            raise SbomError(f"{label} unresolved association lacks an explicit record")
    if artifact and (not isinstance(record["role"], str) or not record["role"]):
        raise SbomError("final artifact role is invalid")
    return record


def _require_unique_paths(records: list[dict[str, Any]], label: str) -> None:
    paths = [record["path"] for record in records]
    if len(paths) != len(set(paths)):
        raise SbomError(f"{label} contains a duplicate canonical path")
    folded = [path.casefold() for path in paths]
    if len(folded) != len(set(folded)):
        raise SbomError(f"{label} contains a Windows case collision")


def _validate_path_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SbomError(f"{label} are invalid")
    for item in value:
        _canonical_path(item, label)
    if len(value) != len(set(value)):
        raise SbomError(f"{label} contain duplicate paths")
    if len({item.casefold() for item in value}) != len(value):
        raise SbomError(f"{label} contain a Windows case collision")
    return list(value)


def _validate_source_inventory(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise SbomError("PyInstaller source inventory is invalid")
    result = []
    for item in value:
        record = _require_object(item, {"path", "sha256"}, "PyInstaller source record")
        _canonical_path(record["path"], "PyInstaller source record")
        if not isinstance(record["sha256"], str) or SHA256_PATTERN.fullmatch(record["sha256"]) is None:
            raise SbomError("PyInstaller source SHA-256 is invalid")
        result.append(record)
    if len({item["path"].casefold() for item in result}) != len(result):
        raise SbomError("PyInstaller source inventory contains duplicate paths")
    return result


def _validate_component_files(
    value: object,
    label: str,
    components: dict[str, dict[str, Any]],
    portable_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    item = _require_object(value, {"component_id", "files"}, label)
    component_id = item["component_id"]
    if component_id not in components:
        raise SbomError(f"{label} references an unknown component")
    files = _validate_path_list(item["files"], f"{label} files")
    if not files or any(path not in portable_by_path for path in files):
        raise SbomError(f"{label} files are unavailable")
    if any(portable_by_path[path]["component_id"] != component_id for path in files):
        raise SbomError(f"{label} file association is inconsistent")
    return item


def _validate_component_file_list(
    value: object,
    label: str,
    components: dict[str, dict[str, Any]],
    portable_by_path: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SbomError(f"{label} are invalid")
    result = [
        _validate_component_files(item, label, components, portable_by_path)
        for item in value
    ]
    ids = [item["component_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise SbomError(f"{label} contain duplicate component IDs")
    return result


def _validate_release_manifest(
    value: object,
    release: dict[str, Any],
    final_artifacts: list[dict[str, Any]],
) -> None:
    expected_keys = {
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
    }
    manifest = _require_object(value, expected_keys, "release manifest evidence")
    if (
        manifest["schema_version"] != 2
        or manifest["bundle_format"] != "s9h-release-bundle-v2"
        or manifest["release_tag"] != release["tag"]
        or manifest["source_commit"] != release["source_commit"]
        or manifest["control_commit"] != release["control_commit"]
        or type(manifest["prerelease"]) is not bool
        or manifest["release_ready"] is not False
        or manifest["legal_compliance_certified"] is not False
        or manifest["source_availability_certified"] is not False
        or not isinstance(manifest["release_blockers"], list)
        or not manifest["release_blockers"]
    ):
        raise SbomError("release manifest mismatch")
    assets = manifest["assets"]
    if not isinstance(assets, list):
        raise SbomError("release manifest mismatch")
    for record in assets:
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "role", "size", "sha256"}
            or not isinstance(record["name"], str)
            or not isinstance(record["role"], str)
            or type(record["size"]) is not int
            or record["size"] <= 0
            or not isinstance(record["sha256"], str)
            or SHA256_PATTERN.fullmatch(record["sha256"]) is None
        ):
            raise SbomError("release manifest evidence asset is invalid")
        _canonical_path(record["name"], "release manifest evidence")
    expected_assets = [
        {
            "name": item["path"],
            "role": item["role"],
            "size": item["size"],
            "sha256": item["sha256"],
        }
        for item in final_artifacts
    ]
    if sorted(assets, key=lambda item: item["name"]) != sorted(
        expected_assets, key=lambda item: item["name"]
    ):
        raise SbomError("release manifest mismatch")
    for key in ("checksum_file", "release_notes"):
        record = manifest[key]
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "size", "sha256"}
            or not isinstance(record["name"], str)
            or type(record["size"]) is not int
            or record["size"] <= 0
            or not isinstance(record["sha256"], str)
            or SHA256_PATTERN.fullmatch(record["sha256"]) is None
        ):
            raise SbomError("release manifest evidence record is invalid")
        _canonical_path(record["name"], "release manifest evidence record")
    if (
        manifest["checksum_file"]["name"] != "SHA256SUMS.txt"
        or manifest["release_notes"]["name"] != "RELEASE_NOTES.md"
    ):
        raise SbomError("release manifest evidence record is invalid")


def _validate_checksum_records(value: object, final_artifacts: list[dict[str, Any]]) -> None:
    if not isinstance(value, list) or not value:
        raise SbomError("checksum input is missing")
    for record in value:
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "sha256"}
            or not isinstance(record["name"], str)
            or not isinstance(record["sha256"], str)
            or SHA256_PATTERN.fullmatch(record["sha256"]) is None
        ):
            raise SbomError("checksum input record is invalid")
        _canonical_path(record["name"], "checksum input")
    expected = [
        {"name": item["path"], "sha256": item["sha256"]}
        for item in sorted(final_artifacts, key=lambda item: item["path"])
    ]
    if sorted(value, key=lambda item: item["name"]) != expected:
        raise SbomError("checksum input does not match the final artifact inventory")


def _component_package(
    component: dict[str, Any],
    spdx_id: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    unresolved_by_id = {
        item["id"]: item for item in evidence["unresolved_components"]
    }
    unresolved_details = [
        unresolved_by_id[record_id]
        for record_id in sorted(set(component["unresolved_fields"].values()))
    ]
    fields = {
        "SPDXID": spdx_id,
        "comment": canonical_json_text(
            {
                "field_provenance": component["field_provenance"],
                "unresolved": unresolved_details,
            }
        ).rstrip("\n"),
        "copyrightText": "NOASSERTION",
        "downloadLocation": component["download_location"],
        "filesAnalyzed": False,
        "licenseConcluded": component["license_concluded"],
        "licenseDeclared": component["license_declared"],
        "name": component["name"],
        "originator": component["origin"],
        "supplier": component["supplier"],
        "versionInfo": component["version"],
    }
    if component["purl"] != "NOASSERTION":
        fields["externalRefs"] = [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceLocator": component["purl"],
                "referenceType": "purl",
            }
        ]
    return fields


def _verify_semantic_relationships(
    document: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    relationships = document.get("relationships", [])
    describes = [
        item for item in relationships
        if item.get("spdxElementId") == "SPDXRef-DOCUMENT"
        and item.get("relationshipType") == "DESCRIBES"
    ]
    release_id = _spdx_id("Package", "release")
    if describes != [
        {
            "relatedSpdxElement": release_id,
            "relationshipType": "DESCRIBES",
            "spdxElementId": "SPDXRef-DOCUMENT",
        }
    ]:
        raise SbomError("incomplete DESCRIBES relationship")
    contained = {
        item["relatedSpdxElement"]
        for item in relationships
        if item.get("relationshipType") == "CONTAINS"
    }
    file_ids = {item["SPDXID"] for item in document.get("files", [])}
    if contained != file_ids:
        raise SbomError("incomplete package/file relationship")
    package_ids = {item["SPDXID"] for item in document.get("packages", [])}
    dependencies = {
        item["relatedSpdxElement"]
        for item in relationships
        if item.get("spdxElementId") == release_id
        and item.get("relationshipType") == "DEPENDS_ON"
    }
    if dependencies != package_ids - {release_id}:
        raise SbomError("incomplete release/component relationship")
    if document["name"] != expected_filename(evidence["release"]["version"]):
        raise SbomError("SBOM filename or identity mismatch")


def _parse_checksum_bytes(data: bytes) -> dict[str, str]:
    _require_utf8_lf(data, "checksum file")
    result: dict[str, str] = {}
    for line in data.decode("utf-8").rstrip("\n").split("\n"):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\r\n]+)", line)
        if match is None or match.group(2) in result:
            raise SbomError("checksum file content is invalid")
        result[match.group(2)] = match.group(1)
    if list(result) != sorted(result):
        raise SbomError("checksum file records are not sorted")
    return result


def _canonical_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SbomError(f"{label} contains an unsafe path")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise SbomError(f"{label} contains an absolute path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SbomError(f"{label} contains parent traversal or a non-canonical path")
    canonical = path.as_posix()
    if canonical != value:
        raise SbomError(f"{label} path is not canonical")
    return canonical


def _canonical_id(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None:
        raise SbomError(f"{label} ID is invalid")
    return value


def _require_object(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SbomError(f"{label} fields are invalid")
    return value


def _spdx_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    safe = re.sub(r"[^A-Za-z0-9.-]+", "-", value).strip("-")[:40] or kind
    return f"SPDXRef-{kind}-{safe}-{digest}"


def _load_strict_json(data: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SbomError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SbomError("JSON input is invalid") from exc


def _require_utf8_lf(data: bytes, name: str) -> None:
    if data.startswith(b"\xef\xbb\xbf"):
        raise SbomError(f"{name} contains a BOM")
    if b"\0" in data or b"\r" in data:
        raise SbomError(f"{name} is not UTF-8 LF text")
    if not data.endswith(b"\n") or data.endswith(b"\n\n"):
        raise SbomError(f"{name} must have exactly one final newline")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SbomError(f"{name} is not UTF-8") from exc


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()
