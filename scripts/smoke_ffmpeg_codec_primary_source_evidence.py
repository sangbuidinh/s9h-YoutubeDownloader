from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_ffmpeg_codec_primary_source_evidence as verifier


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = {
    "primary-evidence": ROOT / "legal/primary-source-evidence-ffmpeg-codecs.json",
    "source-correspondence": ROOT / "legal/source-correspondence.json",
    "source-input-inventory": ROOT / "legal/source-input-inventory.json",
    "source-kit-feasibility": ROOT / "legal/source-kit-feasibility.json",
    "source-kit-requirements": ROOT / "legal/source-kit-requirements.json",
    "release-policy": ROOT / "legal/release-policy.json",
    "release-assets": ROOT / "legal/release-assets-v2.json",
    "aria2-primary-evidence": ROOT / "legal/primary-source-evidence-aria2.json",
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
    _positive_unrelated_refinement()
    cases: list[tuple[str, Callable[[Fixture], None]]] = [
        ("missing component", lambda f: _primary(f, lambda d: d["components"].pop())),
        ("duplicate component", lambda f: _primary(f, lambda d: d["components"].insert(1, copy.deepcopy(d["components"][0])))),
        ("invented component", _invent_component),
        ("reordered components", lambda f: _primary(f, lambda d: d["components"].reverse())),
        ("wrong linkage", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("provider_linkage", "dynamic"))),
        ("changed package hash", lambda f: _primary(f, lambda d: d.__setitem__("binary_package_sha256", "0" * 64))),
        ("changed provider release", lambda f: _primary(f, lambda d: d.__setitem__("provider_release_identity", "latest"))),
        ("changed core identity", lambda f: _primary(f, lambda d: d.__setitem__("ffmpeg_core_commit", "1" * 40))),
        ("provider version invented", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("provider_version", "3.12.1"))),
        ("provider version status promoted", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("provider_version_status", "verified"))),
        ("official project changed", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("official_project", "invented"))),
        ("non-official repository", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("official_repository", "https://example.invalid/aom"))),
        ("non-TLS repository", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("official_repository", "http://aomedia.googlesource.com/aom"))),
        ("query-bearing repository", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("official_repository", verifier.REPOSITORIES["libaom"] + "?token=value"))),
        ("release identity kind invented", lambda f: _identity(f, "kind", "git-commit")),
        ("latest upstream substituted", lambda f: _identity(f, "value", "latest")),
        ("immutable ref inserted", lambda f: _identity(f, "value", "1" * 40)),
        ("provider-version explanation removed", lambda f: _identity(f, "resolution_method", "Upstream project identified.")),
        ("archive filename inserted", lambda f: _archive(f, "filename", "libaom.tar.gz")),
        ("archive locator inserted", lambda f: _archive(f, "official_locator", "https://aomedia.googlesource.com/aom/+archive/main.tar.gz")),
        ("archive hash inserted", lambda f: _archive(f, "sha256", "1" * 64)),
        ("archive marked hashed", lambda f: _archive(f, "independently_hashed", True)),
        ("license evidence promoted", lambda f: _component_mutation(f, "libaom", lambda c: c["license_evidence"].__setitem__("status", "verified"))),
        ("provider mapping promoted", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("provider_to_upstream_match", "exact"))),
        ("component promoted verified", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("resolution_status", "verified-immutable-input"))),
        ("component promoted version-only", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("resolution_status", "identified-version-only"))),
        ("upstream release represented as provider input", _claim_provider_use),
        ("missing blocker", lambda f: _component_mutation(f, "libaom", lambda c: c.__setitem__("blockers", []))),
        ("unsorted blockers", lambda f: _component_mutation(f, "libaom", lambda c: c["blockers"].reverse())),
        ("missing official evidence", lambda f: _component_mutation(f, "libaom", lambda c: c["evidence"].pop(1))),
        ("unsorted evidence", lambda f: _component_mutation(f, "libaom", lambda c: c["evidence"].reverse())),
        ("evidence promoted", lambda f: _component_mutation(f, "libaom", lambda c: c["evidence"][0].__setitem__("status", "verified"))),
        ("provider recipe invented", lambda f: _build_flag(f, "exact_historical_recipe_identified")),
        ("dependency versions invented", lambda f: _build_flag(f, "exact_dependency_versions_identified")),
        ("toolchain invented", lambda f: _build_flag(f, "exact_toolchain_identified")),
        ("configure command invented", lambda f: _build_flag(f, "exact_configure_command_identified")),
        ("patch set invented", lambda f: _build_flag(f, "patch_set_identified")),
        ("wrong provider-version count", lambda f: _summary(f, "provider_versions_verified", 1)),
        ("wrong verified count", lambda f: _summary(f, "verified_immutable_inputs", 1)),
        ("wrong name-only count", lambda f: _summary(f, "identified_name_only", 15)),
        ("wrong archive count", lambda f: _summary(f, "archive_hashes_verified", 1)),
        ("batch marked resolved", lambda f: _summary(f, "all_batch_inputs_resolved", True)),
        ("summary assembly authorized", lambda f: _summary(f, "source_kit_assembly_authorized", True)),
        ("primary gate true", lambda f: _primary(f, lambda d: d["gate_state"].__setitem__("publishing_allowed", True))),
        ("inventory provider version inserted", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("provider_version", "3.12.1"))),
        ("inventory version status promoted", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("version_status", "verified"))),
        ("inventory repository inserted", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("upstream_repository", verifier.REPOSITORIES["libaom"]))),
        ("inventory ref inserted", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("immutable_ref", "1" * 40))),
        ("inventory archive hash inserted", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("source_archive_sha256", "1" * 64))),
        ("inventory status promoted", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("resolution_status", "verified-immutable-input"))),
        ("inventory missing blocker", lambda f: _inventory_component(f, "libaom", lambda c: c.__setitem__("blockers", []))),
        ("changed non-batch FFmpeg structural record", _modify_non_batch_structural),
        ("changed aria2 inventory", _modify_aria2_inventory),
        ("changed aria2 primary evidence", _modify_aria2_primary),
        ("changed source correspondence", lambda f: f.mutate_json("source-correspondence", lambda d: d.__setitem__("release_gate_status", "open"))),
        ("changed requirements", lambda f: f.mutate_json("source-kit-requirements", lambda d: d.__setitem__("assembly_authorized", True))),
        ("release policy gate true", lambda f: f.mutate_json("release-policy", lambda d: d.__setitem__("legal_compliance_certified", True))),
        ("release assets ready", lambda f: f.mutate_json("release-assets", lambda d: d.__setitem__("release_readiness", "ready"))),
        ("source asset ready", _source_asset_ready),
        ("modified feasibility bytes", _stale_feasibility),
        ("malformed JSON", lambda f: f.paths["primary-evidence"].write_bytes(b"{")),
        ("UTF-8 BOM", _bom),
        ("CRLF", _crlf),
        ("wrong field order", _wrong_order),
        ("duplicate JSON key", _duplicate_key),
        ("local Windows path", lambda f: _inject_claim(f, "C:" + "\\" + "Users" + "\\" + "example")),
        ("local Unix path", lambda f: _inject_claim(f, "/" + "tmp" + "/source")),
        ("timestamp", lambda f: _inject_claim(f, "2026" + "-07-15T12:34")),
        ("credential", lambda f: _inject_claim(f, "AIza" + "A" * 35)),
        ("token", lambda f: _inject_claim(f, "ghp_" + "A" * 30)),
        ("signed media URL", lambda f: _inject_claim(f, "https://x.google" + "video.com/videoplayback?sig=value")),
        ("unsupported completion claim", _unsupported_claim),
        ("tracked source archive", _tracked_archive),
        ("repository source archive", _repository_archive),
        ("binary introduced", lambda f: _introduced(f, "data/bin/new.exe")),
        ("media introduced", lambda f: _introduced(f, "evidence/video.mp4")),
    ]
    if len(cases) != 76:
        raise AssertionError(f"expected 76 negative cases, found {len(cases)}")
    for name, mutation in cases:
        _expect_rejected(name, mutation)
    print("FFmpeg codec primary-source evidence smoke tests passed")
    return 0


def _expect_rejected(name: str, mutation: Callable[[Fixture], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-evidence-smoke-") as raw:
        fixture = Fixture(Path(raw))
        mutation(fixture)
        try:
            verifier.verify_repository(
                ROOT, overrides=fixture.paths, tracked_paths=fixture.tracked_paths,
                repository_files=fixture.repository_files,
                introduced_paths=fixture.introduced_paths,
            )
        except (
            verifier.FFmpegCodecPrimarySourceEvidenceError, OSError, UnicodeError,
            json.JSONDecodeError, subprocess.SubprocessError,
        ):
            return
    raise AssertionError(f"mutation was accepted: {name}")


def _primary(fixture: Fixture, mutation: Callable[[dict[str, Any]], None]) -> None:
    fixture.mutate_json("primary-evidence", mutation)


def _component(document: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(item for item in document["components"] if item["id"] == cid)


def _component_mutation(
    fixture: Fixture, cid: str, mutation: Callable[[dict[str, Any]], None]
) -> None:
    _primary(fixture, lambda document: mutation(_component(document, cid)))


def _identity(fixture: Fixture, key: str, value: Any) -> None:
    _component_mutation(
        fixture, "libaom",
        lambda component: component["release_identity"].__setitem__(key, value),
    )


def _archive(fixture: Fixture, key: str, value: Any) -> None:
    _component_mutation(
        fixture, "libaom",
        lambda component: component["source_archive"].__setitem__(key, value),
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
        item["id"] = "libinvented"
        document["components"].append(item)
    _primary(fixture, mutate)


def _claim_provider_use(fixture: Fixture) -> None:
    def mutate(component: dict[str, Any]) -> None:
        record = next(item for item in component["evidence"] if item["kind"] == "official-repository-research")
        record["claim"] = "The latest upstream release was selected by Gyan for this package."
    _component_mutation(fixture, "libaom", mutate)


def _build_flag(fixture: Fixture, field: str) -> None:
    _primary(fixture, lambda document: document["provider_build_evidence"].__setitem__(field, True))


def _summary(fixture: Fixture, field: str, value: Any) -> None:
    _primary(fixture, lambda document: document["summary"].__setitem__(field, value))


def _positive_unrelated_refinement() -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-codec-boundary-") as raw:
        fixture = Fixture(Path(raw))

        def refine(component: dict[str, Any]) -> None:
            component["evidence"].append({
                "kind": "official-upstream-research",
                "authority": "libass",
                "locator": "https://github.com/libass/libass",
                "claim": (
                    "Official upstream identity for libass was researched "
                    "without selecting a provider input."
                ),
                "status": "partial",
            })
            component["evidence"].sort(key=lambda item: tuple(item.values()))
            component["blockers"] = sorted([
                "Exact immutable upstream source ref is unresolved.",
                "Exact provider component version is unresolved.",
                "Independent source archive SHA-256 is unresolved.",
            ])

        _inventory_component(fixture, "libass", refine)
        verifier.verify_repository(ROOT, overrides=fixture.paths)


def _modify_non_batch_structural(fixture: Fixture) -> None:
    _inventory_component(
        fixture, "libass",
        lambda component: component.__setitem__("provider_version", "invented"),
    )


def _modify_aria2_inventory(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        aria2 = next(item for item in document["packages"] if item["id"] == "aria2")
        aria2["core_source"]["commit"] = "1" * 40
    fixture.mutate_json("source-input-inventory", mutate)


def _modify_aria2_primary(fixture: Fixture) -> None:
    fixture.mutate_json(
        "aria2-primary-evidence",
        lambda document: document["summary"].__setitem__("source_kit_assembly_authorized", True),
    )


def _source_asset_ready(fixture: Fixture) -> None:
    fixture.mutate_json(
        "release-assets",
        lambda document: document["required_source_asset_templates"][1].__setitem__("status", "not-ready" if document["required_source_asset_templates"][1]["status"] == "ready" else "ready"),
    )


def _stale_feasibility(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
        ffmpeg["verified_immutable_inputs"] = ["libaom"]
    fixture.mutate_json("source-kit-feasibility", mutate)


def _inject_claim(fixture: Fixture, value: str) -> None:
    _component_mutation(
        fixture, "libaom",
        lambda component: component["evidence"][0].__setitem__("claim", value),
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


def _unsupported_claim(fixture: Fixture) -> None:
    path = fixture.paths["feasibility-doc"]
    path.write_text(
        path.read_text(encoding="utf-8") + "\nCorresponding Source is complete.\n",
        encoding="utf-8", newline="\n",
    )


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
