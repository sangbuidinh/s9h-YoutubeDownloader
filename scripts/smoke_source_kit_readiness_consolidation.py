from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_source_kit_readiness_consolidation as verifier


ROOT = Path(__file__).resolve().parents[1]
PHASE_PATHS = (
    "README.md", "docs/source-kit-feasibility.md", "legal/README.md",
    "legal/source-kit-readiness-consolidation.json",
    "scripts/smoke_source_kit_readiness_consolidation.py",
    "scripts/verify_source_kit_readiness_consolidation.py",
)
EXPECTED_ERRORS = (
    verifier.SourceKitReadinessConsolidationError, OSError, UnicodeError,
    json.JSONDecodeError, subprocess.SubprocessError,
)


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="s9h-readiness-smoke-")
        self.base = Path(self.temp.name)
        self.paths: dict[str, Path] = {}
        for key, relative in verifier.PATHS.items():
            target = self.base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
            self.paths[key] = target
        self.tracked_paths = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, check=True, text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        self.repository_files = [
            path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*")
            if path.is_file() and ".git" not in path.parts
            and "__pycache__" not in path.parts and path.name not in verifier.OLD_REPORTS
        ]
        self.introduced_paths = list(PHASE_PATHS)
        feasibility = self.paths["source-kit-feasibility"].read_bytes()
        self.feasibility_runner: verifier.FeasibilityRunner = lambda _root, _paths: feasibility

    def close(self) -> None:
        self.temp.cleanup()

    def mutate_json(self, key: str, mutation: Callable[[dict[str, Any]], None]) -> None:
        document = json.loads(self.paths[key].read_text(encoding="utf-8"))
        mutation(document)
        self.paths[key].write_bytes(verifier._canonical_json_bytes(document))

    def append_doc(self, text: str) -> None:
        with self.paths["feasibility-doc"].open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n{text}\n")

    def verify(self) -> None:
        verifier.verify_repository(
            ROOT, overrides=self.paths, tracked_paths=self.tracked_paths,
            repository_files=self.repository_files,
            introduced_paths=self.introduced_paths,
            feasibility_runner=self.feasibility_runner,
        )


Mutation = Callable[[Fixture], None]


def _consolidation(mutation: Callable[[dict[str, Any]], None]) -> Mutation:
    return lambda fixture: fixture.mutate_json("consolidation", mutation)


def _scope(field: str, value: Any) -> Mutation:
    return _consolidation(lambda document: document["evidence_scope"].__setitem__(field, value))


def _package(document: dict[str, Any], package_id: str) -> dict[str, Any]:
    return next(item for item in document["packages"] if item["package_id"] == package_id)


def _package_field(package_id: str, field: str, value: Any) -> Mutation:
    return _consolidation(lambda document: _package(document, package_id).__setitem__(field, value))


def _disposition(document: dict[str, Any], code: str) -> dict[str, Any]:
    return next(item for item in document["input_disposition"] if item["disposition_code"] == code)


def _material_package(document: dict[str, Any], package_id: str) -> dict[str, Any]:
    return next(item for item in document["package_material_disposition"] if item["package_id"] == package_id)


def _material_field(package_id: str, material: str, field: str, value: Any) -> Mutation:
    def mutate(document: dict[str, Any]) -> None:
        owner = _material_package(document, package_id)
        target = next(item for item in owner["materials"] if item["material"] == material)
        target[field] = copy.deepcopy(value)
    return _consolidation(mutate)


def _remove_item(code: str, identity: tuple[str, str]) -> Mutation:
    def mutate(document: dict[str, Any]) -> None:
        owner = _disposition(document, code)
        owner["items"] = [
            item for item in owner["items"]
            if (item["package_id"], item["component_id"]) != identity
        ]
    return _consolidation(mutate)


def _append_item(code: str, identity: tuple[str, str]) -> Mutation:
    return _consolidation(lambda document: _disposition(document, code)["items"].append({
        "package_id": identity[0], "component_id": identity[1],
    }))


def _move_item(source: str, target: str, identity: tuple[str, str]) -> Mutation:
    def mutate(document: dict[str, Any]) -> None:
        source_record = _disposition(document, source)
        source_record["items"] = [
            item for item in source_record["items"]
            if (item["package_id"], item["component_id"]) != identity
        ]
        _disposition(document, target)["items"].append({
            "package_id": identity[0], "component_id": identity[1],
        })
    return _consolidation(mutate)


def _gate(field: str) -> Mutation:
    return _consolidation(lambda document: document["gate_state"].__setitem__(field, True))


def _protected(key: str) -> Mutation:
    return lambda fixture: fixture.mutate_json(
        key, lambda document: document.__setitem__("mutation_probe", True)
    )


def _introduced(path: str) -> Mutation:
    return lambda fixture: fixture.introduced_paths.append(path)


def _doc(text: str) -> Mutation:
    return lambda fixture: fixture.append_doc(text)


def _malformed(fixture: Fixture) -> None:
    fixture.paths["consolidation"].write_bytes(b"{\n")


def _duplicate_key(fixture: Fixture) -> None:
    path = fixture.paths["consolidation"]
    path.write_bytes(path.read_bytes().replace(b"{\n", b'{\n  "schema_version": 1,\n', 1))


def _bom(fixture: Fixture) -> None:
    path = fixture.paths["consolidation"]
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _crlf(fixture: Fixture) -> None:
    path = fixture.paths["consolidation"]
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _wrong_field_order(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        value = document.pop("schema_version")
        document["schema_version"] = value
    fixture.mutate_json("consolidation", mutate)


def _inventory_semantic_bytes(fixture: Fixture) -> None:
    path = fixture.paths["source-input-inventory"]
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_bytes((json.dumps(document, ensure_ascii=False, indent=4) + "\n").encode("utf-8"))


def _cases() -> list[tuple[str, Mutation]]:
    cases: list[tuple[str, Mutation]] = [
        ("wrong phase", _consolidation(lambda d: d.__setitem__("target_phase", "6B2B2A4"))),
        ("wrong baseline", _consolidation(lambda d: d.__setitem__("baseline_commit", "0" * 40))),
        ("network access allowed", _scope("network_access_allowed", True)),
        ("new primary research allowed", _scope("new_primary_research_allowed", True)),
        ("source download allowed", _scope("source_downloads_allowed", True)),
        ("binary download allowed", _scope("binary_downloads_allowed", True)),
        ("source asset creation allowed", _scope("source_asset_creation_allowed", True)),
        ("build execution allowed", _scope("build_execution_allowed", True)),
        ("missing authoritative input", _consolidation(lambda d: d["authoritative_inputs"].pop())),
        ("invented authoritative input", _consolidation(lambda d: d["authoritative_inputs"].append({"path": "legal/invented.json", "role": "invented", "sha256": "0" * 64, "status": "accepted-input"}))),
        ("wrong authoritative hash", _consolidation(lambda d: d["authoritative_inputs"][0].__setitem__("sha256", "0" * 64))),
        ("wrong package order", _consolidation(lambda d: d["packages"].reverse())),
        ("missing aria2 package", _consolidation(lambda d: d.__setitem__("packages", [p for p in d["packages"] if p["package_id"] != "aria2"]))),
        ("missing FFmpeg package", _consolidation(lambda d: d.__setitem__("packages", [p for p in d["packages"] if p["package_id"] != "ffmpeg"]))),
        ("invented third package", _consolidation(lambda d: d["packages"].append({**copy.deepcopy(d["packages"][0]), "package_id": "third"}))),
        ("wrong aria2 total", _package_field("aria2", "external_components_total", 7)),
        ("wrong FFmpeg total", _package_field("ffmpeg", "external_components_total", 54)),
        ("wrong overall total", _consolidation(lambda d: d["cross_package_summary"].__setitem__("external_components_total", 60))),
        ("wrong verified count", _consolidation(lambda d: d["cross_package_summary"].__setitem__("verified_immutable_inputs", 4))),
        ("wrong partial count", _consolidation(lambda d: d["cross_package_summary"].__setitem__("partial_inputs", 2))),
        ("wrong name-only count", _consolidation(lambda d: d["cross_package_summary"].__setitem__("identified_name_only_inputs", 49))),
        ("wrong system-candidate count", _consolidation(lambda d: d["cross_package_summary"].__setitem__("system_component_candidates", 4))),
        ("missing verified aria2 component", _remove_item("verified-immutable-input", ("aria2", "c-ares"))),
        ("invented verified aria2 component", _append_item("verified-immutable-input", ("aria2", "invented"))),
        ("aria2 gmp promoted to verified", _move_item("partial-immutable-input", "verified-immutable-input", ("aria2", "gmp"))),
        ("aria2 gmp omitted", _remove_item("partial-immutable-input", ("aria2", "gmp"))),
        ("FFmpeg static component promoted", _move_item("provider-version-unresolved", "verified-immutable-input", ("ffmpeg", "amf"))),
        ("FFmpeg static component omitted", _remove_item("provider-version-unresolved", ("ffmpeg", "amf"))),
        ("FFmpeg system component promoted", _move_item("system-interface-review-required", "verified-immutable-input", ("ffmpeg", "d3d11va"))),
        ("FFmpeg system component omitted", _remove_item("system-interface-review-required", ("ffmpeg", "d3d11va"))),
        ("duplicate disposition item", _consolidation(lambda d: _disposition(d, "verified-immutable-input")["items"].append(copy.deepcopy(_disposition(d, "verified-immutable-input")["items"][0])))),
        ("input in two dispositions", _append_item("verified-immutable-input", ("aria2", "gmp"))),
        ("input in no disposition", _remove_item("provider-version-unresolved", ("ffmpeg", "zlib"))),
        ("global ID confuses aria2 gmp and FFmpeg gmp", _consolidation(lambda d: _disposition(d, "partial-immutable-input")["items"][0].__setitem__("package_id", "ffmpeg"))),
        ("global ID confuses aria2 zlib and FFmpeg zlib", _consolidation(lambda d: next(i for i in _disposition(d, "verified-immutable-input")["items"] if i["component_id"] == "zlib").__setitem__("package_id", "ffmpeg"))),
        ("confuses aria2 libssh2 and FFmpeg libssh", _consolidation(lambda d: next(i for i in _disposition(d, "verified-immutable-input")["items"] if i["component_id"] == "libssh2").update({"package_id": "ffmpeg", "component_id": "libssh"}))),
        ("wrong disposition order", _consolidation(lambda d: d["input_disposition"].reverse())),
        ("wrong item order", _consolidation(lambda d: _disposition(d, "provider-version-unresolved")["items"].reverse())),
        ("duplicate blocker", _consolidation(lambda d: _package(d, "aria2")["blockers"].append(_package(d, "aria2")["blockers"][0]))),
        ("unsorted blocker", _consolidation(lambda d: _package(d, "aria2")["blockers"].reverse())),
        ("aria2 provider versions complete", _package_field("aria2", "provider_versions_complete", True)),
        ("aria2 immutable inputs complete", _package_field("aria2", "immutable_component_inputs_complete", True)),
        ("aria2 toolchain complete", _package_field("aria2", "toolchain_complete", True)),
        ("aria2 orchestration complete", _package_field("aria2", "build_orchestration_complete", True)),
        ("aria2 source kit ready", _package_field("aria2", "source_kit_ready", True)),
        ("aria2 assembly eligible", _package_field("aria2", "assembly_eligible", True)),
        ("FFmpeg provider versions complete", _package_field("ffmpeg", "provider_versions_complete", True)),
        ("FFmpeg immutable inputs complete", _package_field("ffmpeg", "immutable_component_inputs_complete", True)),
        ("FFmpeg toolchain complete", _package_field("ffmpeg", "toolchain_complete", True)),
        ("FFmpeg configure complete", _package_field("ffmpeg", "configure_complete", True)),
        ("FFmpeg orchestration complete", _package_field("ffmpeg", "build_orchestration_complete", True)),
        ("FFmpeg patch evidence complete", _package_field("ffmpeg", "patch_evidence_complete", True)),
        ("FFmpeg reproducibility complete", _package_field("ffmpeg", "reproducibility_complete", True)),
        ("FFmpeg source kit ready", _package_field("ffmpeg", "source_kit_ready", True)),
        ("FFmpeg assembly eligible", _package_field("ffmpeg", "assembly_eligible", True)),
        ("component coverage treated as source kit completeness", _package_field("ffmpeg", "source_kit_ready", True)),
        ("package status changed", _package_field("aria2", "package_status", "ready")),
        ("wrong material order", _consolidation(lambda d: _material_package(d, "aria2")["materials"].reverse())),
        ("material status promoted", _material_field("aria2", "toolchain", "status", "verified")),
        ("source asset creation started", _material_field("aria2", "source-asset-creation", "status", "partial")),
        ("legal release review complete", _material_field("aria2", "legal-release-review", "status", "verified")),
        ("assembly decision proceed", _consolidation(lambda d: d["assembly_decision"].__setitem__("decision", "proceed"))),
        ("source asset assembly authorized", _consolidation(lambda d: d["assembly_decision"].__setitem__("source_asset_assembly_authorized", True))),
        ("source kit assembly authorized", _consolidation(lambda d: d["assembly_decision"].__setitem__("source_kit_assembly_authorized", True))),
        ("package authorized", _consolidation(lambda d: d["assembly_decision"].__setitem__("packages_authorized", ["aria2"]))),
        ("blocked package removed", _consolidation(lambda d: d["assembly_decision"].__setitem__("packages_blocked", ["ffmpeg"]))),
        ("release gate reconsideration enabled", _gate("release_gate_reconsideration_allowed")),
        ("release ready enabled", _gate("release_ready")),
        ("publishing enabled", _gate("publishing_allowed")),
        ("legal compliance certified", _gate("legal_compliance_certified")),
        ("source availability certified", _gate("source_availability_certified")),
        ("source assets created", _gate("source_assets_created")),
        ("source kits ready", _gate("source_kits_ready")),
        ("missing prerequisite", _consolidation(lambda d: d["reconsideration_prerequisites"].pop())),
        ("completed prerequisite", _consolidation(lambda d: d["reconsideration_prerequisites"][0].__setitem__("status", "complete"))),
        ("prerequisite no longer blocks", _consolidation(lambda d: d["reconsideration_prerequisites"][0].__setitem__("blocks_assembly", False))),
        ("source archive introduced", _introduced("review/source.tar.xz")),
        ("source kit archive introduced", _introduced("review/source-kit.zip")),
        ("binary introduced", _introduced("review/tool.exe")),
        ("installer introduced", _introduced("review/sdk.msi")),
        ("media introduced", _introduced("review/sample.mp4")),
        ("source tree introduced", _introduced("source-kit/ffmpeg/COPYING")),
        ("inventory changed", _protected("source-input-inventory")),
        ("inventory semantic bytes changed", _inventory_semantic_bytes),
        ("feasibility changed", _protected("source-kit-feasibility")),
        ("aria2 evidence changed", _protected("aria2-primary-evidence")),
        ("FFmpeg codec evidence changed", _protected("codec-primary-evidence")),
        ("FFmpeg support evidence changed", _protected("support-primary-evidence")),
        ("FFmpeg hardware evidence changed", _protected("hardware-system-primary-evidence")),
        ("FFmpeg remaining evidence changed", _protected("remaining-primary-evidence")),
        ("FFmpeg build feasibility changed", _protected("ffmpeg-build-feasibility")),
        ("release policy changed", _protected("release-policy")),
        ("release assets changed", _protected("release-assets")),
        ("unsupported legal sufficiency", _doc("Legal compliance is sufficient.")),
        ("unsupported Corresponding Source completeness", _doc("Corresponding Source is complete.")),
        ("unsupported release approval", _doc("Release is approved.")),
        ("unsupported retrospective certification", _doc("Existing releases are certified.")),
        ("malformed JSON", _malformed),
        ("duplicate JSON key", _duplicate_key),
        ("UTF-8 BOM", _bom),
        ("CRLF", _crlf),
        ("wrong field order", _wrong_field_order),
        ("unsorted authoritative inputs", _consolidation(lambda d: d["authoritative_inputs"].reverse())),
        ("duplicate authoritative input", _consolidation(lambda d: d["authoritative_inputs"].append(copy.deepcopy(d["authoritative_inputs"][0])))),
        ("local Windows path", _scope("claims_boundary", "C:\\Users\\example\\evidence.json")),
        ("local Unix path", _scope("claims_boundary", "/home/example/evidence.json")),
        ("timestamp", _scope("claims_boundary", "Consolidated 2026-07-15T12:34")),
        ("HTTP locator", _consolidation(lambda d: d["authoritative_inputs"][0].__setitem__("path", "http://example.com/evidence.json"))),
        ("API key", _scope("claims_boundary", "AIza" + "A" * 32)),
        ("GitHub token", _scope("claims_boundary", "ghp_" + "A" * 24)),
        ("signed googlevideo URL", _scope("claims_boundary", "https://r1.googlevideo.com/videoplayback?sig=secret")),
    ]
    if len(cases) != 111:
        raise AssertionError(f"mutation case count changed: {len(cases)}")
    return cases


def _positive_future_boundary() -> None:
    fixture = Fixture()
    try:
        future = fixture.base / "legal/future-evidence-recovery.json"
        future.parent.mkdir(parents=True, exist_ok=True)
        future.write_bytes(verifier._canonical_json_bytes({
            "future_phase": "separately-authorized",
            "current_gates_changed": False,
        }))
        fixture.repository_files.append("legal/future-evidence-recovery.json")
        fixture.verify()
    finally:
        fixture.close()


def main() -> int:
    verifier.verify_repository(ROOT)
    passed = 0
    for label, mutation in _cases():
        fixture = Fixture()
        try:
            mutation(fixture)
            try:
                fixture.verify()
            except EXPECTED_ERRORS:
                passed += 1
            else:
                raise AssertionError(f"mutation unexpectedly passed: {label}")
        finally:
            fixture.close()
    if passed != 111:
        raise AssertionError(f"mutation rejection count changed: {passed}")
    _positive_future_boundary()
    print("Source-kit readiness consolidation smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
