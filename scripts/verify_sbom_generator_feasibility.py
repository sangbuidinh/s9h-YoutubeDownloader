from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FEASIBILITY_PATH = Path("legal/sbom-generator-feasibility.json")
DOCUMENT_PATH = Path("docs/sbom-generator-feasibility.md")
ASSURANCE_POLICY_PATH = Path("legal/release-assurance-policy.json")
BUILT_INVENTORY_PATH = Path("legal/built-artifact-inventory.json")

ASSESSMENT_BASELINE = "887219aaf37ac5470a6b1f3f4393f1fd85350c4e"
HISTORICAL_INVENTORY_COMMIT = "988c07f9d3e099b3ff157e33d880c0bad73ad112"

SYFT_RELEASE = "v1.48.0"
SYFT_COMMIT = "3e2bc6ed095f7ec1a415fb38cfe1c319e95dfed6"
SYFT_LICENSE_PATH = "LICENSE"
SYFT_LICENSE_BLOB_SHA1 = "261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64"

SBOM_TOOL_RELEASE = "v4.1.5"
SBOM_TOOL_COMMIT = "c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3"
SBOM_TOOL_LICENSE_PATH = "LICENSE"
SBOM_TOOL_LICENSE_BLOB_SHA1 = "9e841e7a26e4eb057b24511e7b92d42b257a80e5"

SPDX_SPEC_RELEASE = "v2.3"
SPDX_SPEC_COMMIT = "aadf3b0b8dbbabdb4d880b0fc714255fea436ff7"
SPDX_SCHEMA_PATH = "schemas/spdx-schema.json"
SPDX_SCHEMA_BLOB_SHA1 = "ee61e6686e885f8139c132647fd0b4f483b8fb81"
SPDX_SCHEMA_URL = "https://github.com/spdx/spdx-spec/blob/aadf3b0b8dbbabdb4d880b0fc714255fea436ff7/schemas/spdx-schema.json"

PROJECT_OWNED_OFFICIAL_SOURCES = (
    "https://github.com/sangbuidinh/s9h-YoutubeDownloader/blob/887219aaf37ac5470a6b1f3f4393f1fd85350c4e/legal/built-artifact-inventory.json",
    "https://github.com/sangbuidinh/s9h-YoutubeDownloader/blob/887219aaf37ac5470a6b1f3f4393f1fd85350c4e/legal/release-assurance-policy.json",
    "https://github.com/sangbuidinh/s9h-YoutubeDownloader/blob/887219aaf37ac5470a6b1f3f4393f1fd85350c4e/scripts/package_windows.py",
    "https://github.com/spdx/spdx-spec/blob/aadf3b0b8dbbabdb4d880b0fc714255fea436ff7/schemas/spdx-schema.json",
)

TOP_LEVEL_KEYS = (
    "assessment_baseline",
    "blockers",
    "candidates",
    "claims",
    "comparison_criteria",
    "current_inputs",
    "decision",
    "document_id",
    "product",
    "prototype_contract",
    "repository",
    "schema_version",
    "scope",
    "target",
    "version",
)

CANDIDATE_IDS = (
    "project-owned-deterministic-spdx-generator",
    "anchore-syft",
    "microsoft-sbom-tool",
)

CRITERIA_IDS = (
    "spdx-2.3-json-capability",
    "deterministic-serialization",
    "stable-package-ordering",
    "stable-relationship-ordering",
    "final-file-sha256-coverage",
    "pyinstaller-one-file-awareness",
    "embedded-python-runtime-awareness",
    "distributed-python-package-awareness",
    "native-dll-pyd-awareness",
    "external-runtime-binary-awareness",
    "portable-package-file-coverage",
    "supplier-origin-evidence",
    "declared-license-evidence",
    "non-fabricated-concluded-license-handling",
    "spdx-noassertion-support",
    "package-url-when-authoritative",
    "file-to-package-relationship-support",
    "release-manifest-reconciliation",
    "checksum-file-reconciliation",
    "offline-generation-capability",
    "offline-validation-capability",
    "version-and-binary-pinning",
    "release-checksum-provenance-availability",
    "windows-ci-compatibility",
    "maintenance-and-update-burden",
    "new-privileged-dependency-risk",
    "reject-incomplete-coverage",
    "expose-unresolved-components",
    "operate-without-publishing",
    "independent-comparator-suitability",
)

ALLOWED_EVIDENCE_STATES = {
    "established",
    "partially-established",
    "not-established",
    "not-applicable",
}

INPUT_CLASSES = (
    "release identity",
    "source and control commits",
    "final artifact inventory",
    "portable-package extracted file inventory",
    "PyInstaller executable inventory",
    "Python package inventory",
    "native member inventory",
    "external runtime inventory",
    "authoritative legal-component evidence",
    "release manifest",
    "checksum file",
)

INPUT_PROPERTIES = (
    "canonical relative path",
    "file type",
    "size",
    "SHA-256",
    "component association",
    "version evidence",
    "supplier/origin evidence",
    "declared-license evidence",
    "concluded license or NOASSERTION",
    "download location or NOASSERTION",
    "package URL or absence with reason",
    "provenance of each field",
    "unresolved-state reason",
)

OUTPUT_PROPERTIES = (
    "deterministic document namespace",
    "deterministic creation-info ordering",
    "deterministic package ordering",
    "deterministic file ordering",
    "deterministic relationship ordering",
    "SHA-256 checksums",
    "explicit DESCRIBES relationships",
    "explicit package/file containment relationships",
    "no fabricated supplier",
    "no fabricated license",
    "no fabricated version",
    "no fabricated download location",
    "NOASSERTION where required",
    "final artifact association",
    "source commit",
    "control commit",
    "release tag",
    "generator identity and pinned version",
    "canonical UTF-8 JSON",
    "LF-only",
    "no BOM",
    "exactly one final newline",
)

FAIL_CLOSED_CONDITIONS = (
    "final package inventory absent",
    "final checksum absent",
    "checksum mismatch",
    "duplicate canonical path",
    "unsafe path",
    "final release manifest mismatch",
    "unresolved distributed file without explicit unresolved record",
    "external runtime omitted",
    "Python runtime omitted",
    "PyInstaller inventory source mismatch",
    "source/control commit malformed",
    "release tag malformed",
    "package/file relationship incomplete",
    "non-deterministic output",
    "schema validation unavailable",
    "semantic reconciliation unavailable",
)

REQUIRED_BLOCKERS = {
    "checksum reconciliation not implemented",
    "comparator binary not approved or executed",
    "deterministic generator not implemented",
    "external runtime reconciliation not implemented",
    "final package inventory contract not implemented",
    "final portable-package inventory not implemented",
    "final Python package inventory not implemented",
    "production SBOM not generated",
    "release integration not authorized",
    "release-manifest reconciliation not implemented",
    "SPDX schema validation not implemented",
    "SPDX semantic validation not implemented",
}

CLAIM_KEYS = {
    "checksum_file_reconciled",
    "complete_sbom",
    "external_comparator_executed",
    "legal_compliance_certified",
    "production_generator_selected",
    "production_sbom_generated",
    "production_sbom_validated",
    "publishing_allowed",
    "release_bundle_integrated",
    "release_manifest_reconciled",
    "release_ready",
    "source_availability_certified",
}

CANDIDATE_KEYS = {
    "candidate_release",
    "capabilities",
    "evidence_retrieved_at_utc",
    "execution_status",
    "id",
    "immutable_commit",
    "license_blob_sha1",
    "license_path",
    "limitations",
    "maintenance_status",
    "official_sources",
    "release_checksums_or_provenance",
    "release_publication_date",
    "repository",
}

CRITERION_KEYS = {"id", "results"}
RESULT_KEYS = {"candidate_id", "decision_impact", "evidence_status", "limitation", "result"}
TARGET_KEYS = {"filename_template", "format", "predicate_type", "spdx_version"}
CURRENT_INPUT_KEYS = {
    "external_runtime_boundary",
    "historical_executable_inventory_is_current_final_release_evidence",
    "historical_executable_inventory_source_commit",
    "production_policy_fail_closed",
    "production_sbom_generation_implemented",
    "production_sbom_validation_implemented",
    "release_bundle_has_production_sbom",
    "release_manifest_reconciles_production_sbom",
    "required_future_input_boundary",
    "source_kit_assembly_blocked",
}
DECISION_KEYS = {
    "architecture_status",
    "external_comparator_execution_authorized",
    "primary_generator",
    "production_generator_selected",
    "production_sbom_generation_authorized",
    "prototype_implementation_authorized",
    "release_integration_authorized",
    "secondary_candidate",
    "selected_comparator",
    "selection_rationale",
}
PROTOTYPE_KEYS = {
    "data_license",
    "deterministic_document_namespace",
    "fail_closed_conditions",
    "filename_template",
    "input_classes",
    "input_properties",
    "output_format",
    "output_properties",
    "schema_validation_implemented",
    "semantic_reconciliation_implemented",
    "spdx_version",
}

EXTERNAL_PINS = {
    "anchore-syft": {
        "candidate_release": SYFT_RELEASE,
        "immutable_commit": SYFT_COMMIT,
        "license_blob_sha1": SYFT_LICENSE_BLOB_SHA1,
        "license_path": SYFT_LICENSE_PATH,
        "repository": "anchore/syft",
    },
    "microsoft-sbom-tool": {
        "candidate_release": SBOM_TOOL_RELEASE,
        "immutable_commit": SBOM_TOOL_COMMIT,
        "license_blob_sha1": SBOM_TOOL_LICENSE_BLOB_SHA1,
        "license_path": SBOM_TOOL_LICENSE_PATH,
        "repository": "microsoft/sbom-tool",
    },
}

PRIVATE_KEY_PEM_HEADER_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9-]+)* PRIVATE KEY-----",
    re.IGNORECASE,
)
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/|"
    r"%userprofile%|\$env:userprofile|~[\\/])"
)
SIGNED_URL_RE = re.compile(r"(?i)https?://[^\s]+[?&](?:sig|signature|token|auth|expires|se)=[^\s&]+")
MUTABLE_REF_RE = re.compile(r"(?i)(?:/blob/(?:main|master)/|/tree/(?:main|master)(?:/|$)|/releases/latest(?:/|$))")
CREDENTIAL_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
)


class FeasibilityError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _fail(category: str, message: str) -> None:
    raise FeasibilityError(category, message)


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate-key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_strict_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail("file-missing", "SBOM generator feasibility JSON is missing")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("file-read", "SBOM generator feasibility JSON cannot be read")
        raise AssertionError from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("bom", "feasibility JSON contains a UTF-8 BOM")
    if b"\r" in raw:
        _fail("line-endings", "feasibility JSON must use LF-only line endings")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail("final-newline", "feasibility JSON must have exactly one final newline")
    for byte in raw:
        if byte < 0x20 and byte not in (0x09, 0x0A):
            _fail("control-character", "feasibility JSON contains an unexpected control character")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("encoding", "feasibility JSON is not valid UTF-8")
        raise AssertionError from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate)
    except FeasibilityError:
        raise
    except json.JSONDecodeError as exc:
        _fail("json-syntax", f"feasibility document is not strict JSON at line {exc.lineno}")
        raise AssertionError from exc
    if not isinstance(value, dict):
        _fail("top-level-schema", "feasibility JSON root must be an object")
    canonical = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if text != canonical:
        _fail("canonical-json", "feasibility JSON is not canonical two-space sorted JSON")
    return value


def _load_cross_check_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("cross-check", f"{label} cannot be read as strict JSON")
        raise AssertionError from exc
    if not isinstance(value, dict):
        _fail("cross-check", f"{label} must be an object")
    return value


def _require_object(value: Any, expected: set[str], category: str, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(category, f"{label} fields are invalid")
    return value


def _require_string(value: Any, category: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(category, message)
    return value


def _require_false(value: Any, category: str, message: str) -> None:
    if type(value) is not bool or value is not False:
        _fail(category, message)


def _require_string_list(value: Any, category: str, message: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail(category, message)
    return value


def _scan_forbidden(value: Any, path: str = "feasibility") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            folded = key.casefold()
            if folded in {"password", "passphrase", "token", "authorization", "private_key", "client_secret"}:
                _fail("secret-material", f"secret-like field is forbidden at {path}.{key}")
            _scan_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden(child, f"{path}[{index}]")
    elif isinstance(value, str):
        folded = value.casefold()
        if PRIVATE_KEY_PEM_HEADER_RE.search(value):
            _fail("private-key-material", "private-key PEM material is forbidden")
        if re.search(r"(?i)authorization\s*:\s*(?:basic|bearer)\s+\S+", value):
            _fail("authorization-header", "authorization header material is forbidden")
        if any(pattern.search(value) for pattern in CREDENTIAL_RES):
            _fail("credential-material", "credential material is forbidden")
        if "-----begin certificate-----" in folded or re.search(r"(?i)(?:^|[\\/])[^\\/]+\.(?:pfx|p12|pem|key)$", value):
            _fail("certificate-material", "certificate material or path is forbidden")
        if LOCAL_PATH_RE.search(value):
            _fail("user-path", "local user-profile paths are forbidden")
        if SIGNED_URL_RE.search(value):
            _fail("signed-url", "signed URLs are forbidden")
        if MUTABLE_REF_RE.search(value):
            _fail("mutable-ref", "mutable evidence references are forbidden")
        if "production ready" in folded or "production-ready" in folded:
            _fail("unsupported-claim", "unsupported production-ready claim")


def _validate_project_owned_sources(sources: list[str]) -> None:
    if len(sources) != len(set(sources)):
        _fail("official-sources", "project-owned official sources changed")

    spdx_sources = [source for source in sources if source.startswith("https://github.com/spdx/")]
    if len(spdx_sources) != 1:
        _fail("official-sources", "project-owned official sources changed")

    match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)",
        spdx_sources[0],
    )
    if match is None or f"{match.group(1)}/{match.group(2)}" != "spdx/spdx-spec":
        _fail("official-sources", "project-owned official sources changed")

    commit = match.group(3)
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        _fail("spdx-evidence-commit", "SPDX specification commit is malformed")
    if commit != SPDX_SPEC_COMMIT:
        _fail("spdx-evidence-pin", "SPDX specification commit changed")
    if match.group(4) != SPDX_SCHEMA_PATH:
        _fail("spdx-evidence-path", "SPDX schema path changed")
    if tuple(sources) != PROJECT_OWNED_OFFICIAL_SOURCES:
        _fail("official-sources", "project-owned official sources changed")


def _validate_candidates(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        _fail("candidate-set", "candidate set is invalid")
    candidates = value
    ids = [candidate.get("id") for candidate in candidates]
    if len(ids) != len(set(ids)):
        _fail("candidate-unique", "candidate IDs must be unique")
    if set(ids) != set(CANDIDATE_IDS):
        _fail("candidate-set", "candidate set is invalid")
    if tuple(ids) != CANDIDATE_IDS:
        _fail("candidate-order", "candidate order is invalid")

    capabilities: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        candidate_id = candidate["id"]
        _require_object(candidate, CANDIDATE_KEYS, "candidate-schema", f"candidate {candidate_id}")
        capability_map = candidate["capabilities"]
        if not isinstance(capability_map, dict) or tuple(capability_map) != tuple(sorted(CRITERIA_IDS)):
            _fail("candidate-capabilities", f"candidate capabilities are invalid: {candidate_id}")
        for state in capability_map.values():
            if state not in ALLOWED_EVIDENCE_STATES:
                _fail("evidence-state", "unsupported capability evidence state")
        capabilities[candidate_id] = capability_map

        limitations = _require_string_list(
            candidate["limitations"], "candidate-schema", f"candidate limitations are invalid: {candidate_id}"
        )
        if not limitations or limitations != sorted(limitations):
            _fail("candidate-schema", f"candidate limitations are invalid: {candidate_id}")
        sources = _require_string_list(
            candidate["official_sources"], "candidate-schema", f"candidate sources are invalid: {candidate_id}"
        )
        if not sources or sources != sorted(sources):
            _fail("candidate-schema", f"candidate sources are invalid: {candidate_id}")
        if candidate_id == CANDIDATE_IDS[0]:
            _validate_project_owned_sources(sources)
        _require_string(candidate["maintenance_status"], "candidate-schema", "candidate maintenance status is invalid")
        _require_string(
            candidate["release_checksums_or_provenance"],
            "candidate-schema",
            "candidate checksum or provenance disclosure is invalid",
        )
        retrieved = _require_string(
            candidate["evidence_retrieved_at_utc"], "candidate-schema", "candidate retrieval time is invalid"
        )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", retrieved) is None:
            _fail("candidate-schema", "candidate retrieval time is invalid")

        if candidate_id in EXTERNAL_PINS:
            pin = EXTERNAL_PINS[candidate_id]
            if candidate["repository"] != pin["repository"]:
                _fail("candidate-repository", f"candidate repository changed: {candidate_id}")
            commit = candidate["immutable_commit"]
            if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                _fail("candidate-commit", f"candidate immutable commit is malformed: {candidate_id}")
            if commit != pin["immutable_commit"]:
                _fail("candidate-pin", f"candidate immutable commit changed: {candidate_id}")
            if candidate["candidate_release"] != pin["candidate_release"]:
                _fail("candidate-release", f"candidate release changed: {candidate_id}")
            if candidate["license_path"] != pin["license_path"]:
                _fail("candidate-license", f"candidate license path changed: {candidate_id}")
            if candidate["license_blob_sha1"] != pin["license_blob_sha1"]:
                _fail("candidate-license", f"candidate license blob SHA changed: {candidate_id}")
            if candidate["execution_status"] != "not-downloaded-not-executed":
                _fail("execution-status", f"external candidate execution status changed: {candidate_id}")
        else:
            expected = {
                "candidate_release": "not-applicable-prototype-contract-only",
                "execution_status": "contract-only-not-implemented",
                "immutable_commit": ASSESSMENT_BASELINE,
                "license_blob_sha1": "not-applicable",
                "license_path": "not-applicable",
                "repository": "sangbuidinh/s9h-YoutubeDownloader",
                "release_publication_date": "not-applicable",
            }
            for key, expected_value in expected.items():
                if candidate[key] != expected_value:
                    _fail("candidate-schema", f"project-owned candidate field changed: {key}")
    return capabilities


def _validate_criteria(value: Any, capabilities: dict[str, dict[str, str]]) -> None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        _fail("criteria-set", "comparison criteria are invalid")
    ids = [item.get("id") for item in value]
    if set(ids) != set(CRITERIA_IDS) or len(ids) != len(CRITERIA_IDS):
        _fail("criteria-set", "comparison criteria set is invalid")
    if tuple(ids) != CRITERIA_IDS:
        _fail("criteria-order", "comparison criterion order is invalid")
    for criterion in value:
        criterion_id = criterion["id"]
        _require_object(criterion, CRITERION_KEYS, "criterion-schema", f"criterion {criterion_id}")
        results = criterion["results"]
        if not isinstance(results, list) or len(results) != len(CANDIDATE_IDS):
            _fail("criterion-schema", f"criterion results are invalid: {criterion_id}")
        if tuple(result.get("candidate_id") for result in results) != CANDIDATE_IDS:
            _fail("criterion-schema", f"criterion candidate order is invalid: {criterion_id}")
        for result in results:
            _require_object(result, RESULT_KEYS, "criterion-schema", f"criterion result {criterion_id}")
            state = result["evidence_status"]
            if state not in ALLOWED_EVIDENCE_STATES:
                _fail("evidence-state", "unsupported comparison evidence state")
            if capabilities[result["candidate_id"]][criterion_id] != state:
                _fail("evidence-state", "candidate and comparison evidence states differ")
            for field in ("decision_impact", "limitation", "result"):
                _require_string(result[field], "criterion-schema", f"criterion {field} is invalid: {criterion_id}")


def _validate_current_inputs(value: Any) -> None:
    current = _require_object(value, CURRENT_INPUT_KEYS, "current-inputs", "current inputs")
    if current["historical_executable_inventory_source_commit"] != HISTORICAL_INVENTORY_COMMIT:
        _fail("historical-inventory", "historical executable inventory source commit changed")
    if current["historical_executable_inventory_is_current_final_release_evidence"] is not False:
        _fail("historical-inventory", "historical executable inventory must not be claimed as final")
    expected_false = (
        "production_sbom_generation_implemented",
        "production_sbom_validation_implemented",
        "release_bundle_has_production_sbom",
        "release_manifest_reconciles_production_sbom",
    )
    for key in expected_false:
        _require_false(current[key], "current-inputs", f"current input state must remain false: {key}")
    if current["production_policy_fail_closed"] is not True or current["source_kit_assembly_blocked"] is not True:
        _fail("current-inputs", "current fail-closed state changed")
    runtimes = _require_string_list(current["external_runtime_boundary"], "current-inputs", "external runtime boundary is invalid")
    if runtimes != ["aria2", "Deno", "FFmpeg", "ffprobe", "yt-dlp"]:
        _fail("current-inputs", "external runtime boundary changed")
    boundary = _require_string_list(
        current["required_future_input_boundary"], "current-inputs", "future input boundary is invalid"
    )
    if not set(INPUT_CLASSES).issubset(boundary):
        _fail("current-inputs", "future input boundary is incomplete")


def _validate_decision(value: Any) -> None:
    decision = _require_object(value, DECISION_KEYS, "decision", "decision")
    if decision["architecture_status"] != "prototype-architecture-recommended":
        _fail("decision", "architecture status changed")
    if decision["primary_generator"] != CANDIDATE_IDS[0]:
        _fail("decision", "primary generator changed")
    if decision["selected_comparator"] != "anchore-syft":
        _fail("decision", "selected comparator changed")
    if decision["secondary_candidate"] != "microsoft-sbom-tool":
        _fail("decision", "secondary candidate changed")
    _require_string(decision["selection_rationale"], "decision", "selection rationale is missing")
    for key in (
        "external_comparator_execution_authorized",
        "production_generator_selected",
        "production_sbom_generation_authorized",
        "prototype_implementation_authorized",
        "release_integration_authorized",
    ):
        _require_false(decision[key], "authorization", f"decision authorization must remain false: {key}")


def _validate_prototype(value: Any) -> None:
    prototype = _require_object(value, PROTOTYPE_KEYS, "prototype-contract", "prototype contract")
    fixed = {
        "data_license": "CC0-1.0",
        "deterministic_document_namespace": True,
        "filename_template": "Youtube-Downloaderbs-v{version}.spdx.json",
        "output_format": "SPDX-2.3-json",
        "schema_validation_implemented": False,
        "semantic_reconciliation_implemented": False,
        "spdx_version": "SPDX-2.3",
    }
    for key, expected in fixed.items():
        if prototype[key] != expected:
            _fail("prototype-contract", f"prototype contract field changed: {key}")
    if tuple(prototype["input_classes"]) != INPUT_CLASSES:
        _fail("prototype-inputs", "prototype input classes changed")
    if tuple(prototype["input_properties"]) != INPUT_PROPERTIES:
        _fail("prototype-inputs", "prototype input properties changed")
    if tuple(prototype["output_properties"]) != OUTPUT_PROPERTIES:
        _fail("prototype-output", "prototype output properties changed")
    if tuple(prototype["fail_closed_conditions"]) != FAIL_CLOSED_CONDITIONS:
        _fail("fail-closed", "prototype fail-closed conditions changed")


def _validate_cross_checks(root: Path) -> None:
    assurance = _load_cross_check_json(root / ASSURANCE_POLICY_PATH, "release assurance policy")
    sbom = assurance.get("sbom")
    claims = assurance.get("claims")
    if not isinstance(sbom, dict) or not isinstance(claims, dict):
        _fail("assurance-policy", "release assurance SBOM state is invalid")
    expected_false = {
        "generator_selected": sbom.get("generator_selected"),
        "implementation_status": sbom.get("implementation_status"),
        "readiness": sbom.get("readiness"),
        "sbom_generated": claims.get("sbom_generated"),
        "sbom_validated": claims.get("sbom_validated"),
        "sbom_attested": claims.get("sbom_attested"),
    }
    if any(value is not False for value in expected_false.values()):
        _fail("assurance-policy", "existing assurance policy SBOM state must remain false")

    inventory = _load_cross_check_json(root / BUILT_INVENTORY_PATH, "built artifact inventory")
    if inventory.get("source_commit") != HISTORICAL_INVENTORY_COMMIT:
        _fail("historical-inventory", "historical built inventory source commit changed")

    try:
        document_raw = (root / DOCUMENT_PATH).read_bytes()
    except OSError as exc:
        _fail("document", "feasibility document is missing or unreadable")
        raise AssertionError from exc
    if document_raw.startswith(b"\xef\xbb\xbf") or b"\r" in document_raw:
        _fail("document", "feasibility document encoding or line endings are invalid")
    if not document_raw.endswith(b"\n") or document_raw.endswith(b"\n\n"):
        _fail("document", "feasibility document must have exactly one final newline")
    try:
        document = document_raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("document", "feasibility document is not UTF-8")
        raise AssertionError from exc
    if f"spdx/spdx-spec` tag `{SPDX_SPEC_RELEASE}`" not in document:
        _fail("spdx-release-pin", "SPDX specification release changed")
    if f"commit `{SPDX_SPEC_COMMIT}`" not in document:
        _fail("spdx-evidence-pin", "SPDX specification commit changed")
    if f"schema blob `{SPDX_SCHEMA_BLOB_SHA1}`" not in document:
        _fail("spdx-schema-pin", "SPDX schema blob changed")
    if SPDX_SCHEMA_URL not in document:
        _fail("spdx-evidence-path", "SPDX schema path changed")
    required_markers = (
        "# SBOM Generator Feasibility",
        "## Current SBOM Input Boundary",
        "## Official Source Method",
        "## Candidate Comparison",
        "## Architecture Decision",
        "## Prototype Contract",
        "## Explicit Non-Claims",
        "The historical executable inventory is not current final-release evidence.",
        SYFT_RELEASE,
        SYFT_COMMIT,
        SBOM_TOOL_RELEASE,
        SBOM_TOOL_COMMIT,
    )
    if any(marker not in document for marker in required_markers):
        _fail("document", "feasibility document is missing required content")
    if "historical executable inventory is current final-release evidence" in document.casefold():
        _fail("historical-inventory", "feasibility document misrepresents historical inventory")
    _scan_forbidden(document, "document")


def verify_feasibility_file(root: Path) -> None:
    value = _load_strict_json(root / FEASIBILITY_PATH)
    _scan_forbidden(value)
    if tuple(value) != TOP_LEVEL_KEYS:
        _fail("top-level-schema", "top-level feasibility fields are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("fixed-value", "schema_version must be integer 1")
    fixed = {
        "assessment_baseline": ASSESSMENT_BASELINE,
        "document_id": "s9h-sbom-generator-feasibility-v1",
        "product": "Youtube Downloaderbs",
        "repository": "sangbuidinh/s9h-YoutubeDownloader",
        "scope": "prototype-feasibility-only",
        "version": "1.3.1",
    }
    for key, expected in fixed.items():
        if value[key] != expected:
            category = "baseline" if key == "assessment_baseline" else "fixed-value"
            _fail(category, f"fixed feasibility identity changed: {key}")
    if re.fullmatch(r"[0-9a-f]{40}", value["assessment_baseline"]) is None:
        _fail("baseline", "assessment baseline is malformed")

    target = _require_object(value["target"], TARGET_KEYS, "target", "target")
    expected_target = {
        "filename_template": "Youtube-Downloaderbs-v{version}.spdx.json",
        "format": "SPDX-2.3-json",
        "predicate_type": "https://spdx.dev/Document/v2.3",
        "spdx_version": "SPDX-2.3",
    }
    for key, expected in expected_target.items():
        if target[key] != expected:
            _fail("target", f"SBOM target changed: {key}")

    blockers = _require_string_list(value["blockers"], "blockers", "blockers must be a string list")
    if not blockers:
        _fail("blockers", "blockers must not be empty")
    if blockers != sorted(blockers) or not REQUIRED_BLOCKERS.issubset(blockers):
        _fail("blockers", "required blockers are missing or unordered")

    claims = _require_object(value["claims"], CLAIM_KEYS, "claims", "claims")
    for key in CLAIM_KEYS:
        _require_false(claims[key], "claims", f"claim must remain false: {key}")

    capabilities = _validate_candidates(value["candidates"])
    _validate_criteria(value["comparison_criteria"], capabilities)
    _validate_current_inputs(value["current_inputs"])
    _validate_decision(value["decision"])
    _validate_prototype(value["prototype_contract"])
    _validate_cross_checks(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the SBOM generator feasibility baseline")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    args = parser.parse_args(argv)
    try:
        verify_feasibility_file(args.root)
    except (FeasibilityError, OSError) as exc:
        if isinstance(exc, FeasibilityError):
            print(f"SBOM generator feasibility verifier failed [{exc.category}]: {exc}", file=sys.stderr)
        else:
            print("SBOM generator feasibility verifier failed [filesystem]: validation failed", file=sys.stderr)
        return 1
    print("SBOM generator feasibility verifier: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
