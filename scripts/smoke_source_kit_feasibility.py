from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import audit_source_kit_feasibility as feasibility_audit
import verify_source_kit_feasibility as feasibility_verifier


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_FILES = (
    "legal/source-correspondence.json",
    "legal/source-kit-requirements.json",
    "legal/source-input-inventory.json",
    "legal/source-kit-feasibility.json",
    "legal/release-policy.json",
    "legal/release-assets-v2.json",
    "legal/components.json",
    "legal/source-compliance-v1.3.2.json",
    "legal/release-authorization-v1.3.2.json",
    "legal/ffmpeg-correspondence-v1.3.2.json",
    "scripts/build_project_ffmpeg.py",
    "scripts/project_ffmpeg.py",
    "README.md",
    "legal/README.md",
    "docs/source-kit-feasibility.md",
)


class SmokeFailure(AssertionError):
    pass


def main() -> int:
    try:
        run_smoke_tests()
    except (SmokeFailure, AssertionError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"source kit feasibility smoke test failed: {exc}", file=sys.stderr)
        return 1
    print("source kit feasibility smoke tests passed")
    return 0


def run_smoke_tests() -> None:
    feasibility_verifier.verify_repository(ROOT)
    correspondence, requirements, inventory = feasibility_audit.load_inputs(
        ROOT / "legal/source-correspondence.json",
        ROOT / "legal/source-kit-requirements.json",
        ROOT / "legal/source-input-inventory.json",
    )
    feasibility, feasibility_raw = feasibility_audit.load_feasibility(
        ROOT / "legal/source-kit-feasibility.json",
        correspondence,
        requirements,
        inventory,
    )
    generated_one = feasibility_audit.canonical_json_bytes(
        feasibility_audit.generate_feasibility(correspondence, requirements, inventory)
    )
    generated_two = feasibility_audit.canonical_json_bytes(
        feasibility_audit.generate_feasibility(correspondence, requirements, inventory)
    )
    _require(
        generated_one == generated_two == feasibility_raw,
        "deterministic generation differs from committed feasibility bytes",
    )

    def reject_inventory(label: str, mutate: Callable[[dict[str, Any]], None]) -> None:
        candidate = copy.deepcopy(inventory)
        mutate(candidate)
        _expect_rejection(
            label,
            lambda: feasibility_audit.validate_inventory_document(
                candidate, correspondence, requirements
            ),
        )

    def reject_feasibility(label: str, mutate: Callable[[dict[str, Any]], None]) -> None:
        candidate = copy.deepcopy(feasibility)
        mutate(candidate)
        _expect_rejection(
            label,
            lambda: feasibility_audit.validate_feasibility_document(
                candidate, correspondence, requirements, inventory
            ),
        )

    def find_external_component(
        value: dict[str, Any],
        predicate: Callable[[dict[str, Any]], bool],
        label: str,
    ) -> dict[str, Any]:
        for package in value["packages"]:
            for component in package["external_components"]:
                if predicate(component):
                    return component
        raise SmokeFailure(f"no external component matches {label}")

    reject_inventory(
        "legal compliance true",
        lambda value: value.__setitem__("legal_compliance_certified", True),
    )
    reject_inventory(
        "release gate reconsideration true",
        lambda value: value.__setitem__("release_gate_reconsideration_allowed", True),
    )
    reject_inventory(
        "source assets created true",
        lambda value: value.__setitem__("source_assets_created", True),
    )
    reject_feasibility(
        "source kits ready true",
        lambda value: value.__setitem__("source_kits_ready", True),
    )
    reject_feasibility(
        "assembly authorized true",
        lambda value: value.__setitem__("assembly_authorized", True),
    )
    reject_feasibility(
        "source kit status ready",
        lambda value: value["packages"][0].__setitem__("source_kit_status", "ready"),
    )
    reject_inventory(
        "package status changed",
        lambda value: value["packages"][0].__setitem__("package_status", "partial"),
    )
    reject_inventory(
        "missing aria2 component",
        lambda value: value["packages"][0]["external_components"].pop(),
    )
    reject_inventory(
        "missing FFmpeg component",
        lambda value: value["packages"][1]["external_components"].pop(),
    )
    reject_inventory(
        "duplicate component",
        lambda value: value["packages"][0]["external_components"].append(
            copy.deepcopy(value["packages"][0]["external_components"][-1])
        ),
    )

    def invent_component(value: dict[str, Any]) -> None:
        invented = copy.deepcopy(value["packages"][0]["external_components"][-1])
        invented["id"] = "invented"
        value["packages"][0]["external_components"].append(invented)

    reject_inventory("invented component", invent_component)
    reject_inventory(
        "changed binary package hash",
        lambda value: value["packages"][0].__setitem__(
            "binary_package_sha256", "0" * 64
        ),
    )
    reject_inventory(
        "changed linkage",
        lambda value: value["packages"][0]["external_components"][0].__setitem__(
            "linkage", "system"
        ),
    )

    def guess_version(value: dict[str, Any]) -> None:
        component = value["packages"][1]["external_components"][0]
        component["provider_version"] = "1.0.0"
        component["version_status"] = "provider-identified"

    reject_inventory("guessed version", guess_version)
    reject_inventory(
        "mutable ref main",
        lambda value: value["packages"][0]["external_components"][0].__setitem__(
            "immutable_ref", "main"
        ),
    )
    reject_inventory(
        "mutable ref latest",
        lambda value: value["packages"][0]["build_orchestration"].__setitem__(
            "immutable_ref", "latest"
        ),
    )

    def malformed_commit(value: dict[str, Any]) -> None:
        component = value["packages"][0]["external_components"][0]
        component["version_status"] = "verified"
        component["upstream_repository"] = "c-ares/c-ares"
        component["immutable_ref"] = "not-a-commit"
        component["evidence"][0]["status"] = "verified"
        component["resolution_status"] = "verified-immutable-input"
        component["blockers"] = []

    reject_inventory("malformed commit", malformed_commit)
    def missing_blocker(value: dict[str, Any]) -> None:
        component = find_external_component(
            value,
            lambda item: item["resolution_status"] != "verified-immutable-input",
            "a non-verified resolution state",
        )
        _require(component["blockers"], "non-verified component has no blockers")
        component["blockers"] = []

    reject_inventory("missing blocker", missing_blocker)

    def retained_verified_blocker(value: dict[str, Any]) -> None:
        component = find_external_component(
            value,
            lambda item: item["resolution_status"] == "verified-immutable-input",
            "a verified immutable input",
        )
        _require(component["blockers"] == [], "verified component already has blockers")
        component["blockers"] = sorted(
            ["Synthetic blocker retained for verified input."]
        )

    reject_inventory("blocker retained for verified input", retained_verified_blocker)
    reject_inventory(
        "local Windows path",
        lambda value: value["packages"][0]["core_source"]["evidence"][0].__setitem__(
            "locator", "C:" + "\\private\\source"
        ),
    )
    reject_inventory(
        "local Unix path",
        lambda value: value["packages"][0]["core_source"]["evidence"][0].__setitem__(
            "locator", "/" + "home/private/source"
        ),
    )
    reject_inventory(
        "timestamp",
        lambda value: value["packages"][0]["core_source"]["evidence"][0].__setitem__(
            "claim", "Observed at " + "2026-" + "07-14 " + "12:00"
        ),
    )
    reject_inventory(
        "secret-like token",
        lambda value: value["packages"][0]["core_source"]["evidence"][0].__setitem__(
            "claim", "ghp_" + "A" * 36
        ),
    )
    reject_inventory(
        "signed media URL",
        lambda value: value["packages"][0]["core_source"]["evidence"][0].__setitem__(
            "locator",
            "https://" + "r1.google" + "video.com/" + "videoplayback?" + "sig=private",
        ),
    )

    with tempfile.TemporaryDirectory(prefix="source-kit-feasibility-smoke-") as temp:
        fixture_root = Path(temp) / "fixture"
        _copy_fixture(fixture_root)
        inventory_path = fixture_root / "legal/source-input-inventory.json"

        inventory_path.write_bytes(b'{"schema_version": 1,')
        _expect_rejection(
            "malformed JSON", lambda: feasibility_verifier.verify_repository(fixture_root)
        )
        _copy_one("legal/source-input-inventory.json", fixture_root)

        inventory_path.write_bytes(b"\xef\xbb\xbf" + inventory_path.read_bytes())
        _expect_rejection(
            "UTF-8 BOM", lambda: feasibility_verifier.verify_repository(fixture_root)
        )
        _copy_one("legal/source-input-inventory.json", fixture_root)

        inventory_path.write_bytes(inventory_path.read_bytes().replace(b"\n", b"\r\n"))
        _expect_rejection(
            "CRLF", lambda: feasibility_verifier.verify_repository(fixture_root)
        )
        _copy_one("legal/source-input-inventory.json", fixture_root)

        reordered = copy.deepcopy(inventory)
        reordered = {
            "target_phase": reordered["target_phase"],
            "schema_version": reordered["schema_version"],
            **{key: value for key, value in reordered.items() if key not in {"target_phase", "schema_version"}},
        }
        inventory_path.write_bytes(feasibility_audit.canonical_json_bytes(reordered))
        _expect_rejection(
            "non-canonical field order",
            lambda: feasibility_verifier.verify_repository(fixture_root),
        )
        _copy_one("legal/source-input-inventory.json", fixture_root)

        feasibility_path = fixture_root / "legal/source-kit-feasibility.json"
        stale = copy.deepcopy(feasibility)
        stale["overall_status"] = "blocked-stale"
        feasibility_path.write_bytes(feasibility_audit.canonical_json_bytes(stale))
        _expect_rejection(
            "stale feasibility", lambda: feasibility_verifier.verify_repository(fixture_root)
        )
        _copy_one("legal/source-kit-feasibility.json", fixture_root)

        mismatched = copy.deepcopy(feasibility)
        mismatched["packages"][0]["total_external_components"] += 1
        feasibility_path.write_bytes(feasibility_audit.canonical_json_bytes(mismatched))
        _expect_rejection(
            "mismatched generated counts",
            lambda: feasibility_verifier.verify_repository(fixture_root),
        )
        _copy_one("legal/source-kit-feasibility.json", fixture_root)

        requirement_path = fixture_root / "legal/source-kit-requirements.json"
        changed_requirements = copy.deepcopy(requirements)
        changed_requirements["kits"][0]["required_source_items"].remove(
            "external-source:c-ares"
        )
        requirement_path.write_bytes(
            feasibility_audit.canonical_json_bytes(changed_requirements)
        )
        _expect_rejection(
            "missing required source item",
            lambda: feasibility_verifier.verify_repository(fixture_root),
        )
        _copy_one("legal/source-kit-requirements.json", fixture_root)

        readme_path = fixture_root / "README.md"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8") + "\nRelease is ready.\n",
            encoding="utf-8",
            newline="\n",
        )
        _expect_rejection(
            "unsupported documentation claim",
            lambda: feasibility_verifier.verify_repository(fixture_root),
        )


def _copy_fixture(target_root: Path) -> None:
    for relative in FIXTURE_FILES:
        _copy_one(relative, target_root)


def _copy_one(relative: str, target_root: Path) -> None:
    source = ROOT / relative
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _expect_rejection(label: str, operation: Callable[[], Any]) -> None:
    try:
        operation()
    except (
        feasibility_audit.SourceKitFeasibilityError,
        feasibility_verifier.SourceKitFeasibilityVerificationError,
        AssertionError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        return
    raise SmokeFailure(f"negative case was accepted: {label}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
