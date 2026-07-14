from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


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
    args = parser.parse_args()
    try:
        policy = load_policy(args.policy)
        release = release_for_tag(policy, args.tag)
    except (ReleaseLegalGateError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"Release legal gate error: {exc}", file=sys.stderr)
        return 2
    reasons = ", ".join(release["reason_codes"])
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
    _require(policy["legal_compliance_certified"] is False, "legal compliance must remain uncertified")
    _require(policy["source_availability_certified"] is False, "source availability must remain uncertified")
    _require(policy["release_payload_integrated"] is False, "release payload must remain unintegrated")

    releases = policy["releases"]
    _require(isinstance(releases, list) and len(releases) == len(EXPECTED_TAGS), "policy must contain exactly four releases")
    tags: list[str] = []
    for release in releases:
        _require(isinstance(release, dict) and tuple(release) == RELEASE_KEYS, "release policy record schema is invalid")
        tag = release["tag"]
        _require(isinstance(tag, str), "release tag is invalid")
        _require(release["status"] == "blocked", f"release status must be blocked: {tag}")
        reasons = release["reason_codes"]
        _require(isinstance(reasons, list) and reasons, f"release reasons are missing: {tag}")
        _require(all(isinstance(reason, str) and re.fullmatch(r"[a-z0-9-]+", reason) for reason in reasons), f"release reason is invalid: {tag}")
        _require(len(reasons) == len(set(reasons)), f"release reasons are duplicated: {tag}")
        _require(reasons == sorted(reasons), f"release reasons are not sorted: {tag}")
        _require(release["required_phase"] == "6B2", f"required phase is invalid: {tag}")
        tags.append(tag)

    _require(len(tags) == len(set(tags)), "release tags must be unique")
    _require(tuple(tags) == EXPECTED_TAGS, "release tags are missing or unsorted")
    for release in releases:
        expected = EXPECTED_REASONS[release["tag"]]
        _require(tuple(release["reason_codes"]) == expected, f"release reasons are incomplete: {release['tag']}")
    _verify_hygiene(policy)
    return policy


def release_for_tag(policy: dict[str, Any], tag: str) -> dict[str, Any]:
    validate_policy_document(policy)
    _require(tag in EXPECTED_TAGS, "requested tag is not an exact current workflow tag")
    matches = [release for release in policy["releases"] if release["tag"] == tag]
    _require(len(matches) == 1, "requested tag is missing or duplicated")
    return matches[0]


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
