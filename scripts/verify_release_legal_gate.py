from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import source_compliance
import ffmpeg_correspondence
import release_authorization
import verify_release_legal_payload as payload_verifier


TOP_LEVEL_KEYS = (
    "schema_version",
    "policy_mode",
    "legal_compliance_certified",
    "source_availability_certified",
    "release_payload_integrated",
    "releases",
)
RELEASE_KEYS = ("tag", "status", "reason_codes", "required_phase")
EXPECTED_TAGS = (
    "v1.2.7-rc.1",
    "v1.3.0",
    "v1.3.0-rc.1",
    "v1.3.1",
    "v1.3.2",
)
EXPECTED_REASONS = {
    "v1.2.7-rc.1": (
        "historical-runtime-correspondence-not-certified",
        "release-legal-payload-not-integrated",
    ),
    "v1.3.0": (
        "aria2-source-availability-not-integrated",
        "ffmpeg-corresponding-source-not-certified",
        "release-legal-payload-not-integrated",
    ),
    "v1.3.0-rc.1": (
        "aria2-source-availability-not-integrated",
        "ffmpeg-corresponding-source-not-certified",
        "historical-runtime-correspondence-not-certified",
        "release-legal-payload-not-integrated",
    ),
    "v1.3.1": (
        "aria2-source-availability-not-integrated",
        "ffmpeg-corresponding-source-not-certified",
        "release-legal-payload-not-integrated",
    ),
    "v1.3.2": (
        "aria2-source-availability-not-integrated",
        "ffmpeg-corresponding-source-not-certified",
        "release-legal-payload-not-integrated",
    ),
}
LOCAL_PATH_RE = re.compile(r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)")
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
)


class ReleaseLegalGateError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the fail-closed release legal gate")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-owner", type=Path)
    parser.add_argument("--source-assets-root", type=Path)
    parser.add_argument("--legal-payload", type=Path)
    parser.add_argument("--stage", choices=("prebuild", "final"), default="final")
    parser.add_argument("--control-root", type=Path)
    parser.add_argument("--portable-zip", type=Path)
    parser.add_argument("--release-notes", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--control-commit")
    args = parser.parse_args()
    try:
        policy = load_policy(args.policy)
        release = release_for_tag(policy, args.tag)
        owner_path = args.source_owner or args.policy.resolve().parent / "source-compliance-v1.3.2.json"
        owner = source_compliance.load_owner(owner_path)
        control_root = args.control_root or args.policy.resolve().parent.parent
        if args.stage == "prebuild":
            state = validate_prebuild_control(policy, release, owner, control_root=control_root)
            if state["technical_source_ready"]:
                print(f"Prebuild technical control gate passed for {release['tag']}; release_payload_integrated=false")
                return 0
        else:
            state = validate_release_evidence(
                policy, release, owner, source_assets_root=args.source_assets_root,
                legal_payload=args.legal_payload, control_root=control_root,
                portable_zip=args.portable_zip, release_notes=args.release_notes,
                source_commit=args.source_commit, control_commit=args.control_commit,
            )
    except (ReleaseLegalGateError, source_compliance.SourceComplianceError,
            release_authorization.AuthorizationError, payload_verifier.LegalPayloadError,
            OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"Release legal gate error: {exc}", file=sys.stderr)
        return 2
    if state["release_ready"]:
        print(f"Release legal gate ready for {release['tag']}")
        return 0
    reasons = ", ".join(state["release_blockers"])
    print(f"Release legal gate blocked for {release['tag']}: {reasons}")
    return 1


def canonical_policy_bytes(policy: dict[str, Any]) -> bytes:
    return (json.dumps(policy, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_policy(path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), "policy contains a UTF-8 BOM")
    _require(b"\r" not in raw, "policy must use LF line endings")
    _require(b"\0" not in raw, "policy contains NUL")
    try:
        policy = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseLegalGateError("policy JSON is malformed") from exc
    validate_policy_document(policy)
    _require(raw == canonical_policy_bytes(policy), "policy JSON is not deterministic")
    return policy


def validate_policy_document(policy: Any) -> dict[str, Any]:
    _require(isinstance(policy, dict), "policy root must be an object")
    _require(tuple(policy) == TOP_LEVEL_KEYS, "policy top-level schema or field order is invalid")
    _require(policy["schema_version"] == 1, "policy schema_version must be 1")
    _require(policy["policy_mode"] == "fail-closed", "policy mode must be fail-closed")
    flags = (
        policy["legal_compliance_certified"],
        policy["source_availability_certified"],
        policy["release_payload_integrated"],
    )
    _require(all(type(flag) is bool for flag in flags), "release certification flags are invalid")
    _require(flags in ((False, False, False), (False, True, False), (True, True, False)), "release certification flags are inconsistent or prematurely claim payload integration")

    releases = policy["releases"]
    _require(isinstance(releases, list) and len(releases) == len(EXPECTED_TAGS), "policy must contain exactly five releases")
    tags: list[str] = []
    for release in releases:
        _require(isinstance(release, dict) and tuple(release) == RELEASE_KEYS, "release policy record schema is invalid")
        tag = release["tag"]
        _require(isinstance(tag, str), "release tag is invalid")
        if tag != "v1.3.2":
            _require(release["status"] == "blocked", f"historical release must remain blocked: {tag}")
        _require(release["status"] in {"blocked", "technical-ready", "authorized-ready"}, f"release status is invalid: {tag}")
        reasons = release["reason_codes"]
        _require(isinstance(reasons, list), f"release reasons are invalid: {tag}")
        _require(all(isinstance(reason, str) and re.fullmatch(r"[a-z0-9-]+", reason) for reason in reasons), f"release reason is invalid: {tag}")
        _require(len(reasons) == len(set(reasons)), f"release reasons are duplicated: {tag}")
        _require(reasons == sorted(reasons), f"release reasons are not sorted: {tag}")
        _require(release["required_phase"] == "6B2", f"required phase is invalid: {tag}")
        tags.append(tag)

    _require(len(tags) == len(set(tags)), "release tags must be unique")
    _require(tuple(tags) == EXPECTED_TAGS, "release tags are missing or unsorted")
    for release in releases:
        tag = release["tag"]
        if tag != "v1.3.2":
            _require(release["status"] == "blocked", f"historical release status changed: {tag}")
            _require(tuple(release["reason_codes"]) == EXPECTED_REASONS[tag], f"historical release reasons changed: {tag}")
        elif release["status"] == "blocked":
            _require(tuple(release["reason_codes"]) == EXPECTED_REASONS[tag], "v1.3.2 blockers are incomplete")
        else:
            _require(release["reason_codes"] == [], "ready v1.3.2 has blockers")
    current = next(release for release in releases if release["tag"] == "v1.3.2")
    expected_flags = {
        "blocked": (False, False, False),
        "technical-ready": (False, True, False),
        "authorized-ready": (True, True, False),
    }
    _require(flags == expected_flags[current["status"]], "v1.3.2 state and certification flags disagree")
    _verify_hygiene(policy)
    return policy


def release_for_tag(policy: dict[str, Any], tag: str) -> dict[str, Any]:
    validate_policy_document(policy)
    _require(tag in EXPECTED_TAGS, "requested tag is not an exact current workflow tag")
    matches = [release for release in policy["releases"] if release["tag"] == tag]
    _require(len(matches) == 1, "requested tag is missing or duplicated")
    return matches[0]


def validate_prebuild_control(
    policy: dict[str, Any],
    release: dict[str, Any],
    owner: dict[str, Any],
    *,
    control_root: Path | None,
) -> dict[str, Any]:
    validate_policy_document(policy)
    source_compliance.validate_owner(owner)
    _require(owner["release_tag"] == "v1.3.2", "source owner release tag is invalid")
    if release["status"] == "blocked":
        _require(bool(release["reason_codes"]), "blocked release has no reasons")
        return {
            "state": "BLOCKED",
            "technical_source_ready": False,
            "legal_release_authorized": False,
            "release_ready": False,
            "legal_compliance_certified": False,
            "source_availability_certified": False,
            "release_payload_integrated": False,
            "release_blockers": list(release["reason_codes"]),
        }
    _require(release["tag"] == "v1.3.2", "only v1.3.2 may enter ready state")
    _require(owner["legal_compliance_certified"] is False, "technical source owner cannot authorize legal release")
    _require(owner["source_availability_certified"] is True, "source availability is not certified")
    _require(all(kit["status"] == "ready" and not kit["blockers"] for kit in owner["kits"]), "required source kits are not ready")
    _require(control_root is not None, "ready workflow control root is required")
    control_root = payload_verifier.require_regular_directory(control_root, "workflow control root")
    _require(load_policy(control_root / "legal/release-policy.json") == policy, "control policy mismatch")
    _require(source_compliance.load_owner(control_root / source_compliance.OWNER_PATH) == owner, "control source owner mismatch")
    correspondence = ffmpeg_correspondence.load_record(control_root / ffmpeg_correspondence.RECORD_PATH)
    ffmpeg_correspondence.require_ready_owner(correspondence, owner)
    contract = payload_verifier.load_asset_contract(control_root / payload_verifier.CONTRACT_PATH)
    declared_authorized = release["status"] == "authorized-ready"
    expected_contract_state = "ready" if declared_authorized else "technical-ready"
    _require(contract["release_readiness"] == expected_contract_state, "asset contract and prebuild policy disagree")
    authorization = release_authorization.load_authorization(control_root / release_authorization.AUTHORIZATION_PATH)
    authorized = release_authorization.verify_authorization(authorization, policy=policy, owner=owner, contract=contract, correspondence=correspondence)
    _require(authorized == declared_authorized, "explicit legal authorization and policy disagree")
    return {
        "state": "TECHNICAL_READY",
        "technical_source_ready": True,
        "legal_release_authorized": authorized,
        "release_ready": False,
        "legal_compliance_certified": False,
        "source_availability_certified": False,
        "release_payload_integrated": False,
        "release_blockers": ["final-release-evidence-not-verified"] + ([] if authorized else ["legal-release-authorization-required"]),
    }


def validate_release_evidence(
    policy: dict[str, Any], release: dict[str, Any], owner: dict[str, Any], *,
    source_assets_root: Path | None, legal_payload: Path | None,
    control_root: Path | None = None, portable_zip: Path | None = None,
    release_notes: Path | None = None, source_commit: str | None = None,
    control_commit: str | None = None,
) -> dict[str, Any]:
    state = validate_prebuild_control(policy, release, owner, control_root=control_root)
    if not state["technical_source_ready"]:
        return state
    _require(source_assets_root is not None, "ready source assets root is required")
    source_assets_root = payload_verifier.require_regular_directory(source_assets_root, "source assets root")
    for kit in owner["kits"]:
        name = f"Youtube-Downloaderbs-{release['tag']}-{kit['id']}-source.zip"
        _require(kit["source_asset"]["filename"] == name, "source asset filename is not exact")
        payload_verifier.require_regular_file(source_assets_root / name, source_assets_root, name)
        source_compliance.verify_source_asset(owner, kit["id"], source_assets_root / name)
    _require(legal_payload is not None and portable_zip is not None and release_notes is not None, "complete legal payload evidence is required")
    _require(source_commit is not None and control_commit is not None, "final release commit identities are required")
    _require(legal_payload.name == f"Youtube-Downloaderbs-{release['tag']}-legal.zip", "legal payload filename is invalid")
    _require(portable_zip.name == f"Youtube-Downloaderbs-{release['tag']}.zip", "portable ZIP filename is invalid")
    payload_verifier.verify_release_legal_payload(
        control_root=control_root, portable_zip=portable_zip, legal_zip=legal_payload,
        release_notes=release_notes, tag=release["tag"], source_commit=source_commit,
        control_commit=control_commit,
    )
    authorization = release_authorization.load_authorization(control_root / release_authorization.AUTHORIZATION_PATH)
    contract = payload_verifier.load_asset_contract(control_root / payload_verifier.CONTRACT_PATH)
    authorized = release_authorization.verify_authorization(
        authorization, policy=policy, owner=owner, contract=contract, source_commit=source_commit,
        correspondence=ffmpeg_correspondence.load_record(control_root / ffmpeg_correspondence.RECORD_PATH),
    )
    return {
        "state": "AUTHORIZED_READY" if authorized else "TECHNICAL_READY",
        "technical_source_ready": True,
        "legal_release_authorized": authorized,
        "release_ready": authorized,
        "legal_compliance_certified": authorized,
        "source_availability_certified": True,
        "release_payload_integrated": True,
        "release_blockers": [] if authorized else ["legal-release-authorization-required"],
    }


def _verify_hygiene(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    _require(LOCAL_PATH_RE.search(serialized) is None, "policy contains a local absolute path")
    _require(not any(pattern.search(serialized) for pattern in SECRET_RES), "policy contains a secret-like value")
    folded = serialized.casefold()
    for forbidden in ("environment", "branch-allow", "user-allow", "username", "expiry", "expires", "timestamp", "mutable-ref"):
        _require(forbidden not in folded, f"policy contains forbidden override data: {forbidden}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseLegalGateError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
