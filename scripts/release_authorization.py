"""Explicit reviewed release authorization, independent of technical source state."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTHORIZATION_PATH = "legal/release-authorization-v1.3.2.json"
KEYS = (
    "schema_version", "release_tag", "state", "decision_reference",
    "reviewed_source_commit", "reviewed_policy_sha256",
    "reviewed_source_owner_sha256", "reviewed_asset_contract_sha256",
    "reviewed_ffmpeg_correspondence_sha256",
)


class AuthorizationError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def document_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_authorization(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorizationError("release authorization is not valid UTF-8 JSON") from exc
    validate_authorization(value)
    if raw != canonical_bytes(value):
        raise AuthorizationError("release authorization is not canonical UTF-8 LF JSON")
    return value


def validate_authorization(value: Any) -> None:
    if not isinstance(value, dict) or tuple(value) != KEYS:
        raise AuthorizationError("release authorization fields are invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise AuthorizationError("release authorization schema is invalid")
    if value["release_tag"] != "v1.3.2":
        raise AuthorizationError("release authorization tag is invalid")
    if value["state"] == "LEGAL_REVIEW_REQUIRED":
        if any(value[key] is not None for key in KEYS[3:]):
            raise AuthorizationError("unreviewed authorization contains an approval claim")
        return
    if value["state"] != "LEGAL_RELEASE_AUTHORIZED":
        raise AuthorizationError("release authorization state is invalid")
    reference = value["decision_reference"]
    if not isinstance(reference, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,119}", reference):
        raise AuthorizationError("explicit external-review decision reference is required")
    commit = value["reviewed_source_commit"]
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit) or commit == "0" * 40:
        raise AuthorizationError("reviewed release source commit is required")
    for key in KEYS[5:]:
        digest = value[key]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest == "0" * 64:
            raise AuthorizationError(f"reviewed evidence digest is required: {key}")


def verify_authorization(
    value: dict[str, Any], *, policy: dict[str, Any], owner: dict[str, Any],
    contract: dict[str, Any], correspondence: dict[str, Any], source_commit: str | None = None,
) -> bool:
    validate_authorization(value)
    if value["state"] == "LEGAL_REVIEW_REQUIRED":
        return False
    for field, document in (
        ("reviewed_policy_sha256", policy),
        ("reviewed_source_owner_sha256", owner),
        ("reviewed_asset_contract_sha256", contract),
        ("reviewed_ffmpeg_correspondence_sha256", correspondence),
    ):
        if value[field] != document_sha256(document):
            raise AuthorizationError(f"release authorization evidence mismatch: {field}")
    if source_commit is not None and value["reviewed_source_commit"] != source_commit:
        raise AuthorizationError("release authorization source commit mismatch")
    return True
