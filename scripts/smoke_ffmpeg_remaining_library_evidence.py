from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_ffmpeg_remaining_library_evidence as verifier


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = {
    "primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-remaining-libraries.json",
    "codec-primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-codecs.json",
    "support-primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-support.json",
    "hardware-system-primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-hardware-system.json",
    "aria2-primary-evidence": ROOT / "legal/primary-source-evidence-aria2.json",
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
JSON_KEYS = set(SOURCE_PATHS) - {"readme", "legal-readme", "feasibility-doc"}


class Fixture:
    def __init__(self, root: Path) -> None:
        self.paths: dict[str, Path] = {}
        for index, (key, source) in enumerate(SOURCE_PATHS.items()):
            target = root / f"{index:02d}-{key}{source.suffix or '.txt'}"
            target.write_bytes(source.read_bytes())
            self.paths[key] = target
        self.tracked_paths: list[str] | None = None
        self.repository_files: list[str] | None = None
        self.introduced_paths: list[str] | None = None
        self.feasibility_runner: verifier.FeasibilityRunner | None = None

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
    _positive_future_boundary()
    cases: list[tuple[str, Callable[[Fixture], None]]] = [
        ("missing component", lambda f: _primary(f, lambda d: d["components"].pop())),
        ("duplicate component", lambda f: _primary(f, lambda d: d["components"].insert(1, copy.deepcopy(d["components"][0])))),
        ("invented twelfth component", _invent_component),
        ("wrong component order", lambda f: _primary(f, lambda d: d["components"].reverse())),
        ("changed linkage", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_linkage", "system"))),
        ("invalid functional classification", lambda f: _component_mutation(f, lambda c: c.__setitem__("component_nature", "invented-class"))),
        ("missing provider-label interpretation", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_label_interpretation", ""))),
        ("invented provider version", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_version", "1.0"))),
        ("provider version status promoted", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_version_status", "verified"))),
        ("inventory upstream repository populated", lambda f: _inventory_component(f, "avisynth", lambda c: c.__setitem__("upstream_repository", verifier.LOCATORS["avisynth"]))),
        ("inventory immutable ref populated", lambda f: _inventory_component(f, "avisynth", lambda c: c.__setitem__("immutable_ref", "1" * 40))),
        ("inventory archive hash populated", lambda f: _inventory_component(f, "avisynth", lambda c: c.__setitem__("source_archive_sha256", "1" * 64))),
        ("resolution status promoted", lambda f: _component_mutation(f, lambda c: c.__setitem__("resolution_status", "partially-verified"))),
        ("upstream release represented as provider input", lambda f: _identity(f, "avisynth", "upstream-release", "3.7.5")),
        ("provider-to-official mapping promoted", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_to_official_mapping", "verified"))),
        ("archive SHA-256 inserted", lambda f: _archive(f, "avisynth", "sha256", "1" * 64)),
        ("archive independently hashed", lambda f: _archive(f, "avisynth", "independently_hashed", True)),
        ("missing official upstream evidence", lambda f: _remove_evidence_kind(f, "official-upstream-research")),
        ("missing licensing context", lambda f: _remove_evidence_kind(f, "licensing-context")),
        ("missing provider metadata evidence", lambda f: _remove_evidence_kind(f, "provider-package-metadata")),
        ("missing blocker", lambda f: _component_mutation(f, lambda c: c["blockers"].pop())),
        ("unsorted blocker", lambda f: _component_mutation(f, lambda c: c["blockers"].reverse())),
        ("duplicate blocker", lambda f: _component_mutation(f, lambda c: c["blockers"].append(c["blockers"][0]))),
        ("unsorted evidence", lambda f: _component_mutation(f, lambda c: c["evidence"].reverse())),
        ("duplicate evidence", lambda f: _component_mutation(f, lambda c: c["evidence"].append(copy.deepcopy(c["evidence"][0])))),
        ("wrong total count", lambda f: _summary(f, "total_components", 12)),
        ("non-zero provider-version count", lambda f: _summary(f, "provider_versions_verified", 1)),
        ("non-zero verified-input count", lambda f: _summary(f, "verified_immutable_inputs", 1)),
        ("non-zero archive-hash count", lambda f: _summary(f, "archive_hashes_verified", 1)),
        ("exact provider recipe set true", lambda f: _summary(f, "exact_provider_recipe_found", True)),
        ("all batch inputs resolved set true", lambda f: _summary(f, "all_batch_inputs_resolved", True)),
        ("assembly authorized true", lambda f: _summary(f, "source_kit_assembly_authorized", True)),
        ("source kits ready true", lambda f: _gate(f, "source_kits_ready")),
        ("publishing allowed true", lambda f: _gate(f, "publishing_allowed")),
        ("legal compliance certified true", lambda f: _gate(f, "legal_compliance_certified")),
        ("source availability certified true", lambda f: _gate(f, "source_availability_certified")),
        ("source assets created true", lambda f: _gate(f, "source_assets_created")),
        ("release-gate reconsideration true", lambda f: _gate(f, "release_gate_reconsideration_allowed")),
        ("FFmpeg source-kit status ready", _ffmpeg_source_ready),
        ("changed prior codec evidence", lambda f: _modify_prior_primary(f, "codec-primary-evidence")),
        ("changed prior support evidence", lambda f: _modify_prior_primary(f, "support-primary-evidence")),
        ("changed prior hardware/system evidence", lambda f: _modify_prior_primary(f, "hardware-system-primary-evidence")),
        ("changed prior codec inventory", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("provider_version", "invented"))),
        ("changed prior support inventory", lambda f: _inventory_component(f, "libass", lambda c: c.__setitem__("provider_version", "invented"))),
        ("changed prior hardware/system inventory", lambda f: _inventory_component(f, "amf", lambda c: c.__setitem__("provider_version", "invented"))),
        ("changed aria2 evidence", _modify_aria2_primary),
        ("changed unrelated aria2 inventory", _modify_aria2_inventory),
        ("changed unrelated FFmpeg structural record", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("resolution_status", "verified"))),
        ("FFmpeg GMP assigned aria2 GMP version", lambda f: _copy_aria2_field(f, "gmp", "gmp", "provider_version")),
        ("FFmpeg GMP assigned aria2 GMP repository", lambda f: _copy_aria2_field(f, "gmp", "gmp", "upstream_repository")),
        ("FFmpeg GMP assigned aria2 GMP immutable ref", lambda f: _copy_aria2_field(f, "gmp", "gmp", "immutable_ref")),
        ("FFmpeg GMP assigned aria2 GMP archive hash", lambda f: _copy_aria2_field(f, "gmp", "gmp", "source_archive_sha256")),
        ("FFmpeg zlib assigned aria2 zlib version", lambda f: _copy_aria2_field(f, "zlib", "zlib", "provider_version")),
        ("FFmpeg zlib assigned aria2 zlib repository", lambda f: _copy_aria2_field(f, "zlib", "zlib", "upstream_repository")),
        ("FFmpeg zlib assigned aria2 zlib immutable ref", lambda f: _copy_aria2_field(f, "zlib", "zlib", "immutable_ref")),
        ("FFmpeg zlib assigned aria2 zlib archive hash", lambda f: _copy_aria2_field(f, "zlib", "zlib", "source_archive_sha256")),
        ("FFmpeg libssh assigned aria2 libssh2 evidence", _copy_libssh2_evidence),
        ("global component-ID lookup confuses package scopes", lambda f: _global_component_confusion(f, "gmp")),
        ("coverage total not equal to 55", lambda f: _coverage(f, "ffmpeg_external_components_total", 54)),
        ("component-level coverage set false", lambda f: _coverage(f, "component_level_coverage_complete", False)),
        ("provider versions complete set true", lambda f: _coverage(f, "provider_versions_complete", True)),
        ("toolchain complete set true", lambda f: _coverage(f, "toolchain_complete", True)),
        ("build orchestration complete set true", lambda f: _coverage(f, "build_orchestration_complete", True)),
        ("source kit complete set true", lambda f: _coverage(f, "source_kit_complete", True)),
        ("component coverage described as source-kit completion", lambda f: _inject_doc_claim(f, "Component coverage means source kit is complete.")),
        ("stale feasibility bytes", _stale_feasibility),
        ("non-deterministic feasibility output", _non_deterministic_feasibility),
        ("unsupported Corresponding Source claim", lambda f: _inject_doc_claim(f, "Corresponding Source is complete.")),
        ("unsupported reproducible-build claim", lambda f: _inject_doc_claim(f, "The build is reproducible.")),
        ("unsupported provider-recipe-complete claim", lambda f: _inject_doc_claim(f, "The exact provider recipe is complete.")),
        ("malformed JSON", lambda f: f.paths["primary-evidence"].write_bytes(b"{")),
        ("duplicate JSON key", _duplicate_key),
        ("UTF-8 BOM", _bom),
        ("CRLF", _crlf),
        ("wrong field order", _wrong_order),
        ("HTTP locator", lambda f: _component_mutation(f, lambda c: c.__setitem__("official_repository_or_documentation", "http://zlib.net/"), "zlib")),
        ("unofficial mirror as sole authority", lambda f: _component_mutation(f, lambda c: c.__setitem__("official_repository_or_documentation", "https://example.invalid/zlib"), "zlib")),
        ("search-result URL", lambda f: _component_mutation(f, lambda c: c.__setitem__("official_repository_or_documentation", "https://www.google.com/search"), "zlib")),
        ("local Windows path", lambda f: _inject_claim(f, "C:" + "\\" + "Users" + "\\" + "example")),
        ("local Unix path", lambda f: _inject_claim(f, "/" + "tmp" + "/source")),
        ("timestamp", lambda f: _inject_claim(f, "2026" + "-07-15T12:34")),
        ("API-key-like secret", lambda f: _inject_claim(f, "AIza" + "A" * 35)),
        ("GitHub-token-like secret", lambda f: _inject_claim(f, "ghp_" + "A" * 30)),
        ("signed googlevideo URL", lambda f: _inject_claim(f, "https://x.google" + "video.com/videoplayback?sig=value")),
        ("tracked archive", _tracked_archive),
        ("archive inside repository", _repository_archive),
        ("binary introduced", lambda f: _introduced(f, "evidence/new.exe")),
        ("media introduced", lambda f: _introduced(f, "evidence/video.mp4")),
    ]
    if len(cases) != 88:
        raise AssertionError(f"expected 88 negative cases, found {len(cases)}")
    for name, mutation in cases:
        _expect_rejected(name, mutation)
    print("FFmpeg remaining-library evidence smoke tests passed")
    return 0


def _expect_rejected(name: str, mutation: Callable[[Fixture], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-remaining-smoke-") as raw:
        fixture = Fixture(Path(raw))
        mutation(fixture)
        try:
            verifier.verify_repository(
                ROOT,
                overrides=fixture.paths,
                tracked_paths=fixture.tracked_paths,
                repository_files=fixture.repository_files,
                introduced_paths=fixture.introduced_paths,
                feasibility_runner=fixture.feasibility_runner,
            )
        except (
            verifier.FFmpegRemainingLibraryEvidenceError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ):
            return
    raise AssertionError(f"mutation was accepted: {name}")


def _positive_future_boundary() -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-remaining-boundary-") as raw:
        fixture = Fixture(Path(raw))

        def refine(document: dict[str, Any]) -> None:
            ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
            toolchain = ffmpeg["toolchain"]
            structural = {
                key: copy.deepcopy(value)
                for key, value in toolchain.items()
                if key not in {"evidence", "blockers"}
            }
            toolchain["evidence"].append({
                "kind": "official-upstream-research",
                "authority": "Python Software Foundation",
                "locator": "https://www.python.org/",
                "claim": "A future toolchain evidence fixture was refined without changing component records or shared gates.",
                "status": "partial",
            })
            toolchain["blockers"] = sorted(set(toolchain["blockers"] + [
                "Exact future toolchain evidence remains unresolved.",
            ]))
            current = {
                key: value
                for key, value in toolchain.items()
                if key not in {"evidence", "blockers"}
            }
            if current != structural:
                raise AssertionError("future-boundary fixture changed structural state")

        fixture.mutate_json("source-input-inventory", refine)
        feasibility = fixture.paths["source-kit-feasibility"].read_bytes()
        fixture.feasibility_runner = lambda _root, _paths: feasibility
        verifier.verify_repository(
            ROOT,
            overrides=fixture.paths,
            feasibility_runner=fixture.feasibility_runner,
        )


def _primary(fixture: Fixture, mutation: Callable[[dict[str, Any]], None]) -> None:
    fixture.mutate_json("primary-evidence", mutation)


def _component(document: dict[str, Any], cid: str = "avisynth") -> dict[str, Any]:
    return next(item for item in document["components"] if item["id"] == cid)


def _component_mutation(
    fixture: Fixture,
    mutation: Callable[[dict[str, Any]], None],
    cid: str = "avisynth",
) -> None:
    _primary(fixture, lambda document: mutation(_component(document, cid)))


def _inventory_component(
    fixture: Fixture, cid: str, mutation: Callable[[dict[str, Any]], None]
) -> None:
    def apply(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        mutation(next(item for item in ffmpeg["external_components"] if item["id"] == cid))

    fixture.mutate_json("source-input-inventory", apply)


def _aria2_component(document: dict[str, Any], cid: str) -> dict[str, Any]:
    aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
    return next(item for item in aria2["external_components"] if item["id"] == cid)


def _invent_component(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        item = copy.deepcopy(document["components"][-1])
        item["id"] = "invented-remaining-component"
        document["components"].append(item)

    _primary(fixture, mutate)


def _identity(fixture: Fixture, cid: str, kind: str, value: str) -> None:
    def mutate(component: dict[str, Any]) -> None:
        component["release_identity"]["kind"] = kind
        component["release_identity"]["value"] = value

    _component_mutation(fixture, mutate, cid)


def _archive(fixture: Fixture, cid: str, key: str, value: Any) -> None:
    _component_mutation(
        fixture, lambda component: component["source_archive"].__setitem__(key, value), cid
    )


def _remove_evidence_kind(fixture: Fixture, kind: str) -> None:
    _component_mutation(
        fixture,
        lambda component: component.__setitem__(
            "evidence", [item for item in component["evidence"] if item["kind"] != kind]
        ),
    )


def _summary(fixture: Fixture, field: str, value: Any) -> None:
    _primary(fixture, lambda document: document["summary"].__setitem__(field, value))


def _coverage(fixture: Fixture, field: str, value: Any) -> None:
    _primary(
        fixture, lambda document: document["coverage_completion"].__setitem__(field, value)
    )


def _gate(fixture: Fixture, field: str) -> None:
    _primary(fixture, lambda document: document["gate_state"].__setitem__(field, True))


def _ffmpeg_source_ready(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        ffmpeg["source_kit_status"] = "ready"

    fixture.mutate_json("source-kit-feasibility", mutate)


def _modify_prior_primary(fixture: Fixture, key: str) -> None:
    fixture.mutate_json(key, lambda document: document["summary"].__setitem__("identified_name_only", 999))


def _modify_aria2_primary(fixture: Fixture) -> None:
    fixture.mutate_json(
        "aria2-primary-evidence",
        lambda document: document["summary"].__setitem__("source_kit_assembly_authorized", True),
    )


def _modify_aria2_inventory(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        aria2["core_source"]["commit"] = "1" * 40

    fixture.mutate_json("source-input-inventory", mutate)


def _copy_aria2_field(
    fixture: Fixture, ffmpeg_id: str, aria2_id: str, field: str
) -> None:
    document = fixture.json("source-input-inventory")
    value = copy.deepcopy(_aria2_component(document, aria2_id)[field])
    if aria2_id == "gmp" and field != "provider_version":
        primary = fixture.json("aria2-primary-evidence")
        component = next(item for item in primary["components"] if item["id"] == "gmp")
        if field == "upstream_repository":
            value = component["official_repository"]
        elif field == "source_archive_sha256":
            value = component["source_archive"]["sha256"]
        elif field == "immutable_ref":
            value = "aria2/gmp:immutable-ref-unresolved"
    ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
    target = next(item for item in ffmpeg["external_components"] if item["id"] == ffmpeg_id)
    target[field] = value
    fixture.write_json("source-input-inventory", document)


def _copy_libssh2_evidence(fixture: Fixture) -> None:
    document = fixture.json("source-input-inventory")
    evidence = copy.deepcopy(_aria2_component(document, "libssh2")["evidence"])
    ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
    target = next(item for item in ffmpeg["external_components"] if item["id"] == "libssh")
    target["evidence"] = evidence
    fixture.write_json("source-input-inventory", document)


def _global_component_confusion(fixture: Fixture, cid: str) -> None:
    document = fixture.json("source-input-inventory")
    value = _aria2_component(document, cid)["provider_version"]
    _component_mutation(fixture, lambda component: component.__setitem__("provider_version", value), cid)


def _stale_feasibility(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        ffmpeg["verified_immutable_inputs"] = ["avisynth"]

    fixture.mutate_json("source-kit-feasibility", mutate)


def _non_deterministic_feasibility(fixture: Fixture) -> None:
    current = fixture.paths["source-kit-feasibility"].read_bytes()
    calls = 0

    def run(_root: Path, _paths: dict[str, Path]) -> bytes:
        nonlocal calls
        calls += 1
        return current if calls == 1 else current + b" "

    fixture.feasibility_runner = run


def _inject_doc_claim(fixture: Fixture, value: str) -> None:
    path = fixture.paths["feasibility-doc"]
    path.write_text(
        path.read_text(encoding="utf-8") + "\n" + value + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _inject_claim(fixture: Fixture, value: str) -> None:
    _component_mutation(
        fixture, lambda component: component["evidence"][0].__setitem__("claim", value)
    )


def _bom(fixture: Fixture) -> None:
    path = fixture.paths["primary-evidence"]
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _crlf(fixture: Fixture) -> None:
    path = fixture.paths["primary-evidence"]
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _wrong_order(fixture: Fixture) -> None:
    document = fixture.json("primary-evidence")
    reordered = {
        "schema_version": document["schema_version"],
        "baseline_commit": document["baseline_commit"],
        "target_phase": document["target_phase"],
    }
    reordered.update((key, value) for key, value in document.items() if key not in reordered)
    fixture.write_json("primary-evidence", reordered)


def _duplicate_key(fixture: Fixture) -> None:
    path = fixture.paths["primary-evidence"]
    path.write_bytes(
        path.read_bytes().replace(
            b'{\n  "schema_version": 1,',
            b'{\n  "schema_version": 1,\n  "schema_version": 1,',
            1,
        )
    )


def _tracked_archive(fixture: Fixture) -> None:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE
    )
    fixture.tracked_paths = result.stdout.splitlines() + ["evidence/source.tar.xz"]


def _repository_archive(fixture: Fixture) -> None:
    fixture.repository_files = ["evidence/source.tar.xz"]


def _introduced(fixture: Fixture, path: str) -> None:
    fixture.introduced_paths = [path]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
