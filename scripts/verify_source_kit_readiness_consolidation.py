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

import verify_ffmpeg_provider_build_feasibility as prior_verifier
import source_compliance
import verify_release_legal_gate


BASELINE = "3c3bd8de7ca77fb5f0ecb9a132a76f1aec1e799c"
INVENTORY_HASH = "fc0b87bdae593a30df7c34164d525b207dc9e5f43ecfca5a0b54b1582a47c499"
FEASIBILITY_HASH = "6f6cb8149cf116a184c14c4091436debee6db04f6217e5bd1343b955429aa6c3"

PATHS = {
    "consolidation": "legal/source-kit-readiness-consolidation.json",
    "source-correspondence": "legal/source-correspondence.json",
    "source-kit-requirements": "legal/source-kit-requirements.json",
    "source-input-inventory": "legal/source-input-inventory.json",
    "source-kit-feasibility": "legal/source-kit-feasibility.json",
    "aria2-primary-evidence": "legal/primary-source-evidence-aria2.json",
    "codec-primary-evidence": "legal/primary-source-evidence-ffmpeg-codecs.json",
    "support-primary-evidence": "legal/primary-source-evidence-ffmpeg-support.json",
    "hardware-system-primary-evidence": "legal/primary-source-evidence-ffmpeg-hardware-system.json",
    "remaining-primary-evidence": "legal/primary-source-evidence-ffmpeg-remaining-libraries.json",
    "ffmpeg-build-feasibility": "legal/ffmpeg-provider-build-feasibility.json",
    "release-policy": "legal/release-policy.json",
    "release-assets": "legal/release-assets-v2.json",
    "readme": "README.md",
    "legal-readme": "legal/README.md",
    "feasibility-doc": "docs/source-kit-feasibility.md",
}

AUTHORITATIVE_ROLES = {
    "legal/ffmpeg-provider-build-feasibility.json": "ffmpeg-provider-build-feasibility",
    "legal/primary-source-evidence-aria2.json": "aria2-primary-source-evidence",
    "legal/primary-source-evidence-ffmpeg-codecs.json": "ffmpeg-codec-primary-source-evidence",
    "legal/primary-source-evidence-ffmpeg-hardware-system.json": "ffmpeg-hardware-system-primary-source-evidence",
    "legal/primary-source-evidence-ffmpeg-remaining-libraries.json": "ffmpeg-remaining-library-primary-source-evidence",
    "legal/primary-source-evidence-ffmpeg-support.json": "ffmpeg-support-primary-source-evidence",
    "legal/release-assets-v2.json": "release-asset-contract",
    "legal/release-policy.json": "release-policy",
    "legal/source-correspondence.json": "source-correspondence",
    "legal/source-input-inventory.json": "source-input-inventory",
    "legal/source-kit-feasibility.json": "source-kit-feasibility",
    "legal/source-kit-requirements.json": "source-kit-requirements",
}
CURRENT_RELEASE_PROTECTED_SHA256 = {
    "legal/release-policy.json": "6b2fc3d061287f57bf04e6e02e64d56d5bf36af490db16bba129f160c374fdb7",
    "legal/release-assets-v2.json": "6983a68fe45c66b936ac055179b1ee895c87523ea239b6e035a71372c265a234",
}
RELATIVE_TO_KEY = {relative: key for key, relative in PATHS.items()}

TOP_KEYS = (
    "schema_version", "target_phase", "baseline_commit", "evidence_scope",
    "authoritative_inputs", "packages", "input_disposition",
    "cross_package_summary", "package_material_disposition",
    "assembly_decision", "reconsideration_prerequisites", "gate_state",
)
SCOPE_KEYS = (
    "mode", "network_access_allowed", "new_primary_research_allowed",
    "source_downloads_allowed", "binary_downloads_allowed",
    "source_asset_creation_allowed", "build_execution_allowed",
    "runtime_execution_allowed", "consolidation_only", "claims_boundary",
)
AUTHORITATIVE_KEYS = ("path", "role", "sha256", "status")
PACKAGE_KEYS = (
    "package_id", "evidence_sources", "external_components_total",
    "verified_immutable_inputs", "partial_inputs", "identified_name_only_inputs",
    "system_component_candidates", "provider_versions_complete",
    "immutable_component_inputs_complete", "component_level_evidence_coverage_complete",
    "toolchain_complete", "configure_complete", "build_orchestration_complete",
    "patch_evidence_complete", "reproducibility_complete", "source_assets_created",
    "source_kit_ready", "assembly_eligible", "package_status", "blockers",
)
DISPOSITION_KEYS = (
    "disposition_code", "count", "items", "evidence_meaning", "blocking_effect",
    "required_next_evidence",
)
ITEM_KEYS = ("package_id", "component_id")
SUMMARY_KEYS = (
    "packages_total", "external_components_total", "verified_immutable_inputs",
    "partial_inputs", "identified_name_only_inputs", "system_component_candidates",
    "disposition_union_total", "duplicate_dispositions", "omitted_inputs",
    "all_required_inputs_resolved", "source_assets_created", "source_kits_ready",
)
MATERIAL_PACKAGE_KEYS = ("package_id", "materials", "complete", "blockers")
MATERIAL_KEYS = (
    "material", "status", "accepted_evidence", "unresolved_requirement",
    "assembly_effect",
)
DECISION_KEYS = (
    "decision", "decision_status", "source_asset_assembly_authorized",
    "source_kit_assembly_authorized", "packages_authorized", "packages_blocked",
    "decision_basis", "prohibited_actions", "next_permitted_work",
)
PREREQUISITE_KEYS = (
    "order", "prerequisite", "status", "owning_package", "evidence_required",
    "blocks_assembly",
)
GATE_KEYS = (
    "legal_compliance_certified", "source_availability_certified",
    "source_assets_created", "source_kits_ready", "assembly_authorized",
    "release_gate_reconsideration_allowed", "release_ready", "publishing_allowed",
)

VERIFIED_ARIA2 = ("c-ares", "expat", "libssh2", "sqlite", "zlib")
PARTIAL_ARIA2 = ("gmp",)
FFMPEG_STATIC = (
    "amf", "avisynth", "bzlib", "cairo", "cuda", "cuda-llvm", "cuvid",
    "ffnvcodec", "gmp", "gnutls", "iconv", "libaom", "libass",
    "libfontconfig", "libfreetype", "libfribidi", "libgme", "libgsm",
    "libharfbuzz", "libmfx", "libmp3lame", "libopencore-amrnb",
    "libopencore-amrwb", "libopenjpeg", "libopenmpt", "libopus",
    "librubberband", "libspeex", "libsrt", "libssh", "libtheora",
    "libvidstab", "libvmaf", "libvo-amrwbenc", "libvorbis", "libvpl",
    "libvpx", "libwebp", "libx264", "libx265", "libxml2", "libxvid",
    "libzimg", "libzmq", "lzma", "nvdec", "nvenc", "openal", "sdl2", "zlib",
)
FFMPEG_SYSTEM = ("d3d11va", "d3d12va", "dxva2", "mediafoundation", "vaapi")
DISPOSITION_ORDER = (
    "partial-immutable-input", "provider-version-unresolved",
    "system-interface-review-required", "verified-immutable-input",
)
EXPECTED_ITEMS = {
    "partial-immutable-input": tuple(("aria2", item) for item in PARTIAL_ARIA2),
    "provider-version-unresolved": tuple(("ffmpeg", item) for item in FFMPEG_STATIC),
    "system-interface-review-required": tuple(("ffmpeg", item) for item in FFMPEG_SYSTEM),
    "verified-immutable-input": tuple(("aria2", item) for item in VERIFIED_ARIA2),
}
MATERIAL_ORDER = (
    "toolchain", "configure", "build-orchestration", "patch-evidence",
    "reproducibility", "source-asset-creation", "legal-release-review",
)
MATERIAL_STATUS = {
    "aria2": (
        "unresolved", "not-independently-resolved", "unresolved",
        "not-independently-resolved", "not-independently-resolved", "not-started",
        "blocked",
    ),
    "ffmpeg": (
        "unresolved", "partial", "partial", "unresolved", "unresolved",
        "not-started", "blocked",
    ),
}
ALLOWED_MATERIAL_STATUS = {
    "verified", "partial", "unresolved", "not-independently-resolved",
    "not-started", "blocked",
}
ASSEMBLY_EFFECT = "Incomplete material keeps source-asset assembly blocked."

PREREQUISITES = (
    ("Resolve or formally disposition aria2/gmp immutable-input identity.", "aria2"),
    ("Establish exact provider-selected versions and immutable inputs for all required FFmpeg static components.", "ffmpeg"),
    ("Complete explicit treatment decisions for all five FFmpeg system component candidates.", "ffmpeg"),
    ("Establish sufficient package build materials for aria2.", "aria2"),
    ("Establish sufficient package build materials for FFmpeg, including toolchain, configuration, dependency orchestration and patch disposition.", "ffmpeg"),
    ("Define and verify source-asset assembly procedures.", "cross-package"),
    ("Create source assets only under a separately authorized phase.", "cross-package"),
    ("Verify assembled source assets against the accepted evidence.", "cross-package"),
    ("Perform explicit legal review of source availability and license obligations.", "cross-package"),
    ("Re-run release gates only after all required source-kit prerequisites are satisfied.", "cross-package"),
)
DECISION_BASIS = tuple(sorted((
    "Only 5 of 61 external component inputs have verified immutable evidence.",
    "One aria2 input remains partial.",
    "Fifty FFmpeg static inputs remain provider-version-unresolved.",
    "Five FFmpeg system candidates require final treatment.",
    "aria2 toolchain and build orchestration remain incomplete.",
    "FFmpeg toolchain, configure, and build orchestration remain incomplete.",
    "FFmpeg patch and reproducibility evidence remain incomplete.",
    "No source assets exist.",
    "No source kits exist.",
    "Legal compliance is not certified.",
)))
PROHIBITED_ACTIONS = tuple(sorted((
    "create source archives", "assemble source kits",
    "represent source assets as complete", "enable release gates", "publish releases",
    "retroactively certify existing releases",
)))
NEXT_PERMITTED = tuple(sorted((
    "further evidence recovery under a separately approved phase",
    "policy or legal review", "assembly-procedure design without creating assets",
    "controlled remote publication of accepted evidence after explicit authorization",
)))

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
SOURCE_TREE_PREFIXES = ("source-assets/", "source-kit/", "source-kits/", "third_party/", "vendor/")
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
UNSUPPORTED_DOC_RES = (
    re.compile(r"(?i)\blegal compliance is (?:sufficient|complete|certified)\b"),
    re.compile(r"(?i)\bcorresponding source is (?:complete|available)\b"),
    re.compile(r"(?i)\brelease is approved\b"),
    re.compile(r"(?i)\bexisting releases are certified\b"),
)
FeasibilityRunner = Callable[[Path, dict[str, Path]], bytes]


class SourceKitReadinessConsolidationError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify source-kit readiness consolidation")
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
        SourceKitReadinessConsolidationError, OSError, UnicodeError,
        json.JSONDecodeError, subprocess.SubprocessError,
        source_compliance.SourceComplianceError,
    ) as exc:
        print(f"Source-kit readiness consolidation verification failed: {exc}", file=sys.stderr)
        return 1
    print("Source-kit readiness consolidation verified")
    return 0


def verify_repository(
    root: Path, *, overrides: dict[str, Path] | None = None,
    tracked_paths: list[str] | None = None, repository_files: list[str] | None = None,
    introduced_paths: list[str] | None = None,
    feasibility_runner: FeasibilityRunner | None = None,
) -> None:
    root = root.resolve()
    paths = _paths(root, overrides or {})
    documents: dict[str, dict[str, Any]] = {}
    raw: dict[str, bytes] = {}
    for key in PATHS:
        if key in {"readme", "legal-readme", "feasibility-doc"}:
            continue
        documents[key], raw[key] = _load_json(paths[key], key)
    if (root / source_compliance.OWNER_PATH).is_file():
        # Validate live controls separately, then verify the historical record
        # against its exact preserved pre-migration documents. No research data
        # or authoritative hash inside the consolidation record is rewritten.
        verify_release_legal_gate.validate_repository_control(root)
        for key, relative in (("release-policy", "legal/release-policy.json"),
                              ("release-assets", "legal/release-assets-v2.json")):
            _require(raw[key] == (root / relative).read_bytes(), "live control override is not the current owner")
            snapshot = subprocess.run(["git", "show", f"a9b3282d1e41539ee650fbe24b0801254613ada4:{relative}"],
                                      cwd=root, check=True, stdout=subprocess.PIPE).stdout
            _require(hashlib.sha256(snapshot).hexdigest() == CURRENT_RELEASE_PROTECTED_SHA256[relative],
                     "historical control snapshot hash mismatch")
            raw[key] = snapshot
            documents[key] = json.loads(snapshot)
    _verify_protected(root, paths, raw)
    consolidation = documents["consolidation"]
    _verify_document(consolidation, documents, raw)
    runner = feasibility_runner or _generate_feasibility
    _verify_feasibility(root, paths, raw["source-kit-feasibility"], runner)
    _verify_docs(paths["readme"], paths["legal-readme"], paths["feasibility-doc"])
    _verify_artifacts(root, tracked_paths, repository_files, introduced_paths)
    _hygiene(consolidation, "consolidation")
    current_owner = root / source_compliance.OWNER_PATH
    if current_owner.is_file():
        # Phase 6B2 may evolve the live gate and bundle implementation without
        # changing this historical consolidation record. A valid later-phase
        # owner supersedes only the obsolete current-script ownership check;
        # the consolidation's protected hashes and semantic checks above still
        # validate the prior evidence and decision.
        source_compliance.load_owner(current_owner)
    else:
        _verify_prior_owner(
            root, paths, tracked_paths, repository_files, introduced_paths, runner,
        )


def _paths(root: Path, overrides: dict[str, Path]) -> dict[str, Path]:
    paths = {key: root / relative for key, relative in PATHS.items()}
    _require(not (set(overrides) - set(paths)), "unknown override")
    paths.update(overrides)
    return paths


def _verify_protected(
    root: Path, paths: dict[str, Path], raw: dict[str, bytes],
) -> None:
    for relative in AUTHORITATIVE_ROLES:
        key = RELATIVE_TO_KEY[relative]
        expected_sha256 = CURRENT_RELEASE_PROTECTED_SHA256.get(relative)
        if expected_sha256 is not None:
            _require(hashlib.sha256(raw[key]).hexdigest() == expected_sha256, f"protected input changed: {relative}")
        else:
            _require(raw[key] == _git_blob(root, relative), f"protected input changed: {relative}")
    _require((root / "VERSION").read_text(encoding="utf-8").strip() == "1.3.2", "VERSION changed")


def _verify_document(
    document: dict[str, Any], inputs: dict[str, dict[str, Any]], raw: dict[str, bytes],
) -> None:
    _keys(document, TOP_KEYS, "top-level")
    _require(document["schema_version"] == 1, "schema version changed")
    _require(document["target_phase"] == "6B2B2A3", "phase changed")
    _require(document["baseline_commit"] == BASELINE, "baseline changed")
    _verify_scope(document["evidence_scope"])
    _verify_authoritative(document["authoritative_inputs"], raw)
    _verify_packages(document["packages"])
    _verify_dispositions(document["input_disposition"], inputs["source-input-inventory"])
    _verify_cross_package(document["cross_package_summary"])
    _verify_materials(document["package_material_disposition"])
    _verify_decision(document["assembly_decision"])
    _verify_prerequisites(document["reconsideration_prerequisites"])
    _verify_gates(document["gate_state"], inputs)


def _verify_scope(record: dict[str, Any]) -> None:
    _keys(record, SCOPE_KEYS, "evidence scope")
    _require(record["mode"] == "offline", "scope is not offline")
    for key in SCOPE_KEYS[1:8]:
        _require(record[key] is False, f"scope permission enabled: {key}")
    _require(record["consolidation_only"] is True, "scope is not consolidation-only")
    boundary = record["claims_boundary"].casefold()
    for phrase in (
        "consolidates accepted evidence", "blocked assembly decision",
        "does not establish legal compliance", "source-kit completeness",
    ):
        _require(phrase in boundary, f"claims boundary missing: {phrase}")


def _verify_authoritative(records: Any, raw: dict[str, bytes]) -> None:
    _require(isinstance(records, list) and len(records) == len(AUTHORITATIVE_ROLES), "authoritative input count changed")
    paths = []
    for record in records:
        _keys(record, AUTHORITATIVE_KEYS, "authoritative input")
        path = record["path"]
        _require(path in AUTHORITATIVE_ROLES, f"unknown authoritative input: {path}")
        _require(record["role"] == AUTHORITATIVE_ROLES[path], f"authoritative role changed: {path}")
        _require(record["status"] == "accepted-input", f"authoritative status changed: {path}")
        _require(record["sha256"] == _sha256(raw[RELATIVE_TO_KEY[path]]), f"authoritative hash changed: {path}")
        paths.append(path)
    _require(paths == sorted(AUTHORITATIVE_ROLES), "authoritative inputs unsorted or incomplete")
    _require(len(paths) == len(set(paths)), "duplicate authoritative input")


def _verify_packages(records: Any) -> None:
    _require(isinstance(records, list) and [item.get("package_id") for item in records] == ["aria2", "ffmpeg"], "package order changed")
    expected = {
        "aria2": (6, 5, 1, 0, 0),
        "ffmpeg": (55, 0, 0, 50, 5),
    }
    required_blocker_terms = {
        "aria2": ("aria2/gmp", "toolchain", "build orchestration", "source assets", "source kit", "legal or release approval"),
        "ffmpeg": ("50 static", "5 system-component", "compiler", "package repository snapshot", "configure", "dependency acquisition", "historical provider recipe", "patch", "reproducible", "source assets", "source kit", "legal or release approval"),
    }
    for record in records:
        package_id = record["package_id"]
        _keys(record, PACKAGE_KEYS, f"package {package_id}")
        values = expected[package_id]
        actual = tuple(record[key] for key in (
            "external_components_total", "verified_immutable_inputs", "partial_inputs",
            "identified_name_only_inputs", "system_component_candidates",
        ))
        _require(actual == values, f"package counts changed: {package_id}")
        _require(record["evidence_sources"] == sorted(set(record["evidence_sources"])), f"evidence sources unsorted: {package_id}")
        _require(set(record["evidence_sources"]).issubset(AUTHORITATIVE_ROLES), f"unknown package evidence: {package_id}")
        _require(record["component_level_evidence_coverage_complete"] is True, f"component evidence coverage changed: {package_id}")
        for key in (
            "provider_versions_complete", "immutable_component_inputs_complete",
            "toolchain_complete", "configure_complete", "build_orchestration_complete",
            "patch_evidence_complete", "reproducibility_complete", "source_assets_created",
            "source_kit_ready", "assembly_eligible",
        ):
            _require(record[key] is False, f"package readiness promoted: {package_id}/{key}")
        _require(record["package_status"] == "blocked", f"package status changed: {package_id}")
        _blockers(record["blockers"], f"package {package_id}")
        joined = " ".join(record["blockers"]).casefold()
        for term in required_blocker_terms[package_id]:
            _require(term in joined, f"package blocker missing: {package_id}/{term}")


def _verify_dispositions(records: Any, inventory: dict[str, Any]) -> None:
    _require(isinstance(records, list), "input dispositions are not an array")
    _require(tuple(item.get("disposition_code") for item in records) == DISPOSITION_ORDER, "disposition order changed")
    observed: list[tuple[str, str]] = []
    for record in records:
        code = record["disposition_code"]
        _keys(record, DISPOSITION_KEYS, f"disposition {code}")
        actual_items = []
        for item in record["items"]:
            _keys(item, ITEM_KEYS, f"disposition item {code}")
            actual_items.append((item["package_id"], item["component_id"]))
        _require(tuple(actual_items) == EXPECTED_ITEMS[code], f"disposition items changed: {code}")
        _require(record["count"] == len(actual_items), f"disposition count changed: {code}")
        for field in ("evidence_meaning", "blocking_effect", "required_next_evidence"):
            _require(isinstance(record[field], str) and record[field], f"disposition text missing: {code}/{field}")
        observed.extend(actual_items)
    _require(len(observed) == 61 and len(set(observed)) == 61, "61-input disposition union failed")
    _require(("aria2", "gmp") in observed and ("ffmpeg", "gmp") in observed, "package-scoped GMP identities collapsed")
    _require(("aria2", "zlib") in observed and ("ffmpeg", "zlib") in observed, "package-scoped zlib identities collapsed")
    _require(("aria2", "libssh2") in observed and ("ffmpeg", "libssh") in observed, "SSH component identities collapsed")
    inventory_expected = set()
    for package in inventory.get("packages", []):
        package_id = package.get("id")
        for component in package.get("external_components", []):
            inventory_expected.add((package_id, component.get("id")))
    _require(inventory_expected == set(observed), "inventory disposition omissions or additions")
    aria2 = _inventory_package(inventory, "aria2")
    ffmpeg = _inventory_package(inventory, "ffmpeg")
    _require(tuple(sorted(item["id"] for item in aria2["external_components"] if item["resolution_status"] == "verified-immutable-input")) == VERIFIED_ARIA2, "verified aria2 inputs changed")
    _require(tuple(sorted(item["id"] for item in aria2["external_components"] if item["resolution_status"] != "verified-immutable-input")) == PARTIAL_ARIA2, "partial aria2 inputs changed")
    _require(tuple(sorted(item["id"] for item in ffmpeg["external_components"] if item["resolution_status"] == "identified-name-only")) == FFMPEG_STATIC, "FFmpeg static disposition changed")
    _require(tuple(sorted(item["id"] for item in ffmpeg["external_components"] if item["resolution_status"] == "system-component-candidate")) == FFMPEG_SYSTEM, "FFmpeg system disposition changed")


def _verify_cross_package(record: dict[str, Any]) -> None:
    _keys(record, SUMMARY_KEYS, "cross-package summary")
    expected = {
        "packages_total": 2, "external_components_total": 61,
        "verified_immutable_inputs": 5, "partial_inputs": 1,
        "identified_name_only_inputs": 50, "system_component_candidates": 5,
        "disposition_union_total": 61, "duplicate_dispositions": 0,
        "omitted_inputs": 0, "all_required_inputs_resolved": False,
        "source_assets_created": False, "source_kits_ready": False,
    }
    _require(record == expected, "cross-package summary changed")


def _verify_materials(records: Any) -> None:
    _require(isinstance(records, list) and [item.get("package_id") for item in records] == ["aria2", "ffmpeg"], "material package order changed")
    for record in records:
        package_id = record["package_id"]
        _keys(record, MATERIAL_PACKAGE_KEYS, f"materials {package_id}")
        _require(record["complete"] is False, f"package materials completed: {package_id}")
        _blockers(record["blockers"], f"material blockers {package_id}")
        _require(tuple(item.get("material") for item in record["materials"]) == MATERIAL_ORDER, f"material order changed: {package_id}")
        statuses = []
        for material in record["materials"]:
            _keys(material, MATERIAL_KEYS, f"material {package_id}/{material.get('material')}")
            statuses.append(material["status"])
            _require(material["status"] in ALLOWED_MATERIAL_STATUS, "invalid material status")
            evidence = material["accepted_evidence"]
            _require(isinstance(evidence, list) and evidence == sorted(set(evidence)) and bool(evidence), "material evidence invalid")
            _require(set(evidence).issubset(AUTHORITATIVE_ROLES), "unknown material evidence")
            _require(isinstance(material["unresolved_requirement"], str) and material["unresolved_requirement"], "material requirement missing")
            _require(material["assembly_effect"] == ASSEMBLY_EFFECT, "material assembly effect changed")
        _require(tuple(statuses) == MATERIAL_STATUS[package_id], f"material statuses changed: {package_id}")


def _verify_decision(record: dict[str, Any]) -> None:
    _keys(record, DECISION_KEYS, "assembly decision")
    _require(record["decision"] == "do-not-assemble", "assembly decision changed")
    _require(record["decision_status"] == "blocked", "assembly decision status changed")
    _require(record["source_asset_assembly_authorized"] is False, "source-asset assembly authorized")
    _require(record["source_kit_assembly_authorized"] is False, "source-kit assembly authorized")
    _require(record["packages_authorized"] == [], "package authorized")
    _require(record["packages_blocked"] == ["aria2", "ffmpeg"], "blocked package set changed")
    _require(tuple(record["decision_basis"]) == DECISION_BASIS, "decision basis changed")
    _require(tuple(record["prohibited_actions"]) == PROHIBITED_ACTIONS, "prohibited actions changed")
    _require(tuple(record["next_permitted_work"]) == NEXT_PERMITTED, "next permitted work changed")


def _verify_prerequisites(records: Any) -> None:
    _require(isinstance(records, list) and len(records) == 10, "prerequisite count changed")
    for index, (record, expected) in enumerate(zip(records, PREREQUISITES), 1):
        _keys(record, PREREQUISITE_KEYS, f"prerequisite {index}")
        _require(record["order"] == index, f"prerequisite order changed: {index}")
        _require((record["prerequisite"], record["owning_package"]) == expected, f"prerequisite changed: {index}")
        _require(record["status"] == "incomplete", f"prerequisite completed: {index}")
        _require(record["blocks_assembly"] is True, f"prerequisite no longer blocks: {index}")
        _require(isinstance(record["evidence_required"], str) and record["evidence_required"], f"prerequisite evidence missing: {index}")


def _verify_gates(record: dict[str, Any], inputs: dict[str, dict[str, Any]]) -> None:
    _keys(record, GATE_KEYS, "gate state")
    _require(all(record[key] is False for key in GATE_KEYS), "consolidation gate promoted")
    policy = inputs["release-policy"]
    assets = inputs["release-assets"]
    _require(policy.get("policy_mode") == "fail-closed", "release policy opened")
    _require(len(policy.get("releases", [])) == 5 and all(item.get("status") == "blocked" for item in policy["releases"]), "release status opened")
    _require(assets.get("release_readiness") == "blocked", "release assets opened")


def _verify_feasibility(
    root: Path, paths: dict[str, Path], raw: bytes, runner: FeasibilityRunner,
) -> None:
    _require(raw == _git_blob(root, "legal/source-kit-feasibility.json"), "feasibility bytes changed")
    _require(_sha256(raw) == FEASIBILITY_HASH, "feasibility hash changed")
    first, second = runner(root, paths), runner(root, paths)
    _require(first == second == raw, "feasibility regeneration differs")


def _generate_feasibility(root: Path, paths: dict[str, Path]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="s9h-readiness-feasibility-") as raw:
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


def _verify_docs(readme: Path, legal_readme: Path, detail: Path) -> None:
    docs = {
        "README": _read_doc(readme), "legal README": _read_doc(legal_readme),
        "feasibility document": _read_doc(detail),
    }
    combined = "\n".join(docs.values()).casefold()
    phrases = (
        "phase 6b2b2a3", "source-kit readiness consolidation",
        "all accepted evidence was consolidated offline",
        "no new primary-source research occurred",
        "aria2 has 5 verified immutable inputs and 1 partial input",
        "ffmpeg has 55/55 component-level evidence coverage",
        "ffmpeg has 0 verified provider-selected immutable component inputs",
        "50 ffmpeg static inputs remain provider-version-unresolved",
        "5 ffmpeg system candidates require explicit final treatment",
        "evidence coverage is not source-kit readiness",
        "verified individual inputs do not make a package source kit ready",
        "aria2 remains blocked", "ffmpeg remains blocked",
        "no source assets exist", "no source kits exist", "assembly must not begin",
        "release-gate reconsideration remains prohibited", "publishing remains blocked",
        "existing releases are not retroactively certified", "not legal advice",
        "source-kit-readiness-consolidation.json",
        "does not replace the underlying evidence",
    )
    for phrase in phrases:
        _require(phrase in combined, f"documentation missing: {phrase}")
    detailed = docs["feasibility document"].casefold()
    for phrase in (
        "external inputs: 61", "verified immutable inputs: 5", "partial inputs: 1",
        "provider-version-unresolved inputs: 50",
        "system-interface-review-required inputs: 5",
        "all required inputs resolved: false", "source assets created: false",
        "source kits ready: false", "assembly authorized: false",
        "release gate reconsideration allowed: false", "publishing allowed: false",
        "ten reconsideration prerequisites", "completed prerequisites: 0",
    ):
        _require(phrase in detailed, f"documentation status missing: {phrase}")
    for label, text in docs.items():
        _hygiene(text, label)
        _require(not any(pattern.search(text) for pattern in UNSUPPORTED_DOC_RES), f"unsupported documentation claim: {label}")


def _verify_artifacts(
    root: Path, tracked_paths: list[str] | None, repository_files: list[str] | None,
    introduced_paths: list[str] | None,
) -> None:
    tracked = tracked_paths if tracked_paths is not None else _git_lines(root, ["ls-files"])
    _require(not any(_suffix(path.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for path in tracked), "tracked archive or installer")
    introduced = introduced_paths if introduced_paths is not None else sorted(_changed_paths(root))
    _require(not any(_suffix(path.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES + BINARY_MEDIA_SUFFIXES) for path in introduced), "forbidden introduced file")
    _require(not any(path.replace("\\", "/").casefold().startswith(SOURCE_TREE_PREFIXES) for path in introduced), "source tree introduced")
    if repository_files is None:
        repository_files = [
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
            and "__pycache__" not in path.parts and path.name not in OLD_REPORTS
        ]
    repository_files = verify_release_legal_gate.exclude_verified_release_inputs(root, repository_files)
    _require(not any(_suffix(path.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for path in repository_files), "archive or installer in repository")


def _verify_prior_owner(
    root: Path, paths: dict[str, Path], tracked_paths: list[str] | None,
    repository_files: list[str] | None, introduced_paths: list[str] | None,
    runner: FeasibilityRunner,
) -> None:
    try:
        prior_verifier.verify_repository(
            root,
            overrides={
                "build-evidence": paths["ffmpeg-build-feasibility"],
                "aria2-primary-evidence": paths["aria2-primary-evidence"],
                "codec-primary-evidence": paths["codec-primary-evidence"],
                "support-primary-evidence": paths["support-primary-evidence"],
                "hardware-system-primary-evidence": paths["hardware-system-primary-evidence"],
                "remaining-primary-evidence": paths["remaining-primary-evidence"],
                "source-correspondence": paths["source-correspondence"],
                "source-input-inventory": paths["source-input-inventory"],
                "source-kit-feasibility": paths["source-kit-feasibility"],
                "source-kit-requirements": paths["source-kit-requirements"],
                "release-policy": paths["release-policy"],
                "release-assets": paths["release-assets"],
                "readme": paths["readme"], "legal-readme": paths["legal-readme"],
                "feasibility-doc": paths["feasibility-doc"],
            },
            tracked_paths=tracked_paths, repository_files=repository_files,
            introduced_paths=introduced_paths, feasibility_runner=runner,
        )
    except prior_verifier.FFmpegProviderBuildFeasibilityError as exc:
        raise SourceKitReadinessConsolidationError(f"prior owner rejected fixture: {exc}") from exc


def _changed_paths(root: Path) -> set[str]:
    changed = set(_git_lines(root, ["diff", "--name-only", BASELINE, "--"]))
    for line in subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.splitlines():
        if line.startswith("?? ") and line[3:].replace("\\", "/") not in OLD_REPORTS:
            changed.add(line[3:].replace("\\", "/"))
    return changed


def _inventory_package(document: dict[str, Any], package_id: str) -> dict[str, Any]:
    found = [item for item in document.get("packages", []) if item.get("id") == package_id]
    _require(len(found) == 1, f"inventory package missing or duplicate: {package_id}")
    return found[0]


def _blockers(value: Any, label: str) -> None:
    _require(
        isinstance(value, list) and bool(value) and value == sorted(set(value))
        and all(isinstance(item, str) and item for item in value),
        f"invalid blockers: {label}",
    )


def _keys(value: Any, expected: tuple[str, ...], label: str) -> None:
    _require(isinstance(value, dict) and tuple(value) == expected, f"invalid schema/order: {label}")


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
    return subprocess.run(
        ["git", "show", f"{BASELINE}:{relative}"], cwd=root, check=True,
        stdout=subprocess.PIPE,
    ).stdout


def _git_lines(root: Path, arguments: list[str]) -> list[str]:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _suffix(path: str, suffixes: tuple[str, ...]) -> bool:
    return any(path.endswith(suffix) for suffix in suffixes)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SourceKitReadinessConsolidationError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
