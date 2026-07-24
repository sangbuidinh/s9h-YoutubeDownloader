from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import smoke_ci_workflow as ci_workflow_verifier
import verify_ffmpeg_remaining_library_evidence as remaining_verifier
import verify_legal_notices as legal_notices_verifier


BASELINE = "95587c76919042b0b9d9a5b51f0f2e40e241e346"
PACKAGE_HASH = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
PROVIDER_RELEASE = "8.1.2-essentials_build-www.gyan.dev"
CORE_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"
RELEASE_COMMIT = "46465995c991fe65c5de853fa79bddec09cd6c37"
README_HASH = "9172433fb251059a58d2ff11ba8c6132e04819136ed96e809563911ff0d13816"
CONFIGURATION_HASH = "a181d439987d57ae45533de6f64c0226621911542b9b0ae5e824b6202e890d68"
FEASIBILITY_HASH = "6f6cb8149cf116a184c14c4091436debee6db04f6217e5bd1343b955429aa6c3"

TOP_KEYS = (
    "schema_version", "target_phase", "baseline_commit", "package_id",
    "binary_package_sha256", "provider_release_identity", "ffmpeg_core_commit",
    "evidence_scope", "provider_repository", "exact_package_metadata",
    "toolchain", "configure", "build_orchestration", "patch_evidence",
    "reproducibility", "component_coverage", "summary", "gate_state",
)
SCOPE_KEYS = (
    "official_sources_only", "network_mode", "binary_downloads_allowed",
    "source_archive_downloads_allowed", "runtime_execution_allowed",
    "build_execution_allowed", "provider_package_scope", "claims_boundary",
)
REPOSITORY_KEYS = (
    "official_repository", "release_tag", "release_metadata_commit",
    "release_metadata_commit_status", "repository_role",
    "build_scripts_present_at_release_ref", "build_scripts_presence_status",
    "historical_recipe_identity", "historical_recipe_status", "evidence", "blockers",
)
METADATA_KEYS = (
    "metadata_source", "metadata_identity", "metadata_sha256",
    "ffmpeg_version_present", "ffmpeg_core_commit_present",
    "configuration_string_present", "external_library_names_present",
    "external_library_versions_present", "full_configuration_claim_reconciled",
    "external_version_claim_reconciled", "evidence", "blockers",
)
TOOLCHAIN_KEYS = (
    "provider_toolchain_family", "provider_toolchain_family_status",
    "ucrt64_environment", "ucrt64_environment_status", "host_os", "host_os_version",
    "compiler", "compiler_version", "linker", "linker_version", "binutils_version",
    "runtime_crt", "package_manager", "package_repository_snapshot",
    "supporting_tools", "exact_toolchain_complete", "evidence", "blockers",
)
CONFIGURE_KEYS = (
    "configuration_string_present", "configuration_string_source",
    "configuration_string_sha256", "configure_flags_captured", "configure_flags_count",
    "exact_shell_command_identified", "environment_variables_identified",
    "include_paths_identified", "library_paths_identified",
    "dependency_prefixes_identified", "configure_working_directory_identified",
    "command_wrapper_identified", "exact_configuration_complete", "evidence", "blockers",
)
ORCHESTRATION_KEYS = (
    "environment_bootstrap_identified", "package_repository_snapshot_identified",
    "dependency_acquisition_identified", "dependency_versions_identified",
    "dependency_build_order_identified", "ffmpeg_checkout_step_identified",
    "ffmpeg_configure_step_identified", "ffmpeg_build_step_identified",
    "packaging_step_identified", "checksum_generation_step_identified",
    "complete_historical_recipe_identified", "immutable_recipe_ref",
    "reproducible_entrypoint", "status", "evidence", "blockers",
)
PATCH_KEYS = (
    "ffmpeg_core_patch_set_identified", "dependency_patch_set_identified",
    "explicit_no_core_patches_statement", "explicit_no_dependency_patches_statement",
    "patch_manifest", "patch_status", "evidence", "blockers",
)
REPRODUCIBILITY_KEYS = (
    "clean_environment_definition_identified", "immutable_dependency_inputs_identified",
    "deterministic_build_controls_identified", "build_timestamp_controls_identified",
    "archive_normalization_identified", "binary_comparison_method_identified",
    "reproducible_entrypoint_identified", "independent_reproduction_performed",
    "binary_reproduced", "status", "blockers",
)
COVERAGE_KEYS = (
    "ffmpeg_external_components_total", "dedicated_component_evidence_total",
    "component_level_coverage_complete", "provider_versions_complete",
    "immutable_component_inputs_complete", "toolchain_complete", "configure_complete",
    "build_orchestration_complete", "patch_evidence_complete",
    "reproducibility_complete", "source_kit_complete",
)
SUMMARY_KEYS = (
    "exact_package_identified", "ffmpeg_core_identified",
    "provider_release_repository_identified", "provider_release_metadata_ref_identified",
    "provider_toolchain_family_identified", "exact_compiler_identified",
    "exact_compiler_version_identified", "exact_supporting_tool_versions_identified",
    "configure_flags_captured", "exact_configure_command_identified",
    "historical_build_recipe_identified", "exact_dependency_recipe_identified",
    "patch_set_identified", "reproducible_entrypoint_identified",
    "toolchain_complete", "build_orchestration_complete",
    "source_kit_assembly_authorized", "remaining_blockers",
)
GATE_KEYS = (
    "legal_compliance_certified", "source_availability_certified",
    "source_assets_created", "source_kits_ready", "assembly_authorized",
    "release_gate_reconsideration_allowed", "publishing_allowed",
)
EVIDENCE_KEYS = ("kind", "authority", "locator", "claim", "status")

PATHS = {
    "build-evidence": "legal/ffmpeg-provider-build-feasibility.json",
    "aria2-primary-evidence": "legal/primary-source-evidence-aria2.json",
    "codec-primary-evidence": "legal/primary-source-evidence-ffmpeg-codecs.json",
    "support-primary-evidence": "legal/primary-source-evidence-ffmpeg-support.json",
    "hardware-system-primary-evidence": "legal/primary-source-evidence-ffmpeg-hardware-system.json",
    "remaining-primary-evidence": "legal/primary-source-evidence-ffmpeg-remaining-libraries.json",
    "source-correspondence": "legal/source-correspondence.json",
    "source-input-inventory": "legal/source-input-inventory.json",
    "source-kit-feasibility": "legal/source-kit-feasibility.json",
    "source-kit-requirements": "legal/source-kit-requirements.json",
    "release-policy": "legal/release-policy.json",
    "release-assets": "legal/release-assets-v2.json",
    "readme": "README.md", "legal-readme": "legal/README.md",
    "feasibility-doc": "docs/source-kit-feasibility.md",
}
PROTECTED = (
    "legal/source-input-inventory.json", "legal/source-kit-feasibility.json",
    "legal/primary-source-evidence-aria2.json",
    "legal/primary-source-evidence-ffmpeg-codecs.json",
    "legal/primary-source-evidence-ffmpeg-support.json",
    "legal/primary-source-evidence-ffmpeg-hardware-system.json",
    "legal/primary-source-evidence-ffmpeg-remaining-libraries.json",
    "legal/source-correspondence.json", "legal/source-kit-requirements.json",
    "legal/release-policy.json", "legal/release-assets-v2.json", "legal/components.json",
    "legal/built-artifact-inventory.json", "THIRD_PARTY_NOTICES.md",
    ".gitattributes", ".gitignore", ".github/actions-pins.json",
    ".github/build-dependencies.json", ".github/workflows/ci.yml",
    ".github/workflows/prerelease-v1.2.7-rc.1.yml",
    ".github/workflows/prerelease-v1.3.0-rc.1.yml",
    ".github/workflows/release-v1.3.0.yml", ".github/workflows/release-v1.3.1.yml",
    "scripts/audit_source_kit_feasibility.py", "scripts/verify_source_kit_feasibility.py",
    "scripts/smoke_source_kit_feasibility.py",
    "scripts/verify_aria2_primary_source_evidence.py",
    "scripts/smoke_aria2_primary_source_evidence.py",
    "scripts/verify_ffmpeg_codec_primary_source_evidence.py",
    "scripts/smoke_ffmpeg_codec_primary_source_evidence.py",
    "scripts/verify_ffmpeg_support_primary_source_evidence.py",
    "scripts/smoke_ffmpeg_support_primary_source_evidence.py",
    "scripts/verify_ffmpeg_hardware_system_evidence.py",
    "scripts/smoke_ffmpeg_hardware_system_evidence.py",
    "scripts/verify_ffmpeg_remaining_library_evidence.py",
    "scripts/smoke_ffmpeg_remaining_library_evidence.py",
    "scripts/verify_source_correspondence.py", "scripts/smoke_source_correspondence.py",
    "scripts/smoke_source_kit_requirements.py", "scripts/prepare_release_bundle.py",
    "scripts/prepare_release_legal_payload.py", "scripts/verify_release_legal_gate.py",
    "scripts/verify_release_legal_payload.py",
)
OLD_REPORTS = {
    "bat_ignore_errors_report.txt", "fast_video_scope_report.txt",
    "numbered_two_phase_report.txt",
}
ARCHIVE_SUFFIXES = (
    ".7z", ".bz2", ".gz", ".rar", ".tar", ".tar.bz2", ".tar.gz",
    ".tar.xz", ".tgz", ".txz", ".xz", ".zip",
)
INSTALLER_SUFFIXES = (".appx", ".deb", ".dmg", ".msi", ".pkg", ".rpm")
BINARY_MEDIA_SUFFIXES = (
    ".aac", ".avi", ".dll", ".exe", ".flac", ".m4a", ".mkv", ".mov",
    ".mp3", ".mp4", ".ogg", ".so", ".wav", ".webm",
)
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
TIMESTAMP_RE = re.compile(r"\b20\d\d-\d\d-\d\d(?:[T ][0-2]\d:[0-5]\d)?\b")
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"), re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)
CURRENT_OWNER_SMOKES = {
    ".github/actions-pins.json": "scripts/smoke_workflow_supply_chain.py",
    ".github/build-dependencies.json": "scripts/smoke_build_dependency_lock.py",
    "scripts/prepare_release_bundle.py": "scripts/smoke_release_bundle.py",
}
_CURRENT_OWNER_CACHE: set[tuple[str, str, str]] = set()
UNSUPPORTED_RES = (
    re.compile(r"(?i)\bexact toolchain (?:is )?complete\b(?!\s*[:=]\s*(?:false|no)\b)"),
    re.compile(r"(?i)\bexact historical recipe (?:is )?(?:identified|complete)\b(?!\s*[:=]\s*(?:false|no)\b)"),
    re.compile(r"(?i)\bpatch evidence (?:is )?complete\b(?!\s*[:=]\s*(?:false|no)\b)"),
    re.compile(r"(?i)\bsource kits? (?:is|are) (?:complete|ready)\b(?!\s*[:=]\s*(?:false|no)\b)"),
    re.compile(r"(?i)\bbuild (?:is )?reproducible\b(?!\s*[:=]\s*(?:false|no)\b)"),
)
FeasibilityRunner = Callable[[Path, dict[str, Path]], bytes]


class FFmpegProviderBuildFeasibilityError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify FFmpeg provider build feasibility")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    for option in PATHS:
        parser.add_argument(f"--{option}", type=Path)
    args = parser.parse_args()
    overrides = {
        key.replace("_", "-"): value for key, value in vars(args).items()
        if key != "root" and value is not None
    }
    try:
        verify_repository(args.root, overrides=overrides)
    except (
        FFmpegProviderBuildFeasibilityError, OSError, UnicodeError,
        json.JSONDecodeError, subprocess.SubprocessError,
    ) as exc:
        print(f"FFmpeg provider build feasibility verification failed: {exc}", file=sys.stderr)
        return 1
    print("FFmpeg provider build feasibility verified")
    return 0


def verify_repository(
    root: Path, *, overrides: dict[str, Path] | None = None,
    tracked_paths: list[str] | None = None, repository_files: list[str] | None = None,
    introduced_paths: list[str] | None = None,
    feasibility_runner: FeasibilityRunner | None = None,
) -> None:
    root = root.resolve()
    paths = _paths(root, overrides or {})
    evidence, _ = _load_json(paths["build-evidence"], "build evidence")
    correspondence, _ = _load_json(paths["source-correspondence"], "source correspondence")
    inventory, inventory_raw = _load_json(paths["source-input-inventory"], "source inventory")
    feasibility, feasibility_raw = _load_json(paths["source-kit-feasibility"], "feasibility")
    requirements, _ = _load_json(paths["source-kit-requirements"], "requirements")
    policy, _ = _load_json(paths["release-policy"], "release policy")
    assets, _ = _load_json(paths["release-assets"], "release assets")
    prior = {}
    for key in (
        "aria2-primary-evidence", "codec-primary-evidence", "support-primary-evidence",
        "hardware-system-primary-evidence", "remaining-primary-evidence",
    ):
        prior[key], _ = _load_json(paths[key], key)
    _verify_protected(root, paths)
    _verify_document(evidence, correspondence)
    _verify_inventory(root, inventory, inventory_raw)
    _verify_prior_evidence(root, paths, prior)
    _verify_feasibility(
        root, paths, feasibility, feasibility_raw,
        feasibility_runner or _generate_feasibility,
    )
    _verify_gates(requirements, policy, assets, feasibility, inventory)
    _verify_docs(paths["readme"], paths["legal-readme"], paths["feasibility-doc"])
    _verify_artifacts(root, tracked_paths, repository_files, introduced_paths)
    _hygiene(evidence, "build evidence")
    _verify_prior_owner(root, paths, tracked_paths, repository_files, introduced_paths)


def _paths(root: Path, overrides: dict[str, Path]) -> dict[str, Path]:
    paths = {key: root / relative for key, relative in PATHS.items()}
    _require(not (set(overrides) - set(paths)), "unknown override")
    paths.update(overrides)
    return paths


def _verify_protected(root: Path, paths: dict[str, Path]) -> None:
    reverse = {relative: key for key, relative in PATHS.items()}
    for relative in PROTECTED:
        current_owner = CURRENT_OWNER_SMOKES.get(relative)
        if current_owner is not None:
            _verify_current_owner(root, relative, current_owner)
            continue
        if relative == ".gitattributes":
            try:
                legal_notices_verifier.verify_checkout_policy(root)
            except legal_notices_verifier.LegalVerificationError as exc:
                raise FFmpegProviderBuildFeasibilityError(
                    f"protected file changed: {relative}: {exc}"
                ) from exc
            continue
        if relative == ".github/workflows/ci.yml":
            try:
                ci_workflow_verifier.verify_workflow_file(root)
            except (
                ci_workflow_verifier.WorkflowContractError,
                OSError,
                UnicodeError,
            ) as exc:
                raise FFmpegProviderBuildFeasibilityError(
                    f"protected file changed: {relative}: {exc}"
                ) from exc
            continue
        key = reverse.get(relative)
        if key:
            current = paths[key]
            if relative == "legal/source-kit-requirements.json" and current.resolve() != (root / relative).resolve():
                continue
            _require(current.read_bytes() == _git_blob(root, relative), f"protected file changed: {relative}")
        else:
            result = subprocess.run(
                ["git", "diff", "--quiet", BASELINE, "--", relative], cwd=root,
                check=False,
            )
            _require(result.returncode == 0, f"protected file changed: {relative}")
    _require((root / "VERSION").read_text(encoding="utf-8").strip() == "1.3.1", "VERSION changed")


def _verify_current_owner(root: Path, relative: str, smoke_relative: str) -> None:
    digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    key = (str(root.resolve()), relative, digest)
    if key in _CURRENT_OWNER_CACHE:
        return
    result = subprocess.run(
        [sys.executable, str(root / smoke_relative)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    _require(
        result.returncode == 0,
        f"protected file changed: {relative}: current owner gate failed",
    )
    _CURRENT_OWNER_CACHE.add(key)


def _verify_prior_owner(
    root: Path, paths: dict[str, Path], tracked_paths: list[str] | None,
    repository_files: list[str] | None, introduced_paths: list[str] | None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-provider-owner-") as raw:
        requirements = Path(raw) / "requirements.json"
        requirements.write_bytes(_git_blob(root, "legal/source-kit-requirements.json"))
        try:
            remaining_verifier.verify_repository(
                root,
                overrides={
                    "primary-evidence": paths["remaining-primary-evidence"],
                    "codec-primary-evidence": paths["codec-primary-evidence"],
                    "support-primary-evidence": paths["support-primary-evidence"],
                    "hardware-system-primary-evidence": paths["hardware-system-primary-evidence"],
                    "aria2-primary-evidence": paths["aria2-primary-evidence"],
                    "source-correspondence": paths["source-correspondence"],
                    "source-input-inventory": paths["source-input-inventory"],
                    "source-kit-feasibility": paths["source-kit-feasibility"],
                    "source-kit-requirements": requirements,
                    "release-policy": paths["release-policy"], "release-assets": paths["release-assets"],
                    "readme": paths["readme"], "legal-readme": paths["legal-readme"],
                    "feasibility-doc": paths["feasibility-doc"],
                },
                tracked_paths=tracked_paths, repository_files=repository_files,
                introduced_paths=introduced_paths,
            )
        except remaining_verifier.FFmpegRemainingLibraryEvidenceError as exc:
            raise FFmpegProviderBuildFeasibilityError(f"prior owner rejected fixture: {exc}") from exc


def _verify_document(document: dict[str, Any], correspondence: dict[str, Any]) -> None:
    _keys(document, TOP_KEYS, "top-level")
    _scalars(document, {
        "schema_version": 1, "target_phase": "6B2B2A2", "baseline_commit": BASELINE,
        "package_id": "ffmpeg", "binary_package_sha256": PACKAGE_HASH,
        "provider_release_identity": PROVIDER_RELEASE, "ffmpeg_core_commit": CORE_COMMIT,
    }, "fixed")
    ffmpeg = _package(correspondence, "ffmpeg")
    _require(ffmpeg["binary_package"]["sha256"] == PACKAGE_HASH, "package correspondence changed")
    _require(ffmpeg["core_source"]["commit"] == CORE_COMMIT, "core correspondence changed")
    _verify_scope(document["evidence_scope"])
    _verify_repository(document["provider_repository"])
    _verify_metadata(document["exact_package_metadata"])
    _verify_toolchain(document["toolchain"])
    _verify_configure(document["configure"])
    _verify_orchestration(document["build_orchestration"])
    _verify_patch(document["patch_evidence"])
    _verify_reproducibility(document["reproducibility"])
    _verify_coverage(document["component_coverage"])
    _verify_summary(document["summary"])
    _keys(document["gate_state"], GATE_KEYS, "gate state")
    _require(all(document["gate_state"][key] is False for key in GATE_KEYS), "dedicated gate promoted")
    _verify_rich_blockers(document)


def _verify_scope(record: dict[str, Any]) -> None:
    _keys(record, SCOPE_KEYS, "scope")
    _scalars(record, {
        "official_sources_only": True, "network_mode": "read-only",
        "binary_downloads_allowed": False, "source_archive_downloads_allowed": False,
        "runtime_execution_allowed": False, "build_execution_allowed": False,
        "provider_package_scope": "exact-package-only",
    }, "scope")
    boundary = record["claims_boundary"].casefold()
    for term in ("feasibility", "gaps", "not a reproducible recipe", "not a completed source kit"):
        _require(term in boundary, f"claims boundary missing: {term}")


def _verify_repository(record: dict[str, Any]) -> None:
    _keys(record, REPOSITORY_KEYS, "repository")
    _scalars(record, {
        "official_repository": "https://github.com/GyanD/codexffmpeg",
        "release_tag": "8.1.2", "release_metadata_commit": RELEASE_COMMIT,
        "release_metadata_commit_status": "verified", "repository_role": "release-metadata-and-assets",
        "build_scripts_present_at_release_ref": False,
        "build_scripts_presence_status": "verified", "historical_recipe_identity": "unresolved",
        "historical_recipe_status": "unresolved",
    }, "repository")
    _require(re.fullmatch(r"[0-9a-f]{40}", record["release_metadata_commit"]), "release commit is not immutable")
    _evidence(record["evidence"], "repository")
    kinds = {item["kind"]: item for item in record["evidence"]}
    _require(set(kinds) == {"release-asset-metadata", "release-reference", "release-tree-inspection", "repository-role"}, "repository evidence set changed")
    _require("no provider build script" in kinds["release-tree-inspection"]["claim"].casefold(), "release tree result missing")
    _blockers(record["blockers"], "repository")


def _verify_metadata(record: dict[str, Any]) -> None:
    _keys(record, METADATA_KEYS, "metadata")
    _scalars(record, {
        "metadata_source": "legal/source-correspondence.json#packages/ffmpeg/binary_package/provider_metadata_files/README.txt",
        "metadata_identity": "ffmpeg-8.1.2-essentials_build/README.txt",
        "metadata_sha256": README_HASH, "ffmpeg_version_present": True,
        "ffmpeg_core_commit_present": True, "configuration_string_present": True,
        "external_library_names_present": True, "external_library_versions_present": False,
    }, "metadata")
    _require("38-entry" in record["full_configuration_claim_reconciled"], "configuration reconciliation missing")
    _require("does not contain external-library versions" in record["external_version_claim_reconciled"], "version reconciliation missing")
    _evidence(record["evidence"], "metadata")
    _blockers(record["blockers"], "metadata")


def _verify_toolchain(record: dict[str, Any]) -> None:
    _keys(record, TOOLCHAIN_KEYS, "toolchain")
    _scalars(record, {
        "provider_toolchain_family": "UCRT64", "provider_toolchain_family_status": "provider-identified",
        "ucrt64_environment": True, "ucrt64_environment_status": "provider-identified",
        "host_os": "unresolved", "host_os_version": "unresolved", "compiler": "unresolved",
        "compiler_version": "unresolved", "linker": "unresolved", "linker_version": "unresolved",
        "binutils_version": "unresolved", "runtime_crt": "UCRT", "package_manager": "unresolved",
        "package_repository_snapshot": "unresolved", "supporting_tools": [],
        "exact_toolchain_complete": False,
    }, "toolchain")
    _evidence(record["evidence"], "toolchain")
    _require("ucrt64" in " ".join(item["claim"] for item in record["evidence"]).casefold(), "UCRT64 evidence missing")
    _blockers(record["blockers"], "toolchain")


def _verify_configure(record: dict[str, Any]) -> None:
    _keys(record, CONFIGURE_KEYS, "configure")
    _scalars(record, {
        "configuration_string_present": True,
        "configuration_string_source": "ffmpeg-8.1.2-essentials_build/README.txt#release-essentials-build-configuration",
        "configuration_string_sha256": CONFIGURATION_HASH, "configure_flags_captured": True,
        "configure_flags_count": 38, "exact_shell_command_identified": False,
        "environment_variables_identified": False, "include_paths_identified": False,
        "library_paths_identified": False, "dependency_prefixes_identified": False,
        "configure_working_directory_identified": False, "command_wrapper_identified": False,
        "exact_configuration_complete": False,
    }, "configure")
    _evidence(record["evidence"], "configure")
    _require("not an exact shell command" in next(item for item in record["evidence"] if item["kind"] == "exact-package-readme")["claim"].casefold(), "flags confused with command")
    _blockers(record["blockers"], "configure")


def _verify_orchestration(record: dict[str, Any]) -> None:
    _keys(record, ORCHESTRATION_KEYS, "orchestration")
    for key in ORCHESTRATION_KEYS[:11]:
        _require(record[key] is False, f"orchestration promoted: {key}")
    _scalars(record, {
        "immutable_recipe_ref": "unresolved", "reproducible_entrypoint": "unresolved",
        "status": "partial",
    }, "orchestration")
    _evidence(record["evidence"], "orchestration")
    _blockers(record["blockers"], "orchestration")


def _verify_patch(record: dict[str, Any]) -> None:
    _keys(record, PATCH_KEYS, "patch")
    for key in PATCH_KEYS[:4]:
        _require(record[key] is False, f"patch field promoted: {key}")
    _require(record["patch_manifest"] == [] and record["patch_status"] == "unresolved", "patch status promoted")
    _evidence(record["evidence"], "patch")
    _require("not an explicit statement" in record["evidence"][0]["claim"].casefold(), "no-patch inference fabricated")
    _blockers(record["blockers"], "patch")


def _verify_reproducibility(record: dict[str, Any]) -> None:
    _keys(record, REPRODUCIBILITY_KEYS, "reproducibility")
    for key in REPRODUCIBILITY_KEYS[:9]:
        _require(record[key] is False, f"reproducibility promoted: {key}")
    _require(record["status"] == "unresolved", "reproducibility status promoted")
    _blockers(record["blockers"], "reproducibility")


def _verify_coverage(record: dict[str, Any]) -> None:
    _keys(record, COVERAGE_KEYS, "coverage")
    _require(record == {
        "ffmpeg_external_components_total": 55, "dedicated_component_evidence_total": 55,
        "component_level_coverage_complete": True, "provider_versions_complete": False,
        "immutable_component_inputs_complete": False, "toolchain_complete": False,
        "configure_complete": False, "build_orchestration_complete": False,
        "patch_evidence_complete": False, "reproducibility_complete": False,
        "source_kit_complete": False,
    }, "coverage state changed")


def _verify_summary(record: dict[str, Any]) -> None:
    _keys(record, SUMMARY_KEYS, "summary")
    true_fields = {
        "exact_package_identified", "ffmpeg_core_identified",
        "provider_release_repository_identified", "provider_release_metadata_ref_identified",
        "provider_toolchain_family_identified", "configure_flags_captured",
    }
    for key in true_fields:
        _require(record[key] is True, f"verified summary changed: {key}")
    for key in set(SUMMARY_KEYS[:-1]) - true_fields:
        _require(record[key] is False, f"unresolved summary promoted: {key}")
    _blockers(record["remaining_blockers"], "summary")


def _verify_rich_blockers(document: dict[str, Any]) -> None:
    values = []
    for owner in (
        "provider_repository", "exact_package_metadata", "toolchain", "configure",
        "build_orchestration", "patch_evidence", "reproducibility",
    ):
        values.extend(document[owner]["blockers"])
    values.extend(document["summary"]["remaining_blockers"])
    joined = " ".join(values).casefold()
    for term in (
        "historical provider", "host", "compiler", "linker", "binutils", "supporting tool",
        "package repository snapshot", "configure", "environment", "paths",
        "dependency acquisition", "build order", "packaging", "patch set", "no-patch",
        "reproducible", "independent reproduction",
    ):
        _require(term in joined, f"dedicated blocker model missing: {term}")


def _verify_inventory(root: Path, document: dict[str, Any], raw: bytes) -> None:
    baseline = _git_blob(root, "legal/source-input-inventory.json")
    _require(raw == baseline, "source-input inventory bytes changed")
    original = _load_json_bytes(baseline, "baseline inventory")
    _require(document == original, "source-input inventory semantics changed")
    ffmpeg = _package(document, "ffmpeg")
    _require(len(ffmpeg["external_components"]) == 55, "FFmpeg component count changed")
    _require(_package(document, "aria2") == _package(original, "aria2"), "aria2 inventory changed")


def _verify_prior_evidence(root: Path, paths: dict[str, Path], prior: dict[str, dict[str, Any]]) -> None:
    for key in prior:
        _require(paths[key].read_bytes() == _git_blob(root, PATHS[key]), f"prior evidence changed: {key}")
    counts = tuple(len(prior[key]["components"]) for key in (
        "codec-primary-evidence", "support-primary-evidence",
        "hardware-system-primary-evidence", "remaining-primary-evidence",
    ))
    _require(counts == (16, 14, 14, 11), "component evidence coverage changed")


def _verify_feasibility(
    root: Path, paths: dict[str, Path], document: dict[str, Any], raw: bytes,
    runner: FeasibilityRunner,
) -> None:
    baseline = _git_blob(root, "legal/source-kit-feasibility.json")
    _require(raw == baseline and _sha256(raw) == FEASIBILITY_HASH, "feasibility bytes changed")
    first, second = runner(root, paths), runner(root, paths)
    _require(first == second == raw, "feasibility regeneration differs")
    _require(_package(document, "ffmpeg")["source_kit_status"] == "not-ready", "FFmpeg kit ready")


def _generate_feasibility(root: Path, paths: dict[str, Path]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="s9h-provider-feasibility-") as raw:
        output = Path(raw) / "generated.json"
        result = subprocess.run([
            sys.executable, str(root / "scripts/audit_source_kit_feasibility.py"),
            "--source-correspondence", str(paths["source-correspondence"]),
            "--source-kit-requirements", str(paths["source-kit-requirements"]),
            "--source-input-inventory", str(paths["source-input-inventory"]),
            "--output", str(output),
        ], cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _require(result.returncode == 0, "feasibility generator failed")
        return output.read_bytes()


def _verify_gates(
    requirements: dict[str, Any], policy: dict[str, Any], assets: dict[str, Any],
    feasibility: dict[str, Any], inventory: dict[str, Any],
) -> None:
    _require(all(item.get("status") == "blocked" for item in requirements.get("kits", [])), "requirements gate opened")
    _require(policy.get("policy_mode") == "fail-closed", "release policy opened")
    _require(len(policy.get("releases", [])) == 4 and all(item.get("status") == "blocked" for item in policy["releases"]), "release gate opened")
    _require(assets.get("release_readiness") == "blocked", "assets gate opened")
    _require(feasibility.get("overall_status") == "blocked-inventory-recorded", "feasibility gate opened")
    for source in (requirements, policy, assets, feasibility, inventory):
        for key in (
            "release_gate_reconsideration_allowed", "legal_compliance_certified",
            "source_availability_certified", "source_assets_created", "source_kits_ready",
            "assembly_authorized", "release_payload_integrated",
        ):
            if key in source:
                _require(source[key] is False, f"shared gate promoted: {key}")


def _verify_docs(readme: Path, legal_readme: Path, detail: Path) -> None:
    docs = {
        "README": _read_doc(readme), "legal README": _read_doc(legal_readme),
        "feasibility document": _read_doc(detail),
    }
    combined = "\n".join(docs.values()).casefold()
    for phrase in (
        "phase 6b2b2a2", "does not modify the conservative source-input inventory",
        "prior component verifiers retain ownership", "detailed provider toolchain and orchestration research is stored in",
        "no readiness status changed", "provider release metadata is not a build recipe",
        "ucrt64 is not an exact compiler identity", "configuration flags are not a complete configure command",
        "exact dependency versions remain unresolved", "exact compiler and supporting-tool versions remain unresolved",
        "exact package repository snapshot remains unresolved", "patch evidence remains unresolved",
        "reproducibility remains unresolved", "component evidence coverage: 55/55",
        "provider versions complete: false", "exact toolchain complete: false",
        "exact configure command complete: false", "build orchestration complete: false",
        "patch evidence complete: false", "reproducibility complete: false",
        "source kit complete: false", "no build was performed", "no binary was reproduced",
        "no source kit was assembled", "assembly remains unauthorized", "publishing remains blocked",
        "existing releases are not retroactively certified", "not legal advice",
    ):
        _require(phrase in combined, f"documentation missing: {phrase}")
    detailed = docs["feasibility document"].casefold()
    for phrase in (
        RELEASE_COMMIT, "release-metadata-and-assets", "build scripts present at release ref | false",
        "configuration string present | true", "external library versions present | false",
        "provider toolchain family | ucrt64", "configure flags captured | true",
        "configure flag count | 38", "exact shell command identified | false",
    ):
        _require(phrase in detailed, f"matrix missing: {phrase}")
    for label, text in docs.items():
        _hygiene(text, label)
        _require(not any(pattern.search(text) for pattern in UNSUPPORTED_RES), f"unsupported claim in {label}")


def _verify_artifacts(
    root: Path, tracked_paths: list[str] | None, repository_files: list[str] | None,
    introduced_paths: list[str] | None,
) -> None:
    tracked_paths = tracked_paths if tracked_paths is not None else _git_lines(root, ["ls-files"])
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for item in tracked_paths), "tracked archive or installer")
    introduced_paths = introduced_paths if introduced_paths is not None else sorted(_changed_paths(root))
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES + BINARY_MEDIA_SUFFIXES) for item in introduced_paths), "forbidden introduced file")
    if repository_files is None:
        repository_files = [
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
            and "__pycache__" not in path.parts and path.name not in OLD_REPORTS
        ]
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for item in repository_files), "archive or installer in repository")


def _changed_paths(root: Path) -> set[str]:
    changed = set(_git_lines(root, ["diff", "--name-only", BASELINE, "--"]))
    for line in subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.splitlines():
        if line.startswith("?? ") and line[3:].replace("\\", "/") not in OLD_REPORTS:
            changed.add(line[3:].replace("\\", "/"))
    return changed


def _evidence(records: Any, label: str) -> None:
    _require(isinstance(records, list) and records, f"missing evidence: {label}")
    order = []
    for record in records:
        _keys(record, EVIDENCE_KEYS, f"{label} evidence")
        _require(record["status"] in {"verified", "provider-identified", "partial", "unresolved"}, f"invalid evidence status: {label}")
        _official(record["locator"], label)
        order.append(tuple(record[key] for key in EVIDENCE_KEYS))
    _require(order == sorted(set(order)), f"unsorted or duplicate evidence: {label}")


def _official(locator: str, label: str) -> None:
    if locator.startswith("legal/"):
        return
    parsed = urlparse(locator)
    host, path = (parsed.hostname or "").casefold(), parsed.path.casefold().rstrip("/")
    _require(parsed.scheme == "https" and not parsed.query and not parsed.fragment, f"insecure locator: {label}")
    if host == "www.gyan.dev":
        _require(path == "/ffmpeg/builds", f"unapproved Gyan locator: {label}")
    elif host == "github.com":
        _require(path == "/gyand/codexffmpeg" or path.startswith("/gyand/codexffmpeg/"), f"unapproved GitHub locator: {label}")
    elif host == "api.github.com":
        _require(path.startswith("/repos/gyand/codexffmpeg/"), f"unapproved API locator: {label}")
    elif host == "raw.githubusercontent.com":
        _require(path.startswith(f"/gyand/codexffmpeg/{RELEASE_COMMIT}/"), f"mutable raw locator: {label}")
    else:
        _require(False, f"unofficial authority: {label}")


def _blockers(value: Any, label: str) -> None:
    _require(
        isinstance(value, list) and bool(value)
        and value == sorted(set(value))
        and all(isinstance(item, str) and item for item in value),
        f"invalid blockers: {label}",
    )


def _scalars(record: dict[str, Any], expected: dict[str, Any], label: str) -> None:
    for key, value in expected.items():
        _require(record.get(key) == value, f"{label} field changed: {key}")


def _keys(value: Any, expected: tuple[str, ...], label: str) -> None:
    _require(isinstance(value, dict) and tuple(value) == expected, f"invalid schema/order: {label}")


def _package(document: dict[str, Any], package_id: str) -> dict[str, Any]:
    found = [item for item in document.get("packages", []) if item.get("id") == package_id]
    _require(len(found) == 1, f"package missing or duplicate: {package_id}")
    return found[0]


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    return _load_json_bytes(raw, label), raw


def _load_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    _require(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw, f"noncanonical encoding: {label}")
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    _require(isinstance(document, dict) and raw == _canonical_json_bytes(document), f"noncanonical JSON: {label}")
    return document


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_doc(path: Path) -> str:
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw, f"noncanonical documentation: {path.name}")
    return raw.decode("utf-8")


def _hygiene(value: object, label: str) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    _require(not LOCAL_PATH_RE.search(text), f"local path in {label}")
    _require(not TIMESTAMP_RE.search(text), f"timestamp in {label}")
    _require(not any(pattern.search(text) for pattern in SECRET_RES), f"secret or signed URL in {label}")


def _git_blob(root: Path, relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{BASELINE}:{relative}"], cwd=root, check=True, stdout=subprocess.PIPE).stdout


def _git_lines(root: Path, arguments: list[str]) -> list[str]:
    return subprocess.run(["git", *arguments], cwd=root, check=True, text=True, stdout=subprocess.PIPE).stdout.splitlines()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _suffix(path: str, suffixes: tuple[str, ...]) -> bool:
    return any(path.endswith(suffix) for suffix in suffixes)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise FFmpegProviderBuildFeasibilityError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
