from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_aria2_primary_source_evidence as verifier


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = {
    "primary-evidence": ROOT / "legal/primary-source-evidence-aria2.json",
    "source-correspondence": ROOT / "legal/source-correspondence.json",
    "source-input-inventory": ROOT / "legal/source-input-inventory.json",
    "source-kit-feasibility": ROOT / "legal/source-kit-feasibility.json",
    "source-kit-requirements": ROOT / "legal/source-kit-requirements.json",
    "release-policy": ROOT / "legal/release-policy.json",
    "release-assets": ROOT / "legal/release-assets-v2.json",
    "readme": ROOT / "README.md",
    "legal-readme": ROOT / "legal/README.md",
    "feasibility-doc": ROOT / "docs/source-kit-feasibility.md",
}
JSON_KEYS = {
    "primary-evidence", "source-correspondence", "source-input-inventory",
    "source-kit-feasibility", "source-kit-requirements", "release-policy",
    "release-assets",
}


class Fixture:
    def __init__(self, root: Path) -> None:
        self.paths: dict[str, Path] = {}
        for index, (key, source) in enumerate(SOURCE_PATHS.items()):
            target = root / f"{index:02d}-{key}{source.suffix or '.txt'}"
            target.write_bytes(source.read_bytes())
            self.paths[key] = target
        self.tracked_paths: list[str] | None = None

    def json(self, key: str) -> dict[str, Any]:
        if key not in JSON_KEYS:
            raise AssertionError(f"not a JSON fixture: {key}")
        return json.loads(self.paths[key].read_text(encoding="utf-8-sig"))

    def write_json(self, key: str, value: dict[str, Any]) -> None:
        self.paths[key].write_bytes(verifier._canonical_json_bytes(value))

    def mutate_json(
        self, key: str, mutation: Callable[[dict[str, Any]], None]
    ) -> None:
        value = self.json(key)
        mutation(value)
        self.write_json(key, value)


def main() -> int:
    verifier.verify_repository(ROOT)
    _expect_ffmpeg_only_refinement_accepted()
    cases: list[tuple[str, Callable[[Fixture], None]]] = [
        ("missing component", lambda f: _primary(f, lambda d: d["components"].pop())),
        ("duplicate component", lambda f: _primary(f, lambda d: d["components"].insert(1, copy.deepcopy(d["components"][0])))),
        ("invented seventh component", _invent_component),
        ("changed provider version", lambda f: _component_mutation(f, "c-ares", lambda c: c.__setitem__("provider_version", "1.19.2"))),
        ("changed static linkage", lambda f: _component_mutation(f, "c-ares", lambda c: c.__setitem__("provider_linkage", "dynamic"))),
        ("changed aria2 binary hash", lambda f: _primary(f, lambda d: d.__setitem__("binary_package_sha256", "0" * 64))),
        ("changed aria2 core commit", _change_aria2_core_commit),
        ("changed aria2 inventory component version", lambda f: _inventory_component(f, "c-ares", lambda c: c.__setitem__("provider_version", "1.19.2"))),
        ("changed aria2 inventory resolution status", lambda f: _inventory_component(f, "c-ares", lambda c: c.__setitem__("resolution_status", "identified-version-only"))),
        ("mutable ref main", lambda f: _identity_value(f, "c-ares", "main")),
        ("mutable ref master", lambda f: _identity_value(f, "c-ares", "master")),
        ("mutable ref latest", lambda f: _identity_value(f, "c-ares", "latest")),
        ("malformed immutable identity", lambda f: _identity_value(f, "c-ares", "abc")),
        ("non-official repository", lambda f: _component_mutation(f, "c-ares", lambda c: c.__setitem__("official_repository", "https://example.invalid/c-ares"))),
        ("third-party mirror as sole authority", _third_party_mirror),
        ("search-result URL as evidence", lambda f: _evidence_locator(f, "c-ares", "https://www.google.com/search?q=c-ares")),
        ("source archive without SHA-256", lambda f: _archive_hash(f, "c-ares", "")),
        ("malformed archive SHA-256", lambda f: _archive_hash(f, "c-ares", "1234")),
        ("independently hashed false", lambda f: _component_mutation(f, "c-ares", lambda c: c["source_archive"].__setitem__("independently_hashed", False))),
        ("partial version match on verified record", lambda f: _component_mutation(f, "c-ares", lambda c: c.__setitem__("version_match", "partial"))),
        ("verified record with blocker", lambda f: _component_mutation(f, "c-ares", lambda c: c.__setitem__("blockers", ["unexpected blocker"]))),
        ("partial record without blocker", lambda f: _component_mutation(f, "gmp", lambda c: c.__setitem__("blockers", []))),
        ("promoted inventory without complete evidence", _promote_gmp_inventory),
        ("evidence inventory commit mismatch", lambda f: _inventory_component(f, "c-ares", lambda c: c.__setitem__("immutable_ref", "1" * 40))),
        ("evidence inventory archive mismatch", lambda f: _inventory_component(f, "c-ares", lambda c: c.__setitem__("source_archive_sha256", "1" * 64))),
        ("evidence feasibility count mismatch", lambda f: _primary(f, lambda d: d["summary"].__setitem__("verified_immutable_inputs", ["c-ares", "expat", "libssh2", "sqlite"]))),
        ("modified FFmpeg record", _modify_ffmpeg),
        ("FFmpeg-only mutation changes shared gate", _change_shared_inventory_gate),
        ("source kits ready", lambda f: _gate_flag(f, "source_kits_ready")),
        ("assembly authorized", lambda f: _gate_flag(f, "assembly_authorized")),
        ("release gate reconsideration allowed", lambda f: _gate_flag(f, "release_gate_reconsideration_allowed")),
        ("publishing allowed", lambda f: _gate_flag(f, "publishing_allowed")),
        ("legal compliance certified", lambda f: _gate_flag(f, "legal_compliance_certified")),
        ("source assets created", lambda f: _gate_flag(f, "source_assets_created")),
        ("source kit status ready", _source_kit_ready),
        ("unsupported Corresponding Source completeness claim", _unsupported_claim),
        ("local Windows path", lambda f: _inject_claim(f, chr(67) + ":" + "\\" + "Users" + "\\" + "example" + "\\" + "source")),
        ("local Unix path", lambda f: _inject_claim(f, "/" + "tmp" + "/source")),
        ("timestamp", lambda f: _inject_claim(f, "2026" + "-07-14T12:34")),
        ("API-key-like secret", lambda f: _inject_claim(f, "AI" + "za" + "A" * 35)),
        ("token-like secret", lambda f: _inject_claim(f, "gh" + "p_" + "A" * 30)),
        ("signed googlevideo URL", lambda f: _inject_claim(f, "https://rr1---sn.example.google" + "video.com/videoplayback?sig=value")),
        ("malformed JSON", _malformed_json),
        ("UTF-8 BOM", _utf8_bom),
        ("CRLF", _crlf),
        ("non-canonical field order", _noncanonical_order),
        ("unsorted evidence", _unsorted_evidence),
        ("source archive tracked in Git", _tracked_archive),
        ("SQLite without official identity mapping", _sqlite_without_mapping),
        ("GMP unofficial mirror only", _gmp_unofficial_only),
        ("stale generated feasibility bytes", _stale_feasibility),
    ]
    if len(cases) != 51:
        raise AssertionError(f"expected 51 negative cases, found {len(cases)}")
    for name, mutation in cases:
        _expect_rejected(name, mutation)
    print("aria2 primary-source evidence smoke tests passed")
    return 0


def _expect_ffmpeg_only_refinement_accepted() -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-aria2-boundary-smoke-") as raw:
        fixture = Fixture(Path(raw))
        document = fixture.json("source-input-inventory")
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        component = next(
            item for item in ffmpeg["external_components"] if item["id"] == "libass"
        )
        structural = {
            key: copy.deepcopy(component[key])
            for key in (
                "provider_version",
                "version_status",
                "upstream_repository",
                "immutable_ref",
                "source_archive_sha256",
                "resolution_status",
            )
        }
        expected_structural = {
            "provider_version": "unresolved",
            "version_status": "unresolved",
            "upstream_repository": "unresolved",
            "immutable_ref": "unresolved",
            "source_archive_sha256": "unresolved",
            "resolution_status": "identified-name-only",
        }
        if structural != expected_structural:
            raise AssertionError(
                "positive FFmpeg fixture is not unresolved/name-only"
            )
        component["evidence"].append(
            {
                "kind": "official-upstream-research",
                "authority": "libass project",
                "locator": "https://github.com/libass/libass",
                "claim": "Official upstream identity was researched contextually without claiming a provider version or provider-selected input.",
                "status": "partial",
            }
        )
        component["evidence"] = sorted(
            component["evidence"],
            key=lambda record: tuple(record[key] for key in verifier.EVIDENCE_KEYS),
        )
        component["blockers"] = sorted(
            set(
                component["blockers"]
                + [
                    "Exact provider-to-upstream source mapping remains unresolved after contextual research."
                ]
            )
        )
        fixture.write_json("source-input-inventory", document)
        _require_structural_state(component, structural)
        verifier.verify_repository(ROOT, overrides=fixture.paths)


def _require_structural_state(
    component: dict[str, Any], expected: dict[str, Any]
) -> None:
    actual = {key: component[key] for key in expected}
    if actual != expected:
        raise AssertionError("positive FFmpeg refinement changed structural state")


def _expect_rejected(name: str, mutation: Callable[[Fixture], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-aria2-evidence-smoke-") as raw:
        fixture = Fixture(Path(raw))
        mutation(fixture)
        try:
            verifier.verify_repository(
                ROOT, overrides=fixture.paths, tracked_paths=fixture.tracked_paths
            )
        except (
            verifier.Aria2PrimarySourceEvidenceError,
            verifier.feasibility_audit.SourceKitFeasibilityError,
            OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError,
        ):
            return
    raise AssertionError(f"mutation was accepted: {name}")


def _primary(fixture: Fixture, mutation: Callable[[dict[str, Any]], None]) -> None:
    fixture.mutate_json("primary-evidence", mutation)


def _component(document: dict[str, Any], component_id: str) -> dict[str, Any]:
    return next(item for item in document["components"] if item["id"] == component_id)


def _component_mutation(
    fixture: Fixture, component_id: str,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    _primary(fixture, lambda document: mutation(_component(document, component_id)))


def _identity_value(fixture: Fixture, component_id: str, value: str) -> None:
    _component_mutation(fixture, component_id, lambda c: c["release_identity"].__setitem__("value", value))


def _archive_hash(fixture: Fixture, component_id: str, value: str) -> None:
    _component_mutation(fixture, component_id, lambda c: c["source_archive"].__setitem__("sha256", value))


def _evidence_locator(fixture: Fixture, component_id: str, value: str) -> None:
    _component_mutation(fixture, component_id, lambda c: c["evidence"][0].__setitem__("locator", value))


def _inventory_component(
    fixture: Fixture, component_id: str,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    def apply(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        mutation(next(item for item in aria2["external_components"] if item["id"] == component_id))
    fixture.mutate_json("source-input-inventory", apply)


def _invent_component(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        invented = copy.deepcopy(document["components"][-1])
        invented["id"] = "invented"
        document["components"].append(invented)
    _primary(fixture, mutate)


def _third_party_mirror(fixture: Fixture) -> None:
    def mutate(component: dict[str, Any]) -> None:
        component["official_repository"] = "https://github.com/example/c-ares"
        for record in component["evidence"]:
            if record["locator"].startswith("https://"):
                record["locator"] = "https://github.com/example/c-ares"
    _component_mutation(fixture, "c-ares", mutate)


def _promote_gmp_inventory(fixture: Fixture) -> None:
    def mutate(record: dict[str, Any]) -> None:
        record["version_status"] = "verified"
        record["upstream_repository"] = "https://gmplib.org/repo/gmp/"
        record["immutable_ref"] = "1" * 40
        record["source_archive_sha256"] = verifier.ARCHIVES["gmp"][2]
        record["resolution_status"] = "verified-immutable-input"
        record["blockers"] = []
    _inventory_component(fixture, "gmp", mutate)


def _modify_ffmpeg(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        ffmpeg["package_status"] = "ready"
    fixture.mutate_json("source-input-inventory", mutate)


def _change_shared_inventory_gate(fixture: Fixture) -> None:
    fixture.mutate_json(
        "source-input-inventory",
        lambda document: document.__setitem__(
            "release_gate_reconsideration_allowed", True
        ),
    )


def _change_aria2_core_commit(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        aria2["core_source"]["commit"] = "1" * 40

    fixture.mutate_json("source-input-inventory", mutate)


def _gate_flag(fixture: Fixture, field: str) -> None:
    _primary(fixture, lambda document: document["gate_state"].__setitem__(field, True))


def _source_kit_ready(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        aria2["source_kit_status"] = "ready"
    fixture.mutate_json("source-kit-feasibility", mutate)


def _unsupported_claim(fixture: Fixture) -> None:
    path = fixture.paths["feasibility-doc"]
    path.write_text(path.read_text(encoding="utf-8") + "\nCorresponding Source is complete.\n", encoding="utf-8", newline="\n")


def _inject_claim(fixture: Fixture, value: str) -> None:
    _component_mutation(fixture, "c-ares", lambda c: c["evidence"][0].__setitem__("claim", value))


def _malformed_json(fixture: Fixture) -> None:
    fixture.paths["primary-evidence"].write_bytes(b"{")


def _utf8_bom(fixture: Fixture) -> None:
    path = fixture.paths["primary-evidence"]
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _crlf(fixture: Fixture) -> None:
    path = fixture.paths["primary-evidence"]
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _noncanonical_order(fixture: Fixture) -> None:
    document = fixture.json("primary-evidence")
    reordered = {
        "schema_version": document["schema_version"],
        "baseline_commit": document["baseline_commit"],
        "target_phase": document["target_phase"],
    }
    reordered.update((key, value) for key, value in document.items() if key not in reordered)
    fixture.write_json("primary-evidence", reordered)


def _unsorted_evidence(fixture: Fixture) -> None:
    def mutate(component: dict[str, Any]) -> None:
        component["evidence"][0], component["evidence"][1] = component["evidence"][1], component["evidence"][0]
    _component_mutation(fixture, "c-ares", mutate)


def _tracked_archive(fixture: Fixture) -> None:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE)
    fixture.tracked_paths = result.stdout.splitlines() + ["evidence/source.tar.xz"]


def _sqlite_without_mapping(fixture: Fixture) -> None:
    def mutate(component: dict[str, Any]) -> None:
        component["release_identity"]["secondary_identity"] = "not-applicable"
        component["evidence"] = [item for item in component["evidence"] if item["kind"] != "official-mirror-mapping"]
    _component_mutation(fixture, "sqlite", mutate)


def _gmp_unofficial_only(fixture: Fixture) -> None:
    def mutate(component: dict[str, Any]) -> None:
        component["official_repository"] = "https://github.com/example/gmp"
        component["release_identity"].update({"kind": "git-commit", "value": "1" * 40, "secondary_identity": "not-applicable"})
        component["version_match"] = "exact"
        component["resolution_status"] = "verified-immutable-input"
        component["blockers"] = []
        component["evidence"] = [
            {"kind": "mirror-release", "authority": "unofficial mirror", "locator": "https://github.com/example/gmp", "claim": "Unofficial mirror claim.", "status": "verified"},
            {"kind": "mirror-source", "authority": "unofficial mirror", "locator": "https://github.com/example/gmp", "claim": "Unofficial mirror source claim.", "status": "verified"},
        ]
    _component_mutation(fixture, "gmp", mutate)


def _stale_feasibility(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        aria2["blockers"] = sorted(set(aria2["blockers"] + ["Stale generated record."]))
    fixture.mutate_json("source-kit-feasibility", mutate)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
