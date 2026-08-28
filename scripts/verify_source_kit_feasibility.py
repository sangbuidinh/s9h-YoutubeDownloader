from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import audit_source_kit_feasibility as feasibility_audit
import verify_release_legal_gate as release_gate


EXPECTED_RELEASE_TAGS = (
    "v1.2.7-rc.1",
    "v1.3.0",
    "v1.3.0-rc.1",
    "v1.3.1",
    "v1.3.2",
)
REQUIRED_DOCUMENT_CONCEPTS = (
    "source input inventory is evidence-only",
    "unresolved does not mean absent",
    "identified does not mean complete",
    "no source kit was assembled",
    "release remains blocked",
    "existing releases are not retroactively certified",
)


class SourceKitFeasibilityVerificationError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the fail-closed source-kit feasibility inventory"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root",
    )
    args = parser.parse_args()
    try:
        verify_repository(args.root)
    except (
        SourceKitFeasibilityVerificationError,
        feasibility_audit.SourceKitFeasibilityError,
        release_gate.ReleaseLegalGateError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"source kit feasibility verification failed: {exc}", file=sys.stderr)
        return 1
    print("source kit feasibility verified")
    return 0


def verify_repository(root: Path) -> None:
    root = _regular_root(root)
    correspondence, requirements, inventory = feasibility_audit.load_inputs(
        root / "legal/source-correspondence.json",
        root / "legal/source-kit-requirements.json",
        root / "legal/source-input-inventory.json",
    )
    feasibility, feasibility_raw = feasibility_audit.load_feasibility(
        root / "legal/source-kit-feasibility.json",
        correspondence,
        requirements,
        inventory,
    )
    generated = feasibility_audit.generate_feasibility(
        correspondence, requirements, inventory
    )
    _require(feasibility == generated, "committed feasibility data is stale")
    _require(
        feasibility_raw == feasibility_audit.canonical_json_bytes(generated),
        "committed feasibility bytes differ from deterministic generation",
    )

    _verify_release_policy(root / "legal/release-policy.json")
    _verify_release_assets(root / "legal/release-assets-v2.json")
    _verify_component_inventory(root / "legal/components.json")
    _verify_documentation(root)


def _verify_release_policy(path: Path) -> None:
    policy = release_gate.load_policy(path)
    release_gate.validate_repository_control(path.parent.parent)
    _require(policy["policy_mode"] == "fail-closed", "release policy is not fail-closed")
    _require(
        policy["release_payload_integrated"] is False,
        "release payload must remain unintegrated",
    )
    releases = policy["releases"]
    _require(
        tuple(item["tag"] for item in releases) == EXPECTED_RELEASE_TAGS,
        "release policy must retain exactly five tracked releases",
    )
    _require(
        all(item["status"] == "blocked" for item in releases if item["tag"] != "v1.3.2"),
        "historical releases must remain blocked",
    )


def _verify_release_assets(path: Path) -> None:
    document = _load_canonical_json(path, "release asset contract")
    import verify_release_legal_payload
    verify_release_legal_payload.load_asset_contract(path)
    if document["release_readiness"] in {"technical-ready", "ready"}:
        release_gate.validate_repository_control(path.parent.parent)
        return
    _require(
        document.get("release_readiness") == "blocked",
        "release asset readiness must remain blocked",
    )
    for field in (
        "legal_compliance_certified",
        "source_availability_certified",
        "source_kits_ready",
    ):
        _require(document.get(field) is False, f"release asset {field} must remain false")
    templates = document.get("required_source_asset_templates")
    _require(isinstance(templates, list), "required source asset templates are missing")
    _require(
        [item.get("id") for item in templates] == ["aria2", "ffmpeg"],
        "required source asset templates changed",
    )
    _require(
        all(item.get("status") == "not-ready" for item in templates),
        "required source assets must remain not ready",
    )


def _verify_component_inventory(path: Path) -> None:
    document = _load_canonical_json(path, "component inventory")
    _require(
        document.get("project_license_status") == "not-selected",
        "project license must remain not selected",
    )
    _require(
        document.get("legal_compliance_certified") is False,
        "component inventory legal compliance must remain false",
    )


def _verify_documentation(root: Path) -> None:
    document_paths = (
        root / "README.md",
        root / "legal/README.md",
        root / "docs/source-kit-feasibility.md",
    )
    documents: dict[Path, str] = {}
    for path in document_paths:
        raw = _read_regular_file(path, "documentation")
        _require(not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} contains a UTF-8 BOM")
        text = raw.decode("utf-8")
        _verify_document_hygiene(text, path.name)
        documents[path] = text.casefold()

    combined = "\n".join(documents.values())
    for concept in REQUIRED_DOCUMENT_CONCEPTS:
        _require(concept in combined, f"documentation concept is missing: {concept}")

    legal_readme = documents[root / "legal/README.md"]
    for concept in (
        "phase 6b2b2a",
        "no source archive or source kit was created",
        "source kits remain not ready",
        "no release gate reconsideration",
        "no legal certification",
        "phase 6b2b2b is not authorized until blockers are resolved",
    ):
        _require(concept in legal_readme, f"legal README concept is missing: {concept}")

    detailed = documents[root / "docs/source-kit-feasibility.md"]
    for concept in (
        "evidence hierarchy",
        "provider evidence",
        "immutable upstream evidence",
        "core source",
        "full package source inputs",
        "pe imports",
        "toolchain gaps",
        "build-orchestration gaps",
        "phase 6b2b2b",
        "not legal advice",
    ):
        _require(concept in detailed, f"feasibility documentation concept is missing: {concept}")


def _load_canonical_json(path: Path, label: str) -> dict[str, Any]:
    raw = _read_regular_file(path, label)
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{label} contains a UTF-8 BOM")
    _require(b"\r" not in raw, f"{label} must use LF line endings")
    document = json.loads(
        raw.decode("utf-8"), object_pairs_hook=feasibility_audit._reject_duplicate_keys
    )
    _require(isinstance(document, dict), f"{label} root must be an object")
    _require(
        raw == feasibility_audit.canonical_json_bytes(document),
        f"{label} JSON is not canonical",
    )
    feasibility_audit._verify_hygiene(document, label)
    return document


def _verify_document_hygiene(text: str, label: str) -> None:
    _require(
        feasibility_audit.LOCAL_PATH_RE.search(text) is None,
        f"local absolute path in documentation {label}",
    )
    _require(
        feasibility_audit.TIMESTAMP_RE.search(text) is None,
        f"timestamp in documentation {label}",
    )
    _require(
        not any(pattern.search(text) for pattern in feasibility_audit.SECRET_RES),
        f"secret-like value in documentation {label}",
    )
    _require(
        not any(
            pattern.search(text) for pattern in feasibility_audit.UNSUPPORTED_CLAIM_RES
        ),
        f"unsupported readiness or compliance claim in documentation {label}",
    )


def _regular_root(root: Path) -> Path:
    candidate = feasibility_audit._lexical_absolute(Path(root))
    _require(candidate.is_dir(), "repository root is unavailable")
    _require(
        not feasibility_audit._path_or_existing_parent_is_reparse(candidate),
        "repository root uses a symlink or reparse point",
    )
    return candidate


def _read_regular_file(path: Path, label: str) -> bytes:
    try:
        return feasibility_audit._require_regular_input(path, label).read_bytes()
    except feasibility_audit.SourceKitFeasibilityError as exc:
        raise SourceKitFeasibilityVerificationError(str(exc)) from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceKitFeasibilityVerificationError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
