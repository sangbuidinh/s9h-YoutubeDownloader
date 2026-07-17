from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


POLICY_PATH = Path("legal/release-assurance-policy.json")
TOP_LEVEL_KEYS = [
    "assessment_baseline",
    "authenticode",
    "claims",
    "policy_id",
    "product",
    "provenance",
    "release_integration",
    "sbom",
    "schema_version",
    "version",
]
AUTHENTICODE_KEYS = {
    "blockers",
    "candidate_timestamp",
    "certificate_provisioned",
    "credential_source_selected",
    "failure_policy",
    "file_digest",
    "first_party_signing_targets",
    "implementation_status",
    "readiness",
    "sequencing",
    "signing_key_custody_approved",
    "third_party_resigning",
    "verification_policy",
    "verification_switch",
}
CLAIM_KEYS = {
    "authenticode_signed",
    "provenance_attested",
    "release_assurance_ready",
    "sbom_attested",
    "sbom_generated",
    "sbom_validated",
    "signature_timestamped",
    "signature_verified",
}
PROVENANCE_KEYS = {
    "action_repository",
    "attestation_generated",
    "blockers",
    "candidate_action_commit",
    "candidate_major_release",
    "candidate_release",
    "final_bytes_only",
    "implementation_status",
    "provider",
    "public_verification_repository",
    "readiness",
    "sigstore_claim_made",
    "source_kit_subjects_mandatory",
    "subjects",
    "synthetic_ci_attestations_are_release_attestations",
    "verification",
    "workflow_permission_review",
}
RELEASE_INTEGRATION_KEYS = {
    "all_byte_changes_before_finalization",
    "blockers",
    "existing_gate_invariants",
    "readiness",
    "sequence",
}
SBOM_KEYS = {
    "blockers",
    "coverage_categories",
    "filename_template",
    "format",
    "future_properties",
    "generator_selected",
    "implementation_status",
    "non_claims",
    "predicate_type",
    "readiness",
}

SIGNING_TARGETS = ["Youtube.Downloaderbs.exe"]
RESIGN_EXCLUSIONS = [
    "yt-dlp.exe",
    "ffmpeg.exe",
    "ffprobe.exe",
    "deno.exe",
    "aria2c.exe",
    "any other vendor-supplied binary",
]
SBOM_SCOPE = [
    "first-party application",
    "Python interpreter/runtime components included by packaging",
    "Python packages actually distributed",
    "yt-dlp",
    "FFmpeg and ffprobe",
    "Deno",
    "aria2",
    "any other executable, DLL, package or distributable dependency present in the final release package",
]
PROVENANCE_SUBJECTS = [
    "Youtube.Downloaderbs.exe",
    "Youtube-Downloaderbs-v{version}.zip",
    "Youtube-Downloaderbs-v{version}-legal.zip",
    "Youtube-Downloaderbs-v{version}.spdx.json",
    "SHA256SUMS.txt",
    "RELEASE_MANIFEST.json",
]
FINAL_BYTE_SEQUENCE = [
    "build first-party executable",
    "validate unsigned build structure",
    "Authenticode-sign first-party executable",
    "verify Authenticode signature and timestamp",
    "assemble the portable package using the signed executable",
    "calculate final artifact checksums",
    "generate and validate the final SBOM",
    "synchronize checksums, release notes and release manifest",
    "perform final release-bundle validation",
    "generate provenance and SBOM attestations over final immutable subjects",
    "verify attestations",
    "hand off immutable release bundle",
    "allow publishing only when all independent release gates pass",
]
GATE_INVARIANTS = {
    "assembly_authorized": False,
    "legal_compliance_certified": False,
    "publishing_allowed": False,
    "release_gate_reconsideration_allowed": False,
    "release_ready": False,
    "source_assets_created": False,
    "source_availability_certified": False,
    "source_kits_ready": False,
}
READINESS_FIELDS = {
    "authenticode_ready": False,
    "provenance_ready": False,
    "release_assurance_ready": False,
    "sbom_ready": False,
}
SECRET_KEY_PARTS = (
    "password",
    "passphrase",
    "private_key",
    "pfx_base64",
    "certificate_base64",
    "client_secret",
    "authorization",
)
SECRET_KEY_EXACT = {"token"}
PRIVATE_KEY_PEM_HEADER_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9-]+)* PRIVATE KEY-----",
    re.IGNORECASE,
)
ATTEST_ACTION_COMMIT = "f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"


class PolicyError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _fail(category: str, message: str) -> None:
    raise PolicyError(category, message)


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate-key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_policy(raw: bytes) -> tuple[str, dict[str, Any]]:
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("bom", "policy contains a UTF-8 BOM")
    if b"\r" in raw:
        _fail("line-endings", "policy must use LF-only line endings")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail("final-newline", "policy must have exactly one final newline")
    for byte in raw:
        if byte < 0x20 and byte != 0x0A:
            _fail("control-character", "policy contains an unexpected control character")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("encoding", "policy is not valid UTF-8")
        raise AssertionError from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate)
    except PolicyError:
        raise
    except json.JSONDecodeError as exc:
        _fail("json-syntax", f"policy is not strict JSON at line {exc.lineno}")
        raise AssertionError from exc
    if not isinstance(value, dict):
        _fail("top-level-schema", "policy root must be an object")
    canonical = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if text != canonical:
        _fail("canonical-json", "policy is not canonical two-space sorted JSON")
    return text, value


def _require_object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("type", f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        _fail("nested-schema", f"{label} fields are invalid")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        _fail("type", f"{label} must be a boolean")
    return value


def _require_false(value: Any, category: str, label: str) -> None:
    if _require_bool(value, label) is not False:
        _fail(category, f"{label} must remain false")


def _require_true(value: Any, category: str, label: str) -> None:
    if _require_bool(value, label) is not True:
        _fail(category, f"{label} must remain true")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail("type", f"{label} must be a string")
    return value


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail("type", f"{label} must be a string list")
    return value


def _require_blockers(value: Any, category: str, label: str, required: set[str]) -> None:
    blockers = _require_string_list(value, label)
    if not blockers:
        _fail(category, f"{label} must not be empty")
    if blockers != sorted(blockers):
        _fail("list-order", f"{label} must use canonical ordering")
    if not required.issubset(blockers):
        _fail(category, f"{label} is missing required blockers")


def _scan_for_forbidden_material(value: Any, path: str = "policy") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            folded = key.casefold()
            if folded in {"production_ready", "production-ready"}:
                _fail("unsupported-claim", "unsupported production-ready claim")
            if folded in SECRET_KEY_EXACT or any(part in folded for part in SECRET_KEY_PARTS):
                _fail("secret-key", f"secret-like field is forbidden at {path}")
            if "pfx" in folded or "p12" in folded or "pem" in folded:
                _fail("certificate-material", f"certificate field is forbidden at {path}")
            _scan_for_forbidden_material(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_forbidden_material(child, f"{path}[{index}]")
    elif isinstance(value, str):
        folded = value.casefold()
        if PRIVATE_KEY_PEM_HEADER_RE.search(value) is not None:
            _fail("private-key-material", "private-key material is forbidden")
        if "-----begin certificate-----" in folded or re.search(r"(?i)(?:^|[\\/])[^\\/]+\.(?:pfx|p12|pem|key)$", value):
            _fail("certificate-material", "certificate material or path is forbidden")
        if re.search(r"(?i)(?:[a-z]:[\\/]users[\\/]|/home/|%userprofile%|\$env:userprofile|~[\\/])", value):
            _fail("user-path", "local user-profile paths are forbidden")
        if "production ready" in folded or "production-ready" in folded:
            _fail("unsupported-claim", "unsupported production-ready claim")


def _validate_authenticode(value: Any) -> None:
    auth = _require_object(value, AUTHENTICODE_KEYS, "authenticode")
    _require_false(auth["implementation_status"], "auth-readiness", "authenticode implementation status")
    _require_false(auth["readiness"], "auth-readiness", "authenticode readiness")
    _require_false(auth["certificate_provisioned"], "auth-certificate", "certificate provisioned")
    _require_false(auth["credential_source_selected"], "auth-certificate", "credential source selected")
    _require_false(auth["signing_key_custody_approved"], "auth-certificate", "signing key custody approved")
    if _require_string_list(auth["first_party_signing_targets"], "first-party signing targets") != SIGNING_TARGETS:
        _fail("auth-target", "first-party signing target changed")
    if _require_string(auth["file_digest"], "Authenticode file digest") != "SHA256":
        _fail("auth-digest", "Authenticode file digest must be SHA256")
    if auth["verification_policy"] != "Default Authenticode" or auth["verification_switch"] != "/pa":
        _fail("auth-verification", "Default Authenticode /pa verification is required")

    timestamp = _require_object(
        auth["candidate_timestamp"],
        {"digest", "protocol", "timestamp_authority_selected"},
        "candidate timestamp",
    )
    if _require_string(timestamp["protocol"], "timestamp protocol") != "RFC3161":
        _fail("timestamp-protocol", "timestamp protocol must be RFC3161")
    if _require_string(timestamp["digest"], "timestamp digest") != "SHA256":
        _fail("timestamp-digest", "timestamp digest must be SHA256")
    _require_false(timestamp["timestamp_authority_selected"], "auth-certificate", "timestamp authority selected")

    boundary = _require_object(auth["third_party_resigning"], {"allowed", "excluded"}, "third-party re-signing")
    _require_false(boundary["allowed"], "third-party-boundary", "third-party re-signing allowed")
    if _require_string_list(boundary["excluded"], "third-party exclusions") != RESIGN_EXCLUSIONS:
        _fail("third-party-boundary", "third-party re-signing exclusions changed")

    sequencing = _require_object(
        auth["sequencing"],
        {
            "after_final_application_build",
            "before_final_checksums",
            "before_portable_zip_creation",
            "verify_after_signing",
            "verify_timestamp",
        },
        "Authenticode sequencing",
    )
    for key in sequencing:
        _require_true(sequencing[key], "auth-sequence", f"Authenticode sequencing {key}")

    failure = _require_object(
        auth["failure_policy"],
        {
            "signing_fail_closed",
            "timestamp_fail_closed",
            "unsigned_fallback_may_be_published_as_signed",
            "verification_fail_closed",
        },
        "Authenticode failure policy",
    )
    for key in ("signing_fail_closed", "timestamp_fail_closed", "verification_fail_closed"):
        _require_true(failure[key], "auth-failure-policy", f"Authenticode failure policy {key}")
    _require_false(
        failure["unsigned_fallback_may_be_published_as_signed"],
        "auth-failure-policy",
        "unsigned fallback publication claim",
    )
    _require_blockers(
        auth["blockers"],
        "auth-blockers",
        "Authenticode blockers",
        {
            "code-signing certificate provider not selected",
            "private-key custody model not approved",
            "CI signing identity not provisioned",
            "RFC3161 timestamp authority not approved",
            "signing and verification workflow not implemented",
            "signed-artifact checksum sequencing not implemented",
        },
    )


def _validate_sbom(value: Any) -> None:
    sbom = _require_object(value, SBOM_KEYS, "sbom")
    _require_false(sbom["implementation_status"], "sbom-readiness", "SBOM implementation status")
    _require_false(sbom["readiness"], "sbom-readiness", "SBOM readiness")
    _require_false(sbom["generator_selected"], "sbom-generator", "SBOM generator selected")
    if _require_string(sbom["format"], "SBOM format") != "SPDX-2.3-json":
        _fail("sbom-format", "SBOM format must be SPDX-2.3-json")
    if _require_string(sbom["predicate_type"], "SBOM predicate type") != "https://spdx.dev/Document/v2.3":
        _fail("sbom-predicate", "SBOM predicate type changed")
    if sbom["filename_template"] != "Youtube-Downloaderbs-v{version}.spdx.json":
        _fail("sbom-format", "SBOM filename template changed")
    if _require_string_list(sbom["coverage_categories"], "SBOM coverage categories") != SBOM_SCOPE:
        _fail("sbom-scope", "SBOM coverage categories changed")

    properties = _require_object(
        sbom["future_properties"],
        {
            "creation_tool_identity_and_pinned_version",
            "declared_license_from_authoritative_evidence",
            "deterministic_json_serialization",
            "document_namespace_unique_to_release",
            "download_location_or_noassertion",
            "final_artifact_association",
            "license_conclusion_not_fabricated",
            "package_supplier_or_origin_when_known",
            "package_url_when_authoritative",
            "package_version",
            "release_tag",
            "semantic_release_manifest_and_checksum_validation",
            "sha256_package_and_file_checksums",
            "source_commit",
            "stable_package_ordering",
            "stable_relationship_ordering",
            "strict_schema_validation",
            "workflow_control_commit",
        },
        "SBOM future properties",
    )
    for key in properties:
        _require_true(properties[key], "sbom-properties", f"SBOM property {key}")
    non_claims = _require_object(
        sbom["non_claims"],
        {
            "complete_sbom",
            "license_compliance_certified",
            "reproducible_build",
            "source_correspondence_complete",
            "vulnerability_free_build",
        },
        "SBOM non-claims",
    )
    for key in non_claims:
        _require_false(non_claims[key], "unsupported-claim", f"SBOM non-claim {key}")
    _require_blockers(
        sbom["blockers"],
        "sbom-blockers",
        "SBOM blockers",
        {
            "generator not selected",
            "generator version not pinned",
            "PyInstaller dependency extraction strategy not implemented",
            "distributed Python package inventory not reconciled",
            "bundled runtime inventory not reconciled",
            "SPDX schema validation not implemented",
            "release-manifest cross-check not implemented",
            "production SBOM not generated",
        },
    )


def _validate_provenance(value: Any) -> None:
    provenance = _require_object(value, PROVENANCE_KEYS, "provenance")
    _require_false(provenance["implementation_status"], "provenance-readiness", "provenance implementation status")
    _require_false(provenance["readiness"], "provenance-readiness", "provenance readiness")
    _require_false(provenance["attestation_generated"], "provenance-readiness", "attestation generated")
    _require_false(provenance["sigstore_claim_made"], "unsupported-claim", "Sigstore claim made")
    if provenance["provider"] != "github-artifact-attestations" or provenance["action_repository"] != "actions/attest":
        _fail("provenance-action", "provenance provider or action changed")
    commit = _require_string(provenance["candidate_action_commit"], "candidate action commit")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        _fail("action-pin", "candidate action commit must be a full lowercase SHA")
    if commit != ATTEST_ACTION_COMMIT:
        _fail("action-pin", "candidate action commit changed")
    if provenance["candidate_major_release"] != "v4" or provenance["candidate_release"] != "v4.2.0":
        _fail("provenance-action", "candidate action release changed")
    _require_true(provenance["final_bytes_only"], "final-byte-order", "final-bytes-only provenance")
    if provenance["public_verification_repository"] != "sangbuidinh/s9h-YoutubeDownloader":
        _fail("provenance-verification", "public verification repository changed")
    _require_false(
        provenance["source_kit_subjects_mandatory"],
        "source-kit-subject",
        "source-kit subjects mandatory",
    )
    _require_false(
        provenance["synthetic_ci_attestations_are_release_attestations"],
        "provenance-subjects",
        "synthetic CI release-attestation claim",
    )
    if _require_string_list(provenance["subjects"], "provenance subjects") != PROVENANCE_SUBJECTS:
        _fail("provenance-subjects", "provenance subjects changed")

    verification = _require_object(
        provenance["verification"],
        {"documented_and_tested", "final_subject_names_required", "sha256_subject_digests_required"},
        "provenance verification",
    )
    _require_false(verification["documented_and_tested"], "provenance-verification", "attestation verification tested")
    _require_true(verification["final_subject_names_required"], "provenance-verification", "final subject names required")
    _require_true(verification["sha256_subject_digests_required"], "provenance-verification", "SHA256 subject digests required")

    permissions = _require_object(
        provenance["workflow_permission_review"],
        {"integrated", "packages_write_required", "required_job_permissions", "status"},
        "workflow permission review",
    )
    _require_false(permissions["integrated"], "workflow-permissions", "workflow permissions integrated")
    _require_false(permissions["packages_write_required"], "workflow-permissions", "packages write required")
    if permissions["status"] != "planned":
        _fail("workflow-permissions", "workflow permission review status changed")
    if _require_string_list(permissions["required_job_permissions"], "required job permissions") != [
        "artifact-metadata: write",
        "attestations: write",
        "contents: read",
        "id-token: write",
    ]:
        _fail("workflow-permissions", "future least-privilege permission plan changed")
    _require_blockers(
        provenance["blockers"],
        "provenance-blockers",
        "provenance blockers",
        {
            "artifact metadata permission not integrated",
            "immutable action pin not integrated",
            "job-level OIDC permission not integrated",
            "attestation permission not integrated",
            "final subject strategy not implemented",
            "final-byte sequencing not implemented",
            "verification command and policy not tested",
            "no production attestation exists",
        },
    )


def _validate_release_integration(value: Any) -> None:
    integration = _require_object(value, RELEASE_INTEGRATION_KEYS, "release integration")
    _require_true(
        integration["all_byte_changes_before_finalization"],
        "final-byte-order",
        "all byte changes before finalization",
    )
    if _require_string_list(integration["sequence"], "release integration sequence") != FINAL_BYTE_SEQUENCE:
        _fail("final-byte-order", "release integration sequence changed")
    readiness = _require_object(integration["readiness"], set(READINESS_FIELDS), "release readiness")
    for key in READINESS_FIELDS:
        _require_false(readiness[key], "integration-readiness", f"release readiness {key}")
    invariants = _require_object(
        integration["existing_gate_invariants"],
        set(GATE_INVARIANTS),
        "existing release/source-kit invariants",
    )
    for key in GATE_INVARIANTS:
        _require_false(invariants[key], "gate-invariants", f"existing gate invariant {key}")
    _require_blockers(
        integration["blockers"],
        "integration-blockers",
        "release integration blockers",
        {
            "Authenticode signing and verification are not integrated",
            "production SBOM generation and validation are not integrated",
            "final checksum and manifest synchronization is not integrated with assurance artifacts",
            "provenance and SBOM attestation verification are not integrated",
        },
    )


def verify_policy_file(root: Path) -> None:
    path = root / POLICY_PATH
    if not path.is_file():
        _fail("file-missing", "release assurance policy is missing")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("file-read", "release assurance policy cannot be read")
        raise AssertionError from exc
    _, policy = _load_policy(raw)
    _scan_for_forbidden_material(policy)

    if list(policy) != TOP_LEVEL_KEYS:
        _fail("top-level-schema", "top-level policy fields or order are invalid")
    if policy["schema_version"] != 1 or type(policy["schema_version"]) is not int:
        _fail("fixed-value", "schema_version must be integer 1")
    if policy["policy_id"] != "s9h-release-assurance-v1":
        _fail("fixed-value", "policy_id changed")
    if policy["product"] != "Youtube Downloaderbs":
        _fail("fixed-value", "product changed")
    if policy["version"] != "1.3.1":
        _fail("fixed-value", "version changed")
    baseline = _require_string(policy["assessment_baseline"], "assessment baseline")
    if baseline != "bf196a0895990802624f7f1926458100170b2443" or re.fullmatch(r"[0-9a-f]{40}", baseline) is None:
        _fail("baseline", "assessment baseline changed or is malformed")

    _validate_authenticode(policy["authenticode"])
    claims = _require_object(policy["claims"], CLAIM_KEYS, "claims")
    for key in CLAIM_KEYS:
        _require_false(claims[key], "claims", f"claim {key}")
    _validate_provenance(policy["provenance"])
    _validate_release_integration(policy["release_integration"])
    _validate_sbom(policy["sbom"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the release assurance readiness policy")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    args = parser.parse_args(argv)
    try:
        verify_policy_file(args.root)
    except (PolicyError, OSError) as exc:
        if isinstance(exc, PolicyError):
            print(f"Release assurance policy error [{exc.category}]: {exc}", file=sys.stderr)
        else:
            print("Release assurance policy error [filesystem]: policy validation failed", file=sys.stderr)
        return 1
    print("Release assurance policy verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
