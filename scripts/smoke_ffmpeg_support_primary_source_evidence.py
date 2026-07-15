from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_ffmpeg_support_primary_source_evidence as verifier


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = {
    "primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-support.json",
    "codec-primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-codecs.json",
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
        ("invented fifteenth component", _invent_component),
        ("wrong component order", lambda f: _primary(f, lambda d: d["components"].reverse())),
        ("changed linkage", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_linkage", "dynamic"))),
        ("invented provider version", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_version", "1.18.4"))),
        ("provider version status promoted", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_version_status", "verified"))),
        ("inventory upstream repository populated", lambda f: _inventory_component(f, "cairo", lambda c: c.__setitem__("upstream_repository", verifier.REPOSITORIES["cairo"]))),
        ("inventory immutable ref populated", lambda f: _inventory_component(f, "cairo", lambda c: c.__setitem__("immutable_ref", "1" * 40))),
        ("inventory archive hash populated", lambda f: _inventory_component(f, "cairo", lambda c: c.__setitem__("source_archive_sha256", "1" * 64))),
        ("resolution status promoted", lambda f: _component_mutation(f, lambda c: c.__setitem__("resolution_status", "verified-immutable-input"))),
        ("upstream release represented as provider input", _claim_provider_use),
        ("provider mapping promoted", lambda f: _component_mutation(f, lambda c: c.__setitem__("provider_to_upstream_match", "exact"))),
        ("archive independently hashed", lambda f: _archive(f, "independently_hashed", True)),
        ("archive SHA-256 inserted", lambda f: _archive(f, "sha256", "1" * 64)),
        ("verified component with unresolved version", lambda f: _component_mutation(f, lambda c: c.__setitem__("resolution_status", "verified-immutable-input"))),
        ("missing component evidence", lambda f: _component_mutation(f, lambda c: c.__setitem__("evidence", []))),
        ("missing provider metadata evidence", lambda f: _remove_evidence_kind(f, "provider-package-metadata")),
        ("missing upstream contextual evidence", lambda f: _remove_evidence_kind(f, "official-repository-research")),
        ("missing license context", lambda f: _component_mutation(f, lambda c: c["license_evidence"].__setitem__("status", "unresolved"))),
        ("missing blocker", lambda f: _component_mutation(f, lambda c: c.__setitem__("blockers", []))),
        ("unsorted blocker", lambda f: _component_mutation(f, lambda c: c["blockers"].reverse())),
        ("summary provider version count non-zero", lambda f: _summary(f, "provider_versions_verified", 1)),
        ("summary verified count non-zero", lambda f: _summary(f, "verified_immutable_inputs", 1)),
        ("summary archive count non-zero", lambda f: _summary(f, "archive_hashes_verified", 1)),
        ("summary name-only count wrong", lambda f: _summary(f, "identified_name_only", 13)),
        ("exact provider recipe true", lambda f: _build_flag(f, "exact_historical_recipe_identified")),
        ("assembly authorization true", lambda f: _summary(f, "source_kit_assembly_authorized", True)),
        ("source kits ready true", lambda f: _gate(f, "source_kits_ready")),
        ("publishing allowed true", lambda f: _gate(f, "publishing_allowed")),
        ("legal compliance certified true", lambda f: _gate(f, "legal_compliance_certified")),
        ("source availability certified true", lambda f: _gate(f, "source_availability_certified")),
        ("source assets created true", lambda f: _gate(f, "source_assets_created")),
        ("release gate reconsideration true", lambda f: _gate(f, "release_gate_reconsideration_allowed")),
        ("FFmpeg source-kit status ready", _ffmpeg_source_ready),
        ("changed prior codec evidence JSON", _modify_codec_primary),
        ("changed prior codec inventory record", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("provider_version", "invented"))),
        ("changed aria2 evidence JSON", _modify_aria2_primary),
        ("changed aria2 inventory", _modify_aria2_inventory),
        ("changed non-batch FFmpeg structural record", lambda f: _inventory_component(f, "gnutls", lambda c: c.__setitem__("provider_version", "invented"))),
        ("stale feasibility bytes", _stale_feasibility),
        ("non-deterministic feasibility output", _non_deterministic_feasibility),
        ("unsupported Corresponding Source claim", lambda f: _inject_doc_claim(f, "Corresponding Source is complete.")),
        ("unsupported reproducible-build claim", lambda f: _inject_doc_claim(f, "The build is reproducible.")),
        ("unsupported provider-recipe-complete claim", lambda f: _inject_doc_claim(f, "The provider recipe is complete.")),
        ("malformed JSON", lambda f: f.paths["primary-evidence"].write_bytes(b"{")),
        ("duplicate JSON key", _duplicate_key),
        ("UTF-8 BOM", _bom),
        ("CRLF", _crlf),
        ("wrong field order", _wrong_order),
        ("unsorted evidence", lambda f: _component_mutation(f, lambda c: c["evidence"].reverse())),
        ("duplicate evidence", _duplicate_evidence),
        ("HTTP locator", lambda f: _component_mutation(f, lambda c: c.__setitem__("official_repository", "http://gitlab.freedesktop.org/cairo/cairo"))),
        ("unofficial mirror as sole authority", lambda f: _component_mutation(f, lambda c: c.__setitem__("official_repository", "https://example.invalid/cairo"))),
        ("search-result URL", lambda f: _component_mutation(f, lambda c: c.__setitem__("official_repository", "https://www.google.com/search"))),
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
    if len(cases) != 65:
        raise AssertionError(f"expected 65 negative cases, found {len(cases)}")
    for name, mutation in cases:
        _expect_rejected(name, mutation)
    print("FFmpeg support primary-source evidence smoke tests passed")
    return 0


def _expect_rejected(name: str, mutation: Callable[[Fixture], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-support-smoke-") as raw:
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
            verifier.FFmpegSupportPrimarySourceEvidenceError,
            OSError, UnicodeError, json.JSONDecodeError,
            subprocess.SubprocessError,
        ):
            return
    raise AssertionError(f"mutation was accepted: {name}")


def _positive_future_boundary() -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-support-boundary-") as raw:
        fixture = Fixture(Path(raw))

        def refine(component: dict[str, Any]) -> None:
            structural = {
                key: copy.deepcopy(component[key]) for key in (
                    "linkage", "provider_version", "version_status",
                    "upstream_repository", "immutable_ref", "source_archive_sha256",
                    "resolution_status",
                )
            }
            component["evidence"].append({
                "kind": "official-upstream-research",
                "authority": "GnuTLS",
                "locator": "https://www.gnutls.org/",
                "claim": (
                    "Official upstream identity for gnutls was researched without "
                    "selecting a provider input."
                ),
                "status": "partial",
            })
            component["evidence"].sort(key=lambda item: tuple(item.values()))
            component["blockers"] = sorted([
                "Exact immutable upstream source ref is unresolved.",
                "Exact provider component version is unresolved.",
                "Independent source archive SHA-256 is unresolved.",
            ])
            after = {key: component[key] for key in structural}
            if after != structural:
                raise AssertionError("future-boundary fixture changed structural state")

        _inventory_component(fixture, "gnutls", refine)
        verifier.verify_repository(ROOT, overrides=fixture.paths)


def _primary(fixture: Fixture, mutation: Callable[[dict[str, Any]], None]) -> None:
    fixture.mutate_json("primary-evidence", mutation)


def _component(document: dict[str, Any], cid: str = "cairo") -> dict[str, Any]:
    return next(item for item in document["components"] if item["id"] == cid)


def _component_mutation(
    fixture: Fixture, mutation: Callable[[dict[str, Any]], None], cid: str = "cairo"
) -> None:
    _primary(fixture, lambda document: mutation(_component(document, cid)))


def _archive(fixture: Fixture, key: str, value: Any) -> None:
    _component_mutation(
        fixture, lambda component: component["source_archive"].__setitem__(key, value)
    )


def _inventory_component(
    fixture: Fixture, cid: str, mutation: Callable[[dict[str, Any]], None]
) -> None:
    def apply(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        mutation(next(item for item in ffmpeg["external_components"] if item["id"] == cid))

    fixture.mutate_json("source-input-inventory", apply)


def _invent_component(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        item = copy.deepcopy(document["components"][-1])
        item["id"] = "invented-support-library"
        document["components"].append(item)

    _primary(fixture, mutate)


def _claim_provider_use(fixture: Fixture) -> None:
    def mutate(component: dict[str, Any]) -> None:
        record = next(
            item for item in component["evidence"]
            if item["kind"] == "official-repository-research"
        )
        record["claim"] = "The latest upstream release was selected by Gyan."

    _component_mutation(fixture, mutate)


def _remove_evidence_kind(fixture: Fixture, kind: str) -> None:
    _component_mutation(
        fixture,
        lambda component: component.__setitem__(
            "evidence", [item for item in component["evidence"] if item["kind"] != kind]
        ),
    )


def _build_flag(fixture: Fixture, field: str) -> None:
    _primary(
        fixture,
        lambda document: document["provider_build_evidence"].__setitem__(field, True),
    )


def _summary(fixture: Fixture, field: str, value: Any) -> None:
    _primary(fixture, lambda document: document["summary"].__setitem__(field, value))


def _gate(fixture: Fixture, field: str) -> None:
    _primary(fixture, lambda document: document["gate_state"].__setitem__(field, True))


def _ffmpeg_source_ready(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        ffmpeg["source_kit_status"] = "ready"

    fixture.mutate_json("source-kit-feasibility", mutate)


def _modify_codec_primary(fixture: Fixture) -> None:
    fixture.mutate_json(
        "codec-primary-evidence",
        lambda document: document["summary"].__setitem__("identified_name_only", 15),
    )


def _modify_aria2_primary(fixture: Fixture) -> None:
    fixture.mutate_json(
        "aria2-primary-evidence",
        lambda document: document["summary"].__setitem__(
            "source_kit_assembly_authorized", True
        ),
    )


def _modify_aria2_inventory(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        aria2["core_source"]["commit"] = "1" * 40

    fixture.mutate_json("source-input-inventory", mutate)


def _stale_feasibility(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        ffmpeg["verified_immutable_inputs"] = ["cairo"]

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
        encoding="utf-8", newline="\n",
    )


def _inject_claim(fixture: Fixture, value: str) -> None:
    _component_mutation(
        fixture, lambda component: component["evidence"][0].__setitem__("claim", value)
    )


def _duplicate_evidence(fixture: Fixture) -> None:
    _component_mutation(
        fixture,
        lambda component: component["evidence"].append(
            copy.deepcopy(component["evidence"][0])
        ),
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
    path.write_bytes(path.read_bytes().replace(
        b'{\n  "schema_version": 1,',
        b'{\n  "schema_version": 1,\n  "schema_version": 1,', 1,
    ))


def _tracked_archive(fixture: Fixture) -> None:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE,
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
