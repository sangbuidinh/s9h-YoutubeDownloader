from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import verify_ffmpeg_provider_build_feasibility as verifier


ROOT = Path(__file__).resolve().parents[1]
PHASE_PATHS = (
    "README.md", "docs/source-kit-feasibility.md", "legal/README.md",
    "legal/ffmpeg-provider-build-feasibility.json",
    "scripts/smoke_ffmpeg_provider_build_feasibility.py",
    "scripts/verify_ffmpeg_provider_build_feasibility.py",
)
EXPECTED_ERRORS = (
    verifier.FFmpegProviderBuildFeasibilityError, OSError, UnicodeError,
    json.JSONDecodeError, subprocess.SubprocessError,
)


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="s9h-provider-build-smoke-")
        base = Path(self.temp.name)
        self.paths: dict[str, Path] = {}
        for key, relative in verifier.PATHS.items():
            target = base / relative
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
            repository_files=self.repository_files, introduced_paths=self.introduced_paths,
            feasibility_runner=self.feasibility_runner,
        )


Mutation = Callable[[Fixture], None]


def _build(section: str | None, field: str, value: Any) -> Mutation:
    def mutate(fixture: Fixture) -> None:
        fixture.mutate_json(
            "build-evidence",
            lambda document: (document if section is None else document[section]).__setitem__(
                field, copy.deepcopy(value)
            ),
        )
    return mutate


def _prior(key: str) -> Mutation:
    return lambda fixture: fixture.mutate_json(
        key, lambda document: document.__setitem__("mutation_probe", True)
    )


def _ffmpeg(document: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in document["packages"] if item["id"] == "ffmpeg")


def _aria2(document: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in document["packages"] if item["id"] == "aria2")


def _inventory(mutation: Callable[[dict[str, Any]], None]) -> Mutation:
    return lambda fixture: fixture.mutate_json("source-input-inventory", mutation)


def _inventory_field(owner: str, field: str, value: Any) -> Mutation:
    return _inventory(lambda document: _ffmpeg(document)[owner].__setitem__(field, value))


def _inventory_list(owner: str, field: str, value: str) -> Mutation:
    return _inventory(lambda document: _ffmpeg(document)[owner][field].append(value))


def _malformed(fixture: Fixture) -> None:
    fixture.paths["build-evidence"].write_bytes(b"{\n")


def _duplicate_key(fixture: Fixture) -> None:
    path = fixture.paths["build-evidence"]
    path.write_bytes(path.read_bytes().replace(b"{\n", b'{\n  "schema_version": 1,\n', 1))


def _bom(fixture: Fixture) -> None:
    path = fixture.paths["build-evidence"]
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _crlf(fixture: Fixture) -> None:
    path = fixture.paths["build-evidence"]
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def _wrong_order(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        value = document.pop("schema_version")
        document["schema_version"] = value
    fixture.mutate_json("build-evidence", mutate)


def _evidence_order(fixture: Fixture) -> None:
    fixture.mutate_json(
        "build-evidence", lambda document: document["provider_repository"]["evidence"].reverse()
    )


def _evidence_duplicate(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        records = document["provider_repository"]["evidence"]
        records.append(copy.deepcopy(records[-1]))
    fixture.mutate_json("build-evidence", mutate)


def _blocker_missing(fixture: Fixture) -> None:
    fixture.mutate_json(
        "build-evidence", lambda document: document["provider_repository"].__setitem__("blockers", [])
    )


def _blocker_order(fixture: Fixture) -> None:
    fixture.mutate_json(
        "build-evidence", lambda document: document["provider_repository"]["blockers"].reverse()
    )


def _locator(value: str) -> Mutation:
    def mutate(fixture: Fixture) -> None:
        fixture.mutate_json(
            "build-evidence",
            lambda document: document["provider_repository"]["evidence"][0].__setitem__("locator", value),
        )
    return mutate


def _claim(value: str) -> Mutation:
    def mutate(fixture: Fixture) -> None:
        fixture.mutate_json(
            "build-evidence",
            lambda document: document["provider_repository"]["evidence"][0].__setitem__("claim", value),
        )
    return mutate


def _stale_feasibility(fixture: Fixture) -> None:
    fixture.mutate_json(
        "source-kit-feasibility", lambda document: document.__setitem__("overall_status", "ready")
    )


def _non_deterministic(fixture: Fixture) -> None:
    expected = fixture.paths["source-kit-feasibility"].read_bytes()
    calls = 0
    def generate(_root: Path, _paths: dict[str, Path]) -> bytes:
        nonlocal calls
        calls += 1
        return expected if calls == 1 else expected + b"\n"
    fixture.feasibility_runner = generate


def _inventory_reordered(fixture: Fixture) -> None:
    def mutate(document: dict[str, Any]) -> None:
        value = document.pop("schema_version")
        document["schema_version"] = value
    fixture.mutate_json("source-input-inventory", mutate)


def _inventory_semantic_bytes(fixture: Fixture) -> None:
    path = fixture.paths["source-input-inventory"]
    path.write_bytes(path.read_bytes() + b"\n")


def _tracked_archive(fixture: Fixture) -> None:
    fixture.tracked_paths.append("review/evidence.zip")


def _introduced(path: str) -> Mutation:
    return lambda fixture: fixture.introduced_paths.append(path)


def _doc(text: str) -> Mutation:
    return lambda fixture: fixture.append_doc(text)


def _cases() -> list[tuple[str, Mutation]]:
    cases: list[tuple[str, Mutation]] = []
    fixed = (
        ("wrong package", None, "package_id", "aria2"),
        ("wrong hash", None, "binary_package_sha256", "0" * 64),
        ("wrong provider release", None, "provider_release_identity", "wrong"),
        ("wrong core", None, "ffmpeg_core_commit", "0" * 40),
        ("wrong baseline", None, "baseline_commit", "0" * 40),
    )
    cases.extend((name, _build(section, field, value)) for name, section, field, value in fixed)
    repository = (
        ("missing repository", "official_repository", ""),
        ("unofficial repository", "official_repository", "https://example.com/ffmpeg"),
        ("mutable repository", "official_repository", "https://github.com/GyanD/codexffmpeg/tree/main"),
        ("short release commit", "release_metadata_commit", "4646599"),
        ("release commit as core", "release_metadata_commit", verifier.CORE_COMMIT),
        ("release commit as recipe", "historical_recipe_identity", verifier.RELEASE_COMMIT),
        ("invented repository role", "repository_role", "build-recipe"),
        ("invented build scripts", "build_scripts_present_at_release_ref", True),
        ("invented recipe status", "historical_recipe_status", "verified"),
    )
    cases.extend((name, _build("provider_repository", field, value)) for name, field, value in repository)
    metadata = (
        ("changelog as exact metadata", "metadata_source", "https://www.gyan.dev/ffmpeg/builds/"),
        ("invented external versions", "external_library_versions_present", True),
        ("missing configuration string", "configuration_string_present", False),
    )
    cases.extend((name, _build("exact_package_metadata", field, value)) for name, field, value in metadata)
    toolchain = (
        ("UCRT as compiler", "compiler", "gcc"), ("UCRT as compiler version", "compiler_version", "UCRT64"),
        ("invented host", "host_os", "Windows"), ("invented host version", "host_os_version", "11"),
        ("invented compiler", "compiler", "gcc.exe"), ("invented compiler version", "compiler_version", "15.1"),
        ("invented linker", "linker", "ld.exe"), ("invented linker version", "linker_version", "2.44"),
        ("invented binutils", "binutils_version", "2.44"),
        ("invented package manager", "package_manager", "msys2"),
        ("invented package snapshot", "package_repository_snapshot", "current"),
        ("invented supporting tools", "supporting_tools", [{"name": "make", "version": "4.4"}]),
        ("toolchain complete", "exact_toolchain_complete", True),
    )
    cases.extend((name, _build("toolchain", field, value)) for name, field, value in toolchain)
    configure = (
        ("flags as command", "exact_shell_command_identified"),
        ("invented environment", "environment_variables_identified"),
        ("invented include paths", "include_paths_identified"),
        ("invented library paths", "library_paths_identified"),
        ("invented dependency prefixes", "dependency_prefixes_identified"),
        ("invented working directory", "configure_working_directory_identified"),
        ("invented wrapper", "command_wrapper_identified"),
        ("configuration complete", "exact_configuration_complete"),
    )
    cases.extend((name, _build("configure", field, True)) for name, field in configure)
    orchestration = (
        "environment_bootstrap_identified", "package_repository_snapshot_identified",
        "dependency_acquisition_identified", "dependency_versions_identified",
        "dependency_build_order_identified", "ffmpeg_checkout_step_identified",
        "ffmpeg_configure_step_identified", "ffmpeg_build_step_identified",
        "packaging_step_identified", "checksum_generation_step_identified",
        "complete_historical_recipe_identified",
    )
    cases.extend((f"invented orchestration {field}", _build("build_orchestration", field, True)) for field in orchestration)
    cases.extend((
        ("invented recipe ref", _build("build_orchestration", "immutable_recipe_ref", verifier.RELEASE_COMMIT)),
        ("invented reproducible entrypoint", _build("build_orchestration", "reproducible_entrypoint", "build.ps1")),
        ("orchestration complete", _build("build_orchestration", "status", "verified")),
    ))
    patch = (
        "ffmpeg_core_patch_set_identified", "dependency_patch_set_identified",
        "explicit_no_core_patches_statement", "explicit_no_dependency_patches_statement",
    )
    cases.extend((f"invented patch {field}", _build("patch_evidence", field, True)) for field in patch)
    cases.extend((
        ("invented patch manifest", _build("patch_evidence", "patch_manifest", ["patch.diff"])),
        ("invented no-patch status", _build("patch_evidence", "patch_status", "verified-no-patches")),
        ("independent reproduction claimed", _build("reproducibility", "independent_reproduction_performed", True)),
        ("binary reproduction claimed", _build("reproducibility", "binary_reproduced", True)),
    ))
    coverage = (
        ("coverage count", "ffmpeg_external_components_total", 54),
        ("provider versions complete", "provider_versions_complete", True),
        ("toolchain coverage complete", "toolchain_complete", True),
        ("configure coverage complete", "configure_complete", True),
        ("orchestration coverage complete", "build_orchestration_complete", True),
        ("patch coverage complete", "patch_evidence_complete", True),
        ("reproducibility coverage complete", "reproducibility_complete", True),
        ("source kit complete", "source_kit_complete", True),
    )
    cases.extend((name, _build("component_coverage", field, value)) for name, field, value in coverage)
    cases.extend((f"gate promoted {field}", _build("gate_state", field, True)) for field in verifier.GATE_KEYS)
    cases.extend((
        ("prior codec changed", _prior("codec-primary-evidence")),
        ("prior support changed", _prior("support-primary-evidence")),
        ("prior hardware changed", _prior("hardware-system-primary-evidence")),
        ("prior remaining changed", _prior("remaining-primary-evidence")),
        ("prior aria2 changed", _prior("aria2-primary-evidence")),
        ("stale feasibility", _stale_feasibility),
        ("non-deterministic feasibility", _non_deterministic),
        ("unsupported reproducibility", _doc("Build is reproducible.")),
        ("unsupported toolchain", _doc("Exact toolchain is complete.")),
        ("unsupported recipe", _doc("Exact historical recipe is identified.")),
        ("unsupported patch", _doc("Patch evidence is complete.")),
        ("unsupported source kit", _doc("Source kit is complete.")),
        ("malformed JSON", _malformed), ("duplicate key", _duplicate_key),
        ("BOM", _bom), ("CRLF", _crlf), ("field order", _wrong_order),
        ("evidence order", _evidence_order), ("evidence duplicate", _evidence_duplicate),
        ("missing blocker", _blocker_missing), ("blocker order", _blocker_order),
        ("HTTP locator", _locator("http://github.com/GyanD/codexffmpeg")),
        ("search locator", _locator("https://www.google.com/search")),
        ("unofficial mirror", _locator("https://example.com/mirror")),
        ("Windows path", _claim("C:\\Users\\example\\README.txt")),
        ("Unix path", _claim("/home/example/README.txt")),
        ("timestamp", _claim("2026-07-15T12:34")),
        ("API key", _claim("AIza" + "A" * 32)),
        ("GitHub token", _claim("ghp_" + "A" * 24)),
        ("signed media URL", _claim("https://r1.googlevideo.com/videoplayback?sig=secret")),
        ("tracked archive", _tracked_archive),
        ("introduced binary", _introduced("review/ffmpeg.exe")),
        ("introduced installer", _introduced("review/sdk.msi")),
        ("introduced media", _introduced("review/sample.mp4")),
    ))
    cases.extend((
        ("inventory toolchain evidence", _inventory(lambda document: _ffmpeg(document)["toolchain"]["evidence"][0].__setitem__("claim", "changed"))),
        ("inventory toolchain blockers", _inventory_list("toolchain", "blockers", "changed")),
        ("inventory orchestration evidence", _inventory(lambda document: _ffmpeg(document)["build_orchestration"]["evidence"][0].__setitem__("claim", "changed"))),
        ("inventory orchestration blockers", _inventory_list("build_orchestration", "blockers", "changed")),
        ("inventory toolchain structural", _inventory_field("toolchain", "compiler", "gcc")),
        ("inventory orchestration structural", _inventory_field("build_orchestration", "immutable_ref", verifier.RELEASE_COMMIT)),
        ("inventory package blocker", _inventory(lambda document: _ffmpeg(document)["blockers"].append("changed"))),
        ("inventory FFmpeg component", _inventory(lambda document: _ffmpeg(document)["external_components"][0]["blockers"].append("changed"))),
        ("inventory aria2 record", _inventory(lambda document: _aria2(document)["blockers"].append("changed"))),
        ("inventory gate", _inventory(lambda document: document.__setitem__("release_gate_reconsideration_allowed", True))),
        ("inventory reordered", _inventory_reordered),
        ("inventory semantic-equivalent bytes", _inventory_semantic_bytes),
    ))
    if len(cases) < 119:
        raise AssertionError(f"mutation coverage too small: {len(cases)}")
    return cases


def _positive_inventory_and_future_boundary() -> None:
    fixture = Fixture()
    try:
        baseline_inventory = subprocess.run(
            ["git", "show", f"{verifier.BASELINE}:legal/source-input-inventory.json"],
            cwd=ROOT, check=True, stdout=subprocess.PIPE,
        ).stdout
        if fixture.paths["source-input-inventory"].read_bytes() != baseline_inventory:
            raise AssertionError("positive fixture inventory differs")
        fixture.mutate_json(
            "source-kit-requirements",
            lambda document: document.__setitem__("future_source_kit_assembly_record", {
                "status": "recorded-outside-phase-owner",
                "assembly_authorized": False,
                "publishing_allowed": False,
            }),
        )
        fixture.verify()
    finally:
        fixture.close()


def main() -> int:
    verifier.verify_repository(ROOT)
    _positive_inventory_and_future_boundary()
    for number, (name, mutation) in enumerate(_cases(), start=1):
        fixture = Fixture()
        try:
            mutation(fixture)
            try:
                fixture.verify()
            except EXPECTED_ERRORS:
                continue
            raise AssertionError(f"mutation {number} accepted: {name}")
        finally:
            fixture.close()
    print("FFmpeg provider build feasibility smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
