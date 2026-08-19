from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


PROVIDER_POLICY_PATH = Path("legal/authenticode-provider.json")
RELEASE_POLICY_PATH = Path("legal/release-assurance-policy.json")
COMPONENTS_PATH = Path("legal/components.json")
SIGN_SCRIPT_PATH = Path("scripts/sign_authenticode.ps1")
VERIFY_SCRIPT_PATH = Path("scripts/verify_authenticode_signature.ps1")
INSTALLER_VERIFY_SCRIPT_PATH = Path("scripts/verify_esigner_cka_installer.ps1")
SANDBOX_WORKFLOW_PATH = Path(".github/workflows/authenticode-sandbox.yml")
WINDOWS_CHECKOUT_PROJECTION_PATHS = frozenset(
    {
        SIGN_SCRIPT_PATH,
        VERIFY_SCRIPT_PATH,
        INSTALLER_VERIFY_SCRIPT_PATH,
        SANDBOX_WORKFLOW_PATH,
    }
)

BASELINE_COMMIT = "45ecd30aaa95661778956b6724aaccd98bfe66c1"
FIRST_PARTY_TARGET = "Youtube.Downloaderbs.exe"
VENDOR_TARGETS = [
    "yt-dlp.exe",
    "ffmpeg.exe",
    "ffprobe.exe",
    "aria2c.exe",
    "deno.exe",
    "every other vendor-supplied executable or DLL",
]
SIGNING_SEQUENCE = [
    "validate unsigned first-party EXE structure",
    "sign only the standalone first-party EXE",
    "obtain an RFC 3161 timestamp",
    "verify Authenticode under /pa",
    "verify timestamp presence and validity",
    "verify expected publisher identity",
    "assemble the portable package from the byte-identical verified signed EXE",
    "calculate checksums and downstream assurance artifacts",
]
BLOCKERS = [
    "certificate class decision",
    "certificate issuance",
    "production final bytes",
    "production signature and timestamp verification",
    "production signing workflow integration",
    "protected credential and environment configuration",
    "protected remote signing environment",
    "provider account and identity validation",
    "remote synthetic signing validation",
    "timestamp authority approval",
]
RELEASE_BLOCKERS = sorted(
    BLOCKERS
    + [
        "CI signing identity not provisioned",
        "RFC3161 timestamp authority not approved",
    ]
)
STALE_BLOCKERS = {
    "code-signing certificate provider not selected",
    "private-key custody model not approved",
    "signed-artifact checksum sequencing not implemented",
    "signing and verification scaffold not implemented",
    "signing and verification workflow not implemented",
    "signing scaffold not implemented",
    "verification scaffold not implemented",
}
FALSE_CLAIMS = {
    "authenticode_signed",
    "provenance_attested",
    "release_assurance_ready",
    "sbom_attested",
    "signature_timestamped",
    "signature_verified",
}
FALSE_GATES = {
    "assembly_authorized",
    "legal_compliance_certified",
    "publishing_allowed",
    "release_gate_reconsideration_allowed",
    "release_ready",
    "source_assets_created",
    "source_availability_certified",
    "source_kits_ready",
}
PROVIDER_STATE = {
    "account_provisioned": False,
    "certificate_provisioned": False,
    "credential_source_configured": False,
    "custody_model_selected": True,
    "production_signing_authorized": False,
    "provider_selected": True,
    "production_signing_completed": False,
    "publishing_allowed": False,
    "release_ready": False,
    "remote_signing_validated": False,
    "sandbox_workflow_implemented": True,
    "signing_scaffold_implemented": True,
    "synthetic_signing_authorized": False,
    "timestamp_authority_approved": False,
    "verification_scaffold_implemented": True,
}
SYNTHETIC_REQUIRED_STATE = [
    "account_provisioned",
    "certificate_provisioned",
    "credential_source_configured",
    "synthetic_signing_authorized",
    "timestamp_authority_approved",
]
SYNTHETIC_NOT_REQUIRED = [
    "provider.procurement_authorized",
    "release.publishing_allowed",
    "release.release_ready",
    "state.production_signing_authorized",
    "state.remote_signing_validated",
]
PRODUCTION_REQUIRED_STATE = [
    "account_provisioned",
    "certificate_provisioned",
    "credential_source_configured",
    "production_signing_authorized",
    "remote_signing_validated",
    "synthetic_signing_authorized",
    "timestamp_authority_approved",
]
PRODUCTION_RELEASE_GATES = [
    "assembly_authorized",
    "legal_compliance_certified",
    "release_gate_reconsideration_allowed",
    "source_assets_created",
    "source_availability_certified",
    "source_kits_ready",
]
PRIVATE_KEY_HEADER_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9-]+)* PRIVATE KEY-----",
    re.IGNORECASE,
)


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


def _load_canonical_json(
    path: Path,
    label: str,
    *,
    require_sorted_keys: bool = True,
) -> dict[str, Any]:
    if not path.is_file():
        _fail("file-missing", f"{label} is missing")
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("bom", f"{label} contains a UTF-8 BOM")
    if b"\r" in raw:
        _fail("line-endings", f"{label} must use LF-only line endings")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail("final-newline", f"{label} must have exactly one final newline")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("encoding", f"{label} is not valid UTF-8")
        raise AssertionError from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate)
    except PolicyError:
        raise
    except json.JSONDecodeError as exc:
        _fail("json-syntax", f"{label} is not strict JSON at line {exc.lineno}")
        raise AssertionError from exc
    if not isinstance(value, dict):
        _fail("schema", f"{label} root must be an object")
    if require_sorted_keys:
        canonical = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if text != canonical:
            _fail("canonical-json", f"{label} is not canonical sorted JSON")
    return value


def _object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("schema", f"{label} fields are invalid")
    return value


def _string(value: Any, expected: str, category: str, label: str) -> None:
    if value != expected or not isinstance(value, str):
        _fail(category, f"{label} must be {expected}")


def _bool(value: Any, expected: bool, category: str, label: str) -> None:
    if type(value) is not bool or value is not expected:
        _fail(category, f"{label} must remain {str(expected).lower()}")


def _scan_material(value: Any, path: str = "provider policy") -> None:
    if isinstance(value, dict):
        forbidden_keys = {
            "certificate_base64",
            "credential_id",
            "otp",
            "password",
            "pfx_path",
            "private_key",
            "private_key_material",
            "token",
            "totp_secret",
        }
        for key, child in value.items():
            if key.casefold() in forbidden_keys:
                _fail("secret-material", f"secret or private-material field is forbidden at {path}")
            _scan_material(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_material(child, f"{path}[{index}]")
    elif isinstance(value, str):
        folded = value.casefold()
        if PRIVATE_KEY_HEADER_RE.search(value):
            _fail("secret-material", "private-key material is forbidden")
        if re.search(r"(?i)(?:^|[\\/])[^\\/]+\.(?:pfx|p12|pem|key)$", value):
            _fail("secret-material", "certificate or private-material path is forbidden")
        if re.search(r"(?i)(?:[a-z]:[\\/]users[\\/]|/home/|%userprofile%|\$env:userprofile)", value):
            _fail("secret-material", "local user-profile path is forbidden")
        if re.search(r"(?i)(?:password|otp|token|secret)\s*[:=]\s*\S+", value):
            _fail("secret-material", "credential value is forbidden")
        if "-----begin certificate-----" in folded:
            _fail("secret-material", "certificate material is forbidden")


def _validate_provider_policy(policy: dict[str, Any]) -> None:
    expected_top = {
        "assessment_baseline",
        "blockers",
        "certificate",
        "custody",
        "official_evidence",
        "policy_id",
        "provider",
        "repository_constraints",
        "sandbox",
        "schema_version",
        "signing_contract",
        "state",
        "timestamp",
    }
    _object(policy, expected_top, "provider policy")
    _scan_material(policy)
    _string(policy["assessment_baseline"], BASELINE_COMMIT, "baseline", "assessment baseline")
    if policy["schema_version"] != 1 or type(policy["schema_version"]) is not int:
        _fail("schema", "schema_version must be integer 1")
    _string(
        policy["policy_id"],
        "s9h-authenticode-provider-v1",
        "fixed-value",
        "provider policy ID",
    )
    if isinstance(policy["blockers"], list) and STALE_BLOCKERS.intersection(policy["blockers"]):
        _fail("stale-blocker", "provider policy contains a stale Authenticode blocker")
    if policy["blockers"] != BLOCKERS:
        _fail("blockers", "required Authenticode blockers changed")

    provider = _object(
        policy["provider"],
        {
            "alternatives",
            "id",
            "private_key_export",
            "procurement_authorized",
            "service",
        },
        "provider",
    )
    _string(provider["id"], "ssl-com-esigner", "provider", "provider ID")
    _string(provider["service"], "SSL.com eSigner", "provider", "provider service")
    _string(provider["private_key_export"], "forbidden", "custody", "private-key export policy")
    _bool(provider["procurement_authorized"], False, "readiness", "provider procurement_authorized")
    alternatives = provider["alternatives"]
    if not isinstance(alternatives, list) or [item.get("id") for item in alternatives] != [
        "microsoft-artifact-signing-public-trust",
        "signpath-foundation",
        "exportable-pfx-in-repository-secrets",
    ]:
        _fail("alternatives", "provider alternatives changed")
    for item in alternatives:
        if set(item) != {"id", "reason", "selected"} or not item["reason"]:
            _fail("alternatives", "provider alternative evidence is invalid")
        _bool(item["selected"], False, "alternatives", f"alternative {item['id']} selected")

    custody = _object(
        policy["custody"],
        {
            "model",
            "non_exportable_provider_key_required",
            "private_key_export_allowed",
            "repository_pfx_allowed",
            "repository_private_key_allowed",
        },
        "custody",
    )
    _string(custody["model"], "provider-managed cloud HSM", "custody", "custody model")
    _bool(
        custody["non_exportable_provider_key_required"],
        True,
        "custody",
        "non-exportable provider key requirement",
    )
    for key in ("private_key_export_allowed", "repository_pfx_allowed", "repository_private_key_allowed"):
        _bool(custody[key], False, "custody", f"custody {key}")

    certificate = _object(
        policy["certificate"],
        {
            "automated_certificate_classes",
            "certificate_class_selected",
            "expected_publisher",
            "expected_thumbprint",
            "operator_entity_type",
            "preferred_class",
            "product_advertised_certificate_classes",
            "provider_confirmation_required_for_iv_automation",
        },
        "certificate",
    )
    _bool(certificate["certificate_class_selected"], False, "certificate-class", "certificate class selected")
    _bool(
        certificate["provider_confirmation_required_for_iv_automation"],
        True,
        "certificate-class",
        "IV automation provider confirmation",
    )
    if certificate["automated_certificate_classes"] != ["OV", "EV"]:
        _fail("certificate-class", "automated certificate classes must remain OV and EV")
    if certificate["product_advertised_certificate_classes"] != ["IV", "OV", "EV"]:
        _fail("certificate-class", "product-advertised certificate classes changed")
    _string(
        certificate["operator_entity_type"],
        "UNRESOLVED",
        "certificate-class",
        "operator entity type",
    )
    if certificate["preferred_class"] is not None:
        _fail("certificate-class", "preferred certificate class must remain unset")
    if certificate["expected_publisher"] is not None or certificate["expected_thumbprint"] is not None:
        _fail("publisher", "certificate identity must remain unset before provisioning")

    state = _object(policy["state"], set(PROVIDER_STATE), "provider state")
    for key, expected in PROVIDER_STATE.items():
        _bool(state[key], expected, "readiness", f"provider state {key}")

    timestamp = _object(
        policy["timestamp"],
        {"authority_approved", "candidate_url", "digest", "protocol"},
        "timestamp",
    )
    _bool(timestamp["authority_approved"], False, "timestamp", "timestamp authority approved")
    _string(timestamp["candidate_url"], "http://ts.ssl.com", "timestamp", "candidate timestamp URL")
    _string(timestamp["protocol"], "RFC3161", "timestamp", "timestamp protocol")
    _string(timestamp["digest"], "SHA256", "timestamp", "timestamp digest")

    contract = _object(
        policy["signing_contract"],
        {
            "certificate_identifier_logging",
            "file_digest",
            "first_party_targets",
            "plan_only_supported",
            "purpose_gates",
            "real_signing_purposes",
            "sequence",
            "third_party_targets_rejected",
            "timestamp_digest",
            "timestamp_protocol",
            "unsigned_fallback_representation",
            "verification_policy",
            "verification_switch",
        },
        "signing contract",
    )
    _string(contract["certificate_identifier_logging"], "forbidden", "logging", "certificate logging")
    _string(contract["file_digest"], "SHA256", "signing-contract", "file digest")
    _string(contract["timestamp_protocol"], "RFC3161", "signing-contract", "timestamp protocol")
    _string(contract["timestamp_digest"], "SHA256", "signing-contract", "timestamp digest")
    _string(
        contract["unsigned_fallback_representation"],
        "forbidden",
        "unsigned-fallback",
        "unsigned fallback representation",
    )
    _string(
        contract["verification_policy"],
        "Default Authenticode",
        "verification-contract",
        "verification policy",
    )
    _string(contract["verification_switch"], "/pa", "verification-contract", "verification switch")
    _bool(contract["plan_only_supported"], True, "signing-purpose", "PlanOnly support")
    if contract["first_party_targets"] != [FIRST_PARTY_TARGET]:
        _fail("target", "first-party signing target changed")
    if contract["third_party_targets_rejected"] != VENDOR_TARGETS:
        _fail("target", "vendor target exclusions changed")
    if contract["sequence"] != SIGNING_SEQUENCE:
        _fail("ordering", "signing and final-byte sequence changed")
    if contract["real_signing_purposes"] != ["synthetic", "production"]:
        _fail("signing-purpose", "real signing purposes must be synthetic and production")
    purpose_gates = _object(contract["purpose_gates"], {"production", "synthetic"}, "purpose gates")
    synthetic = _object(
        purpose_gates["synthetic"],
        {"not_required_flags", "required_provider_state"},
        "synthetic purpose gates",
    )
    production = _object(
        purpose_gates["production"],
        {"required_provider_state", "required_release_gates"},
        "production purpose gates",
    )
    if synthetic["required_provider_state"] != SYNTHETIC_REQUIRED_STATE:
        _fail("signing-purpose", "synthetic signing prerequisites changed")
    if synthetic["not_required_flags"] != SYNTHETIC_NOT_REQUIRED:
        _fail("signing-purpose", "synthetic signing exclusions changed")
    if production["required_provider_state"] != PRODUCTION_REQUIRED_STATE:
        _fail("signing-purpose", "production signing prerequisites changed")
    if production["required_release_gates"] != PRODUCTION_RELEASE_GATES:
        _fail("signing-purpose", "production release gates changed")

    constraints = _object(
        policy["repository_constraints"],
        {"project_license_status", "project_license_status_change_authorized"},
        "repository constraints",
    )
    _string(
        constraints["project_license_status"],
        "not-selected",
        "project-license",
        "project license status",
    )
    _bool(
        constraints["project_license_status_change_authorized"],
        False,
        "project-license",
        "project license status change authorization",
    )

    sandbox = _object(
        policy["sandbox"],
        {
            "account_type",
            "automated_certificate_classes",
            "certificate_class_selected_for_production",
            "certificate_secret_required_by_current_cli",
            "environment",
            "expected_secret_names",
            "expected_variable_names",
            "production_certificate_provisioned",
            "workflow_live_validation_completed",
        },
        "sandbox",
    )
    _string(sandbox["account_type"], "sandbox", "sandbox", "sandbox account type")
    _string(
        sandbox["environment"],
        "authenticode-sandbox",
        "sandbox",
        "sandbox environment",
    )
    if sandbox["automated_certificate_classes"] != ["OV", "EV"]:
        _fail("certificate-class", "sandbox automated classes must remain OV and EV")
    if sandbox["expected_secret_names"] != [
        "ESIGNER_SANDBOX_PASSWORD",
        "ESIGNER_SANDBOX_TOTP_SECRET",
        "ESIGNER_SANDBOX_USERNAME",
    ]:
        _fail("sandbox-secrets", "sandbox secret-name contract changed")
    if sandbox["expected_variable_names"] != [
        "ESIGNER_SANDBOX_CERTIFICATE_CLASS",
        "ESIGNER_SANDBOX_EXPECTED_PUBLISHER",
        "ESIGNER_SANDBOX_EXPECTED_THUMBPRINT",
        "ESIGNER_SANDBOX_SIGNING_AUTHORIZED",
    ]:
        _fail("sandbox-secrets", "sandbox variable-name contract changed")
    for key in (
        "certificate_class_selected_for_production",
        "certificate_secret_required_by_current_cli",
        "production_certificate_provisioned",
        "workflow_live_validation_completed",
    ):
        _bool(sandbox[key], False, "readiness", f"sandbox {key}")

    evidence = _object(
        policy["official_evidence"],
        {
            "authentication",
            "certificate_discovery",
            "cka",
            "evidence_retrieved_at_utc",
            "integration",
            "sources",
        },
        "official evidence",
    )
    if not re.fullmatch(r"2026-08-18T\d{2}:\d{2}:\d{2}Z", evidence["evidence_retrieved_at_utc"]):
        _fail("provider-evidence", "official evidence retrieval time is invalid")
    sources = evidence["sources"]
    if not isinstance(sources, list) or sources != sorted(sources) or not sources:
        _fail("provider-evidence", "official sources must be a non-empty sorted list")
    if any(not source.startswith("https://www.ssl.com/") for source in sources):
        _fail("provider-evidence", "only official SSL.com sources are allowed")

    cka = _object(
        evidence["cka"],
        {
            "architecture_evidence",
            "cka_package_integrated",
            "current_release_build",
            "current_release_label",
            "current_release_version",
            "download_page",
            "downloadable_windows_package",
            "immutable_digest",
            "immutable_package_identity_established",
            "install_command",
            "install_command_documented",
            "package_identity",
            "removal_command",
            "removal_command_documented",
        },
        "CKA evidence",
    )
    _string(cka["current_release_version"], "1.1.2", "provider-package", "CKA version")
    _string(cka["current_release_build"], "20260062", "provider-package", "CKA build")
    _string(
        cka["current_release_label"],
        "SSL-COM-eSigner-CKA_1-1-2_build_20260062",
        "provider-package",
        "CKA release label",
    )
    _string(
        cka["downloadable_windows_package"],
        "SSL.COM eSigner CKA_1.1.2_build_202600624.exe",
        "provider-package",
        "CKA Windows package",
    )
    for key in ("cka_package_integrated", "immutable_package_identity_established"):
        _bool(cka[key], True, "provider-package", f"CKA {key}")
    _bool(cka["removal_command_documented"], False, "provider-package", "CKA removal command documented")
    _bool(cka["install_command_documented"], True, "provider-package", "CKA install command documented")
    _string(
        cka["immutable_digest"],
        "3f088403139505ddfb0ed3b56b72893f92c865f98b382753a1e1c695a5cece35",
        "provider-package",
        "CKA immutable digest",
    )
    _string(
        cka["architecture_evidence"],
        "x86 PE machine identity verified from the exact repository-pinned official linked bytes",
        "provider-package",
        "CKA architecture evidence",
    )
    _string(
        cka["removal_command"],
        "Discover exactly one unins*.exe under the controlled installation root and invoke /VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
        "provider-package",
        "CKA removal behavior",
    )
    if "VERYSILENT" not in cka["install_command"] or "<INSTALL_DIR>" not in cka["install_command"]:
        _fail("provider-package", "CKA documented install command changed")

    identity = _object(
        cka["package_identity"],
        {
            "architecture",
            "authenticode_status_required",
            "byte_size",
            "digest_provenance",
            "file_version",
            "filename_matches_release_label_exactly",
            "official_resource_url",
            "product_name",
            "product_version",
            "provider",
            "publisher_filename_discrepancy_documented",
            "release_page_label",
            "resource_display_filename",
            "resource_namespace",
            "sha256",
            "signer_issuer",
            "signer_serial",
            "signer_simple_name",
            "signer_thumbprint",
            "timestamp_certificate_required",
        },
        "CKA package identity",
    )
    expected_identity = {
        "architecture": "x86",
        "authenticode_status_required": "Valid",
        "byte_size": 16103264,
        "file_version": "1.1.2",
        "official_resource_url": "https://app.esigner.com/documents/7d2c9a7f-99fe-4053-bb1e-07e485a739cf/final",
        "product_name": "SSL.COM eSigner Cloud Key Adapter",
        "product_version": "1.1.2",
        "provider": "SSL.com",
        "release_page_label": "SSL-COM-eSigner-CKA_1-1-2_build_20260062",
        "resource_display_filename": "SSL.COM eSigner CKA_1.1.2_build_202600624.exe",
        "resource_namespace": "SSL-COM-eSigner-CKA_1-1-2_build_20260062",
        "sha256": "3f088403139505ddfb0ed3b56b72893f92c865f98b382753a1e1c695a5cece35",
        "signer_issuer": "CN=SSL.com EV Code Signing Intermediate CA RSA R3, O=SSL Corp, L=Houston, S=Texas, C=US",
        "signer_serial": "03987FF7E46C81A6B4343A575FA0F8F3",
        "signer_simple_name": "SSL Corp",
        "signer_thumbprint": "B40BDE1B8DBA07DEC2D1E7EDFADD9B1BC51F922D",
    }
    for key, expected in expected_identity.items():
        if identity[key] != expected or type(identity[key]) is not type(expected):
            _fail("provider-package", f"CKA package identity changed: {key}")
    _bool(
        identity["filename_matches_release_label_exactly"],
        False,
        "provider-package",
        "CKA filename exact-match state",
    )
    _bool(
        identity["publisher_filename_discrepancy_documented"],
        True,
        "provider-package",
        "CKA filename discrepancy state",
    )
    _bool(
        identity["timestamp_certificate_required"],
        True,
        "provider-package",
        "CKA timestamp requirement",
    )
    _string(
        identity["digest_provenance"],
        "Repository-calculated SHA-256 of the exact official SSL.com linked bytes after Authenticode validation; not represented as an SSL.com-published checksum",
        "provider-package",
        "CKA digest provenance",
    )

    integration = _object(
        evidence["integration"],
        {"interface", "key_storage_provider", "supported_tools"},
        "provider integration",
    )
    _string(integration["interface"], "Windows CNG", "provider-integration", "provider interface")
    _string(integration["key_storage_provider"], "KSP", "provider-integration", "provider KSP")
    if integration["supported_tools"] != ["certutil.exe", "signtool.exe"]:
        _fail("provider-integration", "supported provider tools changed")

    authentication = _object(
        evidence["authentication"],
        {"automated_mode", "manual_mode", "repository_credential_storage_authorized"},
        "provider authentication",
    )
    _bool(
        authentication["repository_credential_storage_authorized"],
        False,
        "custody",
        "repository credential storage authorization",
    )
    if "OV or EV" not in authentication["automated_mode"] or "OTP" not in authentication["manual_mode"]:
        _fail("provider-authentication", "provider authentication constraints changed")

    discovery = _object(
        evidence["certificate_discovery"],
        {"provider_behavior", "repository_selection", "selection_on_multiple_matches"},
        "certificate discovery",
    )
    if "CurrentUser Personal" not in discovery["provider_behavior"]:
        _fail("provider-integration", "provider certificate-store behavior changed")
    _string(
        discovery["repository_selection"],
        "explicit certificate thumbprint required after provisioning",
        "publisher",
        "repository certificate selection",
    )
    _string(
        discovery["selection_on_multiple_matches"],
        "fail closed",
        "publisher",
        "multiple certificate selection policy",
    )


def _validate_release_policy(policy: dict[str, Any]) -> None:
    auth = policy.get("authenticode")
    claims = policy.get("claims")
    integration = policy.get("release_integration")
    if not isinstance(auth, dict) or not isinstance(claims, dict) or not isinstance(integration, dict):
        _fail("release-policy", "release assurance policy structure changed")
    if auth.get("first_party_signing_targets") != [FIRST_PARTY_TARGET]:
        _fail("target", "release policy first-party signing target changed")
    if auth.get("file_digest") != "SHA256":
        _fail("signing-contract", "release policy file digest changed")
    if auth.get("verification_policy") != "Default Authenticode" or auth.get("verification_switch") != "/pa":
        _fail("verification-contract", "release policy /pa verification changed")
    timestamp = auth.get("candidate_timestamp")
    if timestamp != {
        "digest": "SHA256",
        "protocol": "RFC3161",
        "timestamp_authority_selected": False,
    }:
        _fail("timestamp", "release policy timestamp contract changed")
    for key in ("certificate_provisioned", "credential_source_selected", "implementation_status", "readiness"):
        _bool(auth.get(key), False, "readiness", f"release policy authenticode {key}")
    _bool(
        auth.get("signing_key_custody_approved"),
        False,
        "readiness",
        "release policy provisioned signing-key custody approval",
    )
    boundary = auth.get("third_party_resigning")
    if not isinstance(boundary, dict) or boundary.get("allowed") is not False:
        _fail("target", "release policy third-party signing boundary changed")
    if set(boundary.get("excluded", [])) != {
        "yt-dlp.exe",
        "ffmpeg.exe",
        "ffprobe.exe",
        "aria2c.exe",
        "deno.exe",
        "any other vendor-supplied binary",
    }:
        _fail("target", "release policy vendor exclusions changed")
    blockers = auth.get("blockers")
    if isinstance(blockers, list) and STALE_BLOCKERS.intersection(blockers):
        _fail("stale-blocker", "release policy contains a stale Authenticode blocker")
    if blockers != RELEASE_BLOCKERS:
        _fail("blockers", "release policy Authenticode blockers changed")
    for key in FALSE_CLAIMS:
        _bool(claims.get(key), False, "nonclaims", f"release claim {key}")
    readiness = integration.get("readiness")
    gates = integration.get("existing_gate_invariants")
    if not isinstance(readiness, dict) or not isinstance(gates, dict):
        _fail("release-policy", "release readiness or gates are invalid")
    for key, value in readiness.items():
        _bool(value, False, "nonclaims", f"release readiness {key}")
    for key in FALSE_GATES:
        _bool(gates.get(key), False, "nonclaims", f"release gate {key}")


def _validate_project_license(components: dict[str, Any], provider: dict[str, Any]) -> None:
    if components.get("project_license_status") != "not-selected":
        _fail("project-license", "repository project license status changed")
    expected = provider["repository_constraints"]["project_license_status"]
    if expected != components["project_license_status"]:
        _fail("project-license", "provider policy project license status does not match legal inventory")


def _decode_lf_script(raw: bytes, relative: Path, *, source: str) -> str:
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        _fail(
            "script-hygiene",
            f"{relative.as_posix()} {source} must be UTF-8 without BOM and LF-only",
        )
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _fail(
            "script-hygiene",
            f"{relative.as_posix()} {source} must have exactly one final newline",
        )
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("script-hygiene", f"{relative.as_posix()} {source} is not UTF-8")
        raise AssertionError from exc


def _read_committed_script_blob(root: Path, relative: Path) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "blob",
                f"HEAD:{relative.as_posix()}",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        _fail(
            "script-hygiene",
            f"{relative.as_posix()} worktree representation could not be proven against Git HEAD",
        )
        raise AssertionError from exc
    if result.returncode != 0:
        _fail(
            "script-hygiene",
            f"{relative.as_posix()} worktree representation could not be proven against Git HEAD",
        )
    committed = result.stdout
    _decode_lf_script(committed, relative, source="committed Git blob")
    return committed


def _read_script(root: Path, relative: Path) -> str:
    path = root / relative
    if not path.is_file():
        _fail("script-missing", f"{relative.as_posix()} is missing")
    raw = path.read_bytes()
    if relative not in WINDOWS_CHECKOUT_PROJECTION_PATHS:
        return _decode_lf_script(raw, relative, source="worktree file")
    committed = _read_committed_script_blob(root, relative)
    committed_text = committed.decode("utf-8", errors="strict")
    if raw == committed:
        return committed_text
    if raw == committed.replace(b"\n", b"\r\n"):
        return committed_text
    _fail(
        "script-hygiene",
        f"{relative.as_posix()} worktree bytes are not an exact LF or full CRLF projection of the committed Git blob",
    )
    raise AssertionError


def _require_source(source: str, markers: list[str], category: str, label: str) -> None:
    for marker in markers:
        if marker not in source:
            _fail(category, f"{label} is missing required contract marker: {marker}")


def _validate_scripts(root: Path) -> None:
    sign = _read_script(root, SIGN_SCRIPT_PATH)
    verify = _read_script(root, VERIFY_SCRIPT_PATH)
    installer_verify = _read_script(root, INSTALLER_VERIFY_SCRIPT_PATH)
    sandbox_workflow = _read_script(root, SANDBOX_WORKFLOW_PATH)
    for label, source in (("signing script", sign), ("verification script", verify)):
        if re.search(r"(?i)\b(?:Invoke-WebRequest|Start-BitsTransfer|curl|wget|msiexec|Expand-Archive)\b", source):
            _fail("download-install", f"{label} must not download or install software")
        if re.search(r"(?i)\$(?:password|otp|token|privatekey|pfx)", source):
            _fail("secret-logging", f"{label} exposes a secret-bearing parameter or variable")
        if re.search(r"(?i)Write-(?:Host|Output|Verbose|Information).*(?:thumbprint|certificateidentifier)", source):
            _fail("secret-logging", f"{label} logs a certificate identifier")

    _require_source(
        sign,
        [
            "[switch]$PlanOnly",
            '[ValidateSet("synthetic", "production")]',
            "Assert-AuthorizedTarget",
            "Assert-UnsignedPeStructure",
            "Youtube.Downloaderbs.exe",
            '"sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/sha1"',
            "A real signing purpose must be explicitly selected",
            'if ($SigningPurpose -ceq "production")',
            "Synthetic signing prerequisites are not satisfied",
            "synthetic_signing_authorized",
            "timestamp_authority_approved",
            "$Config.state.certificate_provisioned",
            "credential_source_configured",
            "production_signing_authorized",
            "remote_signing_validated",
            "Read-ReleaseAssurancePolicy",
            "Normalize-CertificateThumbprint",
            "verify_authenticode_signature.ps1",
            "-CertificateThumbprint $NormalizedThumbprint",
            "& $SignToolPath @SignArguments",
        ],
        "signing-script",
        "signing script",
    )
    _require_source(
        verify,
        [
            "Assert-AuthorizedTarget",
            "Youtube.Downloaderbs.exe",
            '"verify", "/pa", "/all", "/v", "/tw"',
            "Get-AuthenticodeSignature",
            "Timestamp Verified by",
            "RFC3161",
            "SHA256",
            "ExpectedPublisher",
            "CertificateThumbprint",
            "SignerCertificate.Thumbprint",
            "Normalize-CertificateThumbprint",
            "GetNameInfo",
            "[StringComparison]::Ordinal",
            "Get-FileHash",
        ],
        "verification-script",
        "verification script",
    )
    _require_source(
        installer_verify,
        [
            "Assert-InstallerPath",
            "Get-PeArchitecture",
            "Get-FileHash",
            "Get-AuthenticodeSignature",
            "SignerCertificate",
            "TimeStamperCertificate",
            "signer_serial",
            "signer_thumbprint",
            "signer_issuer",
            "FileVersion",
            "ProductVersion",
            "ProductName",
            "ReparsePoint",
            "StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)",
        ],
        "installer-verifier",
        "CKA installer verifier",
    )
    if re.search(r"(?i)\b(?:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer|curl|wget)\b", installer_verify):
        _fail("installer-verifier", "CKA installer verifier must remain network-free")
    _require_source(
        sandbox_workflow,
        [
            "workflow_dispatch:",
            "environment: authenticode-sandbox",
            "contents: read",
            "refs/heads/main",
            "Verify exact CKA installer identity before execution",
            "verify_esigner_cka_installer.ps1",
            "Install verified CKA in controlled location",
            "-mode sandbox",
            "-SigningPurpose synthetic",
            "SANDBOX_EXPECTED_THUMBPRINT",
            "SANDBOX_EXPECTED_PUBLISHER",
            "HasPrivateKey",
            "Unsigned SHA-256",
            "Signed SHA-256",
            "if: always()",
            "unload",
            "Remove-Item -LiteralPath $SandboxRoot -Recurse -Force",
        ],
        "sandbox-workflow",
        "Authenticode sandbox workflow",
    )
    verify_call = "& .\\scripts\\verify_esigner_cka_installer.ps1"
    installer_call = "& $env:CKA_INSTALLER_PATH"
    if sandbox_workflow.index(verify_call) > sandbox_workflow.index(installer_call):
        _fail("ordering", "CKA installer verification must precede installer execution")
    if "uses:" in sandbox_workflow:
        _fail("sandbox-workflow", "Authenticode sandbox workflow must remain actionless")
    if re.search(r"(?m)^\s{2}(?:push|pull_request|pull_request_target|schedule|workflow_run|repository_dispatch):", sandbox_workflow):
        _fail("sandbox-workflow", "Authenticode sandbox workflow must remain manual-only")
    if sign.index("Assert-UnsignedPeStructure") > sign.index("& $SignToolPath @SignArguments"):
        _fail("ordering", "unsigned PE validation must precede signing")
    if sign.index("& $SignToolPath @SignArguments") > sign.index("verify_authenticode_signature.ps1"):
        _fail("ordering", "verification must follow signing")
    if sign.index("if ($PlanOnly)") > sign.index("A real signing purpose must be explicitly selected"):
        _fail("signing-purpose", "PlanOnly must bypass real signing-purpose gates")
    production_branch = sign.index('if ($SigningPurpose -ceq "production")')
    for marker in ("$Config.state.production_signing_authorized", "$Config.state.remote_signing_validated"):
        if sign.index(marker) < production_branch:
            _fail("signing-purpose", "production-only state leaked into synthetic signing gates")
    if sign.index("$ReleasePolicy = Read-ReleaseAssurancePolicy") < production_branch:
        _fail("signing-purpose", "release-assurance gates must be production-only")
    for marker in PRODUCTION_RELEASE_GATES:
        if f'"{marker}"' not in sign:
            _fail("signing-purpose", f"production release gate is missing: {marker}")
    if "procurement_authorized" in sign:
        _fail("signing-purpose", "procurement authorization must not gate signing execution")
    for marker in ("release_ready", "publishing_allowed"):
        if marker in sign:
            _fail("signing-purpose", f"derived release state must not gate signing execution: {marker}")
    if "SignerCertificate.Subject.IndexOf" in verify:
        _fail("publisher", "publisher substring matching must not be the primary identity control")
    if "$SignerPublisher.IndexOf" in verify:
        _fail("publisher", "publisher display identity must use exact comparison")


def verify_authenticode_policy(root: Path) -> None:
    provider = _load_canonical_json(root / PROVIDER_POLICY_PATH, "Authenticode provider policy")
    release = _load_canonical_json(root / RELEASE_POLICY_PATH, "release assurance policy")
    components = _load_canonical_json(
        root / COMPONENTS_PATH,
        "component inventory",
        require_sorted_keys=False,
    )
    _validate_provider_policy(provider)
    _validate_release_policy(release)
    _validate_project_license(components, provider)
    _validate_scripts(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the fail-closed Authenticode provider policy")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    args = parser.parse_args(argv)
    try:
        verify_authenticode_policy(args.root)
    except (PolicyError, OSError) as exc:
        if isinstance(exc, PolicyError):
            print(f"Authenticode policy error [{exc.category}]: {exc}", file=sys.stderr)
        else:
            print("Authenticode policy error [filesystem]: validation failed", file=sys.stderr)
        return 1
    print("Authenticode provider and signing policy verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
