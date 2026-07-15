from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import verify_ffmpeg_codec_primary_source_evidence as codec_verifier
import verify_ffmpeg_hardware_system_evidence as hardware_verifier
import verify_ffmpeg_support_primary_source_evidence as support_verifier


BASELINE = "840b8faec9f8436f3474c8d07bc0b59bec594b13"
PACKAGE_HASH = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
PROVIDER_RELEASE = "8.1.2-essentials_build-www.gyan.dev"
CORE_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"

IDS = (
    "avisynth", "bzlib", "gmp", "gnutls", "libsrt", "libssh",
    "libvidstab", "libvmaf", "libzmq", "lzma", "zlib",
)
NATURES = {
    "avisynth": "scripting-integration",
    "bzlib": "compression-library",
    "gmp": "arithmetic-library",
    "gnutls": "tls-crypto-library",
    "libsrt": "network-transport-library",
    "libssh": "secure-shell-library",
    "libvidstab": "video-processing-library",
    "libvmaf": "quality-analysis-library",
    "libzmq": "messaging-library",
    "lzma": "compression-library",
    "zlib": "compression-library",
}
AUTHORITIES = {
    "avisynth": "AviSynth project",
    "bzlib": "bzip2 project",
    "gmp": "GNU GMP project",
    "gnutls": "GnuTLS project",
    "libsrt": "Haivision SRT project",
    "libssh": "libssh project",
    "libvidstab": "vid.stab project",
    "libvmaf": "Netflix VMAF project",
    "libzmq": "ZeroMQ project",
    "lzma": "XZ Utils project",
    "zlib": "zlib project",
}
PROJECTS = {
    "avisynth": "AviSynth integration API and AviSynth+",
    "bzlib": "bzip2 and libbzip2",
    "gmp": "GNU Multiple Precision Arithmetic Library",
    "gnutls": "GnuTLS secure communications library",
    "libsrt": "Secure Reliable Transport (SRT)",
    "libssh": "libssh SSH protocol library",
    "libvidstab": "vid.stab video stabilization library",
    "libvmaf": "VMAF and libvmaf video quality analysis",
    "libzmq": "ZeroMQ libzmq messaging library",
    "lzma": "XZ Utils and liblzma",
    "zlib": "zlib DEFLATE compression library",
}
LOCATORS = {
    "avisynth": "https://github.com/AviSynth/AviSynthPlus",
    "bzlib": "https://sourceware.org/bzip2/",
    "gmp": "https://gmplib.org/",
    "gnutls": "https://www.gnutls.org/",
    "libsrt": "https://github.com/Haivision/srt",
    "libssh": "https://www.libssh.org/",
    "libvidstab": "https://github.com/georgmartius/vid.stab",
    "libvmaf": "https://github.com/Netflix/vmaf",
    "libzmq": "https://github.com/zeromq/libzmq",
    "lzma": "https://tukaani.org/xz/",
    "zlib": "https://zlib.net/",
}
LICENSE_LOCATORS = {
    **LOCATORS,
    "avisynth": "https://avisynthplus.readthedocs.io/en/latest/avisynthdoc/license.html",
    "bzlib": "https://sourceware.org/bzip2/manual/manual.html",
    "zlib": "https://zlib.net/zlib_license.html",
}
TREATMENTS = {
    cid: (
        "source-or-sdk-input-requires-version-resolution"
        if cid == "avisynth" else
        "source-archive-required-if-version-resolved"
    )
    for cid in IDS
}
ARCHIVE_APPLICABILITY = {
    cid: (
        "source-or-sdk-input-if-version-resolved"
        if cid == "avisynth" else
        "required-if-version-resolved"
    )
    for cid in IDS
}
INTERPRETATION_TERMS = {
    "avisynth": ("AviSynth integration API", "AviSynth+", "remain unresolved"),
    "bzlib": ("bzip2 compression support", "provider-selected bzip2 version", "unresolved"),
    "gmp": ("GNU GMP", "aria2 GMP evidence", "not reused"),
    "gnutls": ("GnuTLS support", "provider-selected version", "unresolved"),
    "libsrt": ("Secure Reliable Transport", "provider-selected version", "unresolved"),
    "libssh": ("libssh, not libssh2", "aria2 libssh2 evidence", "not reused"),
    "libvidstab": ("vid.stab", "provider-selected version", "unresolved"),
    "libvmaf": ("VMAF", "provider-selected version", "unresolved"),
    "libzmq": ("ZeroMQ", "libzmq", "provider-selected version"),
    "lzma": ("liblzma", "XZ compression support", "unresolved"),
    "zlib": ("zlib", "aria2 zlib evidence", "not reused"),
}

TOP_KEYS = (
    "schema_version", "target_phase", "baseline_commit", "package_id",
    "binary_package_sha256", "provider_release_identity", "ffmpeg_core_commit",
    "research_policy", "provider_build_evidence", "components", "summary",
    "coverage_completion", "gate_state",
)
RESEARCH_KEYS = (
    "authority_scope", "network_mode", "source_archive_scope",
    "sdk_installer_downloads_allowed", "binary_downloads_allowed",
    "runtime_execution_allowed",
)
BUILD_KEYS = (
    "exact_package_identified", "exact_historical_recipe_identified",
    "exact_dependency_versions_identified", "exact_toolchain_identified",
    "exact_configure_command_identified", "patch_set_identified", "evidence",
    "blockers",
)
COMPONENT_KEYS = (
    "id", "provider_linkage", "component_nature",
    "provider_label_interpretation", "provider_version",
    "provider_version_status", "official_authority",
    "official_project_or_interface", "official_repository_or_documentation",
    "release_identity", "source_archive", "license_evidence",
    "provider_to_official_mapping", "source_kit_treatment",
    "resolution_status", "evidence", "blockers",
)
IDENTITY_KEYS = ("kind", "value", "secondary_identity", "resolution_method")
ARCHIVE_KEYS = (
    "applicability", "filename", "official_locator", "sha256",
    "independently_hashed", "upstream_checksum_status", "signature_status",
)
LICENSE_KEYS = ("classification", "source_path_or_locator", "status", "claim")
EVIDENCE_KEYS = ("kind", "authority", "locator", "claim", "status")
SUMMARY_KEYS = (
    "total_components", "static_components", "provider_versions_verified",
    "verified_immutable_inputs", "partial_inputs", "identified_name_only",
    "unresolved_inputs", "archive_hashes_verified", "exact_provider_recipe_found",
    "all_batch_inputs_resolved", "source_kit_assembly_authorized",
    "remaining_blockers",
)
COVERAGE_KEYS = (
    "ffmpeg_external_components_total", "prior_codec_batch",
    "prior_support_batch", "prior_hardware_system_batch",
    "current_remaining_batch", "total_components_with_dedicated_evidence",
    "component_level_coverage_complete", "provider_versions_complete",
    "toolchain_complete", "build_orchestration_complete", "source_kit_complete",
)
GATE_KEYS = (
    "legal_compliance_certified", "source_availability_certified",
    "source_assets_created", "source_kits_ready", "assembly_authorized",
    "release_gate_reconsideration_allowed", "publishing_allowed",
)
INVENTORY_KEYS = (
    "id", "linkage", "provider_version", "version_status",
    "upstream_repository", "immutable_ref", "source_archive_sha256",
    "evidence", "resolution_status", "blockers",
)
PRIMARY_EVIDENCE_KINDS = (
    "functional-classification", "licensing-context",
    "official-upstream-research", "provider-label-interpretation",
    "provider-mapping-gap", "provider-package-metadata", "provider-version-gap",
    "source-kit-treatment",
)
INVENTORY_EVIDENCE_KINDS = (
    "functional-classification", "official-upstream-research",
    "provider-mapping-gap", "provider-package-metadata", "provider-version-gap",
    "source-kit-treatment",
)
COMMON_BLOCKERS = {
    "Exact provider component version is unresolved.",
    "Exact provider-to-upstream mapping is unresolved.",
    "Immutable provider input cannot be selected without provider-version evidence.",
    "Provider historical build recipe does not prove static incorporation.",
    "Source archive identity and independent SHA-256 are unresolved.",
}
EXTRA_BLOCKERS = {
    "avisynth": {"Exact AviSynth implementation and API version are unresolved."},
    "bzlib": {"Exact mapping from provider label bzlib to the provider-selected bzip2 input is unresolved."},
    "gmp": {
        "Aria2 GMP evidence is package-scoped and non-transferable to FFmpeg.",
        "Package-scoped FFmpeg GMP version is unresolved.",
    },
    "libssh": {
        "Aria2 libssh2 evidence is package-scoped and cannot be reused for FFmpeg libssh.",
        "Provider component libssh is distinct from aria2 libssh2.",
    },
    "lzma": {"Exact liblzma or XZ provider version is unresolved."},
    "zlib": {
        "Aria2 zlib evidence is package-scoped and non-transferable to FFmpeg.",
        "Package-scoped FFmpeg zlib version is unresolved.",
    },
}
PROTECTED = {
    "codec-primary-evidence": "legal/primary-source-evidence-ffmpeg-codecs.json",
    "support-primary-evidence": "legal/primary-source-evidence-ffmpeg-support.json",
    "hardware-system-primary-evidence": "legal/primary-source-evidence-ffmpeg-hardware-system.json",
    "aria2-primary-evidence": "legal/primary-source-evidence-aria2.json",
    "source-correspondence": "legal/source-correspondence.json",
    "source-kit-requirements": "legal/source-kit-requirements.json",
    "release-policy": "legal/release-policy.json",
    "release-assets": "legal/release-assets-v2.json",
}
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
GITHUB_ROOTS = {
    "/avisynth/avisynthplus", "/haivision/srt", "/georgmartius/vid.stab",
    "/netflix/vmaf", "/zeromq/libzmq",
}
OTHER_HOSTS = {
    "avisynthplus.readthedocs.io", "gmplib.org", "sourceware.org",
    "www.gnutls.org", "www.gyan.dev", "www.libssh.org", "tukaani.org",
    "zlib.net",
}
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
TIMESTAMP_RE = re.compile(r"\b20\d\d-\d\d-\d\d(?:[T ][0-2]\d:[0-5]\d)?\b")
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)
UNSUPPORTED_RES = (
    re.compile(r"(?i)\bsource kits? (?:is|are) (?:complete|ready)\b"),
    re.compile(r"(?i)\bsource kit complete\s*:\s*true\b"),
    re.compile(r"(?i)\bcorresponding source (?:is )?complete\b"),
    re.compile(r"(?i)\blegal compliance (?:is )?certified\b"),
    re.compile(r"(?i)\brelease (?:is )?(?:approved|ready)\b"),
    re.compile(r"(?i)\bbuild (?:is )?reproducible\b"),
    re.compile(r"(?i)\bprovider recipe (?:is )?complete\b"),
)

FeasibilityRunner = Callable[[Path, dict[str, Path]], bytes]


class FFmpegRemainingLibraryEvidenceError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify offline FFmpeg remaining-library primary-source evidence"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    for option in (
        "primary-evidence", "codec-primary-evidence", "support-primary-evidence",
        "hardware-system-primary-evidence", "aria2-primary-evidence",
        "source-correspondence", "source-input-inventory",
        "source-kit-feasibility", "source-kit-requirements", "release-policy",
        "release-assets", "readme", "legal-readme", "feasibility-doc",
    ):
        parser.add_argument(f"--{option}", type=Path)
    args = parser.parse_args()
    overrides = {
        key.replace("_", "-"): value for key, value in vars(args).items()
        if key != "root" and value is not None
    }
    try:
        verify_repository(args.root, overrides=overrides)
    except (
        FFmpegRemainingLibraryEvidenceError, OSError, UnicodeError,
        json.JSONDecodeError, subprocess.SubprocessError,
    ) as exc:
        print(f"FFmpeg remaining-library evidence verification failed: {exc}", file=sys.stderr)
        return 1
    print("FFmpeg remaining-library evidence verified")
    return 0


def verify_repository(
    root: Path,
    *,
    overrides: dict[str, Path] | None = None,
    tracked_paths: list[str] | None = None,
    repository_files: list[str] | None = None,
    introduced_paths: list[str] | None = None,
    feasibility_runner: FeasibilityRunner | None = None,
) -> None:
    root = root.resolve()
    _require(root.is_dir(), "repository root is unavailable")
    paths = _paths(root, overrides or {})
    primary, _ = _load_json(paths["primary-evidence"], "primary evidence")
    codec, _ = _load_json(paths["codec-primary-evidence"], "codec evidence")
    support, _ = _load_json(paths["support-primary-evidence"], "support evidence")
    hardware, _ = _load_json(paths["hardware-system-primary-evidence"], "hardware/system evidence")
    aria2, _ = _load_json(paths["aria2-primary-evidence"], "aria2 evidence")
    correspondence, _ = _load_json(paths["source-correspondence"], "source correspondence")
    inventory, _ = _load_json(paths["source-input-inventory"], "source input inventory")
    feasibility, feasibility_raw = _load_json(paths["source-kit-feasibility"], "source-kit feasibility")
    _load_json(paths["source-kit-requirements"], "source-kit requirements")
    policy, _ = _load_json(paths["release-policy"], "release policy")
    assets, _ = _load_json(paths["release-assets"], "release assets")
    _verify_protected(root, paths)
    _verify_prior_owners(root, paths, tracked_paths, repository_files, introduced_paths)
    _verify_primary(primary, correspondence)
    _verify_inventory(root, primary, inventory)
    _verify_package_scoped_identity(root, primary, inventory, aria2)
    _verify_coverage(primary, codec, support, hardware, inventory)
    _verify_feasibility(
        root, paths, feasibility, feasibility_raw,
        feasibility_runner or _generate_feasibility,
    )
    _verify_release(policy, assets, feasibility)
    _verify_docs(paths["readme"], paths["legal-readme"], paths["feasibility-doc"])
    _verify_artifacts(root, tracked_paths, repository_files, introduced_paths)


def _paths(root: Path, overrides: dict[str, Path]) -> dict[str, Path]:
    paths = {
        "primary-evidence": root / "legal/primary-source-evidence-ffmpeg-remaining-libraries.json",
        "codec-primary-evidence": root / "legal/primary-source-evidence-ffmpeg-codecs.json",
        "support-primary-evidence": root / "legal/primary-source-evidence-ffmpeg-support.json",
        "hardware-system-primary-evidence": root / "legal/primary-source-evidence-ffmpeg-hardware-system.json",
        "aria2-primary-evidence": root / "legal/primary-source-evidence-aria2.json",
        "source-correspondence": root / "legal/source-correspondence.json",
        "source-input-inventory": root / "legal/source-input-inventory.json",
        "source-kit-feasibility": root / "legal/source-kit-feasibility.json",
        "source-kit-requirements": root / "legal/source-kit-requirements.json",
        "release-policy": root / "legal/release-policy.json",
        "release-assets": root / "legal/release-assets-v2.json",
        "readme": root / "README.md",
        "legal-readme": root / "legal/README.md",
        "feasibility-doc": root / "docs/source-kit-feasibility.md",
    }
    _require(not (set(overrides) - set(paths)), "unknown path override")
    paths.update(overrides)
    return paths


def _verify_protected(root: Path, paths: dict[str, Path]) -> None:
    for key, relative in PROTECTED.items():
        _require(paths[key].read_bytes() == _git_blob(root, relative), f"protected file changed: {relative}")
    _require((root / "VERSION").read_text(encoding="utf-8").strip() == "1.3.1", "VERSION changed")


def _verify_prior_owners(
    root: Path,
    paths: dict[str, Path],
    tracked_paths: list[str] | None,
    repository_files: list[str] | None,
    introduced_paths: list[str] | None,
) -> None:
    # Prior component verifiers own their baseline documentation contracts.
    # New phase wording is checked below by this verifier, not retroactively by them.
    with tempfile.TemporaryDirectory(prefix="s9h-prior-owner-docs-") as raw:
        prior_docs: dict[str, Path] = {}
        for key, relative in (
            ("readme", "README.md"),
            ("legal-readme", "legal/README.md"),
            ("feasibility-doc", "docs/source-kit-feasibility.md"),
        ):
            target = Path(raw) / key
            target.write_bytes(_git_blob(root, relative))
            prior_docs[key] = target
        prior_inventory = _load_json_bytes(
            paths["source-input-inventory"].read_bytes(), "current inventory"
        )
        baseline_inventory = _load_json_bytes(
            _git_blob(root, "legal/source-input-inventory.json"),
            "baseline inventory",
        )
        prior_ffmpeg = next(
            item for item in prior_inventory["packages"] if item["id"] == "ffmpeg"
        )
        baseline_ffmpeg = next(
            item for item in baseline_inventory["packages"] if item["id"] == "ffmpeg"
        )
        for key in ("toolchain", "build_orchestration"):
            prior_ffmpeg[key] = copy.deepcopy(baseline_ffmpeg[key])
        prior_inventory_path = Path(raw) / "source-input-inventory.json"
        prior_inventory_path.write_bytes(_canonical_json_bytes(prior_inventory))
        try:
            hardware_verifier.verify_repository(
                root,
                overrides={
                    "primary-evidence": paths["hardware-system-primary-evidence"],
                    "codec-primary-evidence": paths["codec-primary-evidence"],
                    "support-primary-evidence": paths["support-primary-evidence"],
                    "aria2-primary-evidence": paths["aria2-primary-evidence"],
                    "source-correspondence": paths["source-correspondence"],
                    "source-input-inventory": prior_inventory_path,
                    "source-kit-feasibility": paths["source-kit-feasibility"],
                    "source-kit-requirements": paths["source-kit-requirements"],
                    "release-policy": paths["release-policy"],
                    "release-assets": paths["release-assets"],
                    **prior_docs,
                },
                tracked_paths=tracked_paths,
                repository_files=repository_files,
                introduced_paths=introduced_paths,
            )
        except hardware_verifier.FFmpegHardwareSystemEvidenceError as exc:
            raise FFmpegRemainingLibraryEvidenceError(
                f"prior component-owner verifier rejected the fixture: {exc}"
            ) from exc


def _verify_primary(document: dict[str, Any], correspondence: dict[str, Any]) -> None:
    _require(tuple(document) == TOP_KEYS, "primary evidence schema or field order is invalid")
    expected_top = {
        "schema_version": 1,
        "target_phase": "6B2B2A1e",
        "baseline_commit": BASELINE,
        "package_id": "ffmpeg",
        "binary_package_sha256": PACKAGE_HASH,
        "provider_release_identity": PROVIDER_RELEASE,
        "ffmpeg_core_commit": CORE_COMMIT,
    }
    for key, value in expected_top.items():
        _require(document[key] == value, f"primary identity changed: {key}")
    package = next(item for item in correspondence["packages"] if item["id"] == "ffmpeg")
    _require(package["binary_package"]["sha256"] == PACKAGE_HASH, "FFmpeg package hash changed")
    _require(package["provider"]["release_identity"] == PROVIDER_RELEASE, "FFmpeg provider changed")
    _require(package["core_source"]["commit"] == CORE_COMMIT, "FFmpeg core changed")
    policy = document["research_policy"]
    _require(isinstance(policy, dict) and tuple(policy) == RESEARCH_KEYS, "research policy schema is invalid")
    _require(policy == {
        "authority_scope": "official-primary-sources-only",
        "network_mode": "read-only",
        "source_archive_scope": "provider-matched-source-only",
        "sdk_installer_downloads_allowed": False,
        "binary_downloads_allowed": False,
        "runtime_execution_allowed": False,
    }, "research policy changed")
    _verify_build(document["provider_build_evidence"])
    components = document["components"]
    _require(
        isinstance(components, list) and [item.get("id") for item in components] == list(IDS),
        "component batch is missing, duplicated, invented, or unsorted",
    )
    source = {item["id"]: item for item in package["external_components"]}
    for component in components:
        _verify_component(component, source[component["id"]])
    _verify_summary(document["summary"])
    _verify_coverage_summary(document["coverage_completion"])
    gate = document["gate_state"]
    _require(isinstance(gate, dict) and tuple(gate) == GATE_KEYS, "gate schema is invalid")
    _require(all(value is False for value in gate.values()), "primary gate was opened")
    _hygiene(document, "primary evidence")


def _verify_build(record: Any) -> None:
    _require(isinstance(record, dict) and tuple(record) == BUILD_KEYS, "provider build schema is invalid")
    expected = {
        "exact_package_identified": True,
        "exact_historical_recipe_identified": False,
        "exact_dependency_versions_identified": False,
        "exact_toolchain_identified": False,
        "exact_configure_command_identified": False,
        "patch_set_identified": False,
    }
    for key, value in expected.items():
        _require(record[key] is value, f"provider build field changed: {key}")
    _evidence(record["evidence"], "provider build")
    _require(_sorted_nonempty(record["blockers"]), "provider build blockers are invalid")
    _require(any("11 remaining components" in item for item in record["blockers"]), "batch version blocker is missing")


def _verify_component(component: Any, source: dict[str, Any]) -> None:
    _require(isinstance(component, dict) and tuple(component) == COMPONENT_KEYS, "component schema is invalid")
    cid = component["id"]
    _require(cid in IDS, f"unexpected component: {cid}")
    _require(source["linkage"] == component["provider_linkage"] == "static", f"linkage changed: {cid}")
    _require(component["component_nature"] == NATURES[cid], f"functional classification changed: {cid}")
    interpretation = component["provider_label_interpretation"]
    _require(isinstance(interpretation, str) and interpretation, f"provider-label interpretation is missing: {cid}")
    for term in INTERPRETATION_TERMS[cid]:
        _require(term in interpretation, f"provider-label interpretation is incomplete: {cid}")
    _require(component["provider_version"] == component["provider_version_status"] == "unresolved", f"provider version was promoted: {cid}")
    _require(component["official_authority"] == AUTHORITIES[cid], f"official authority changed: {cid}")
    _require(component["official_project_or_interface"] == PROJECTS[cid], f"official project changed: {cid}")
    _require(component["official_repository_or_documentation"] == LOCATORS[cid], f"official locator changed: {cid}")
    _official(component["official_repository_or_documentation"], f"{cid} official source")
    identity = component["release_identity"]
    _require(isinstance(identity, dict) and tuple(identity) == IDENTITY_KEYS, f"identity schema is invalid: {cid}")
    _require(identity["kind"] == identity["value"] == "unresolved", f"provider identity was invented: {cid}")
    _require(identity["secondary_identity"] == "not-applicable", f"secondary identity changed: {cid}")
    for phrase in ("Official upstream context was researched", "exact Gyan provider version was not established", "no upstream release or commit is selected"):
        _require(phrase in identity["resolution_method"], f"release identity disclaimer is incomplete: {cid}")
    archive = component["source_archive"]
    _require(isinstance(archive, dict) and tuple(archive) == ARCHIVE_KEYS, f"archive schema is invalid: {cid}")
    _require(archive == {
        "applicability": ARCHIVE_APPLICABILITY[cid],
        "filename": "unresolved",
        "official_locator": "unresolved",
        "sha256": "unresolved",
        "independently_hashed": False,
        "upstream_checksum_status": "unresolved",
        "signature_status": "unresolved",
    }, f"provider archive was invented: {cid}")
    license_record = component["license_evidence"]
    _require(isinstance(license_record, dict) and tuple(license_record) == LICENSE_KEYS, f"license schema is invalid: {cid}")
    _require(license_record["source_path_or_locator"] == LICENSE_LOCATORS[cid], f"license locator changed: {cid}")
    _official(license_record["source_path_or_locator"], f"{cid} license")
    _require(license_record["status"] == "partial", f"license status was promoted: {cid}")
    for phrase in ("Upstream licensing context was identified", "exact provider-selected version remains unresolved", "no final redistribution or compatibility conclusion is made"):
        _require(phrase in license_record["claim"], f"license disclaimer is incomplete: {cid}")
    _require(component["provider_to_official_mapping"] == "unresolved", f"provider mapping was promoted: {cid}")
    _require(component["source_kit_treatment"] == TREATMENTS[cid], f"source-kit treatment changed: {cid}")
    _require(component["resolution_status"] == "identified-name-only", f"resolution was promoted: {cid}")
    _evidence(component["evidence"], cid)
    _require(tuple(item["kind"] for item in component["evidence"]) == PRIMARY_EVIDENCE_KINDS, f"component evidence set changed: {cid}")
    status_by_kind = {item["kind"]: item["status"] for item in component["evidence"]}
    _require(status_by_kind["provider-version-gap"] == status_by_kind["provider-mapping-gap"] == "unresolved", f"provider gap was promoted: {cid}")
    _require(all(status_by_kind[kind] == "partial" for kind in status_by_kind if kind not in {"provider-version-gap", "provider-mapping-gap"}), f"contextual evidence was promoted: {cid}")
    provider_locator = f"legal/source-correspondence.json#packages/ffmpeg/external_components/{cid}"
    _require(any(item["kind"] == "provider-package-metadata" and item["locator"] == provider_locator and "static linkage" in item["claim"] for item in component["evidence"]), f"provider metadata evidence is missing: {cid}")
    _require(any(item["kind"] == "official-upstream-research" and item["locator"] == LOCATORS[cid] and "historical Gyan provider input" in item["claim"] for item in component["evidence"]), f"official upstream context is missing: {cid}")
    _require(any(item["kind"] == "functional-classification" and NATURES[cid] in item["claim"] for item in component["evidence"]), f"functional classification evidence is missing: {cid}")
    _require(any(item["kind"] == "provider-label-interpretation" and item["claim"] == interpretation for item in component["evidence"]), f"provider-label evidence is missing: {cid}")
    _require(any(item["kind"] == "licensing-context" and item["locator"] == LICENSE_LOCATORS[cid] for item in component["evidence"]), f"licensing context is missing: {cid}")
    expected_blockers = sorted(COMMON_BLOCKERS | EXTRA_BLOCKERS.get(cid, set()))
    _require(component["blockers"] == expected_blockers, f"component blockers changed: {cid}")


def _verify_summary(summary: Any) -> None:
    _require(isinstance(summary, dict) and tuple(summary) == SUMMARY_KEYS, "summary schema is invalid")
    expected = {
        "total_components": 11,
        "static_components": 11,
        "provider_versions_verified": 0,
        "verified_immutable_inputs": 0,
        "partial_inputs": 0,
        "identified_name_only": 11,
        "unresolved_inputs": 0,
        "archive_hashes_verified": 0,
        "exact_provider_recipe_found": False,
        "all_batch_inputs_resolved": False,
        "source_kit_assembly_authorized": False,
    }
    for key, value in expected.items():
        _require(summary[key] == value, f"summary value changed: {key}")
    _require(_sorted_nonempty(summary["remaining_blockers"]), "summary blockers are invalid")


def _verify_coverage_summary(coverage: Any) -> None:
    _require(isinstance(coverage, dict) and tuple(coverage) == COVERAGE_KEYS, "coverage schema is invalid")
    expected = {
        "ffmpeg_external_components_total": 55,
        "prior_codec_batch": 16,
        "prior_support_batch": 14,
        "prior_hardware_system_batch": 14,
        "current_remaining_batch": 11,
        "total_components_with_dedicated_evidence": 55,
        "component_level_coverage_complete": True,
        "provider_versions_complete": False,
        "toolchain_complete": False,
        "build_orchestration_complete": False,
        "source_kit_complete": False,
    }
    for key, value in expected.items():
        _require(coverage[key] is value if isinstance(value, bool) else coverage[key] == value, f"coverage value changed: {key}")


def _verify_inventory(root: Path, primary: dict[str, Any], inventory: dict[str, Any]) -> None:
    baseline = _load_json_bytes(_git_blob(root, "legal/source-input-inventory.json"), "baseline inventory")
    for key in ("release_gate_reconsideration_allowed", "legal_compliance_certified", "source_assets_created"):
        _require(inventory[key] is False, f"inventory shared gate changed: {key}")
    _require(all(item["package_status"] == "blocked" for item in inventory["packages"]), "inventory package gate changed")
    current_packages = {item["id"]: item for item in inventory["packages"]}
    original_packages = {item["id"]: item for item in baseline["packages"]}
    _require(set(current_packages) == set(original_packages), "inventory package set changed")
    _require(current_packages["aria2"] == original_packages["aria2"], "aria2 inventory changed")
    current_ffmpeg = current_packages["ffmpeg"]
    original_ffmpeg = original_packages["ffmpeg"]
    for key in ("id", "binary_package_sha256", "core_source", "package_status", "blockers"):
        _require(current_ffmpeg[key] == original_ffmpeg[key], f"FFmpeg package field changed: {key}")
    for key in ("toolchain", "build_orchestration"):
        current = copy.deepcopy(current_ffmpeg[key])
        original = original_ffmpeg[key]
        for refinable in ("evidence", "blockers"):
            current[refinable] = copy.deepcopy(original[refinable])
        _require(current == original, f"FFmpeg {key} structural state changed")
    current_records = {item["id"]: item for item in current_ffmpeg["external_components"]}
    original_records = {item["id"]: item for item in original_ffmpeg["external_components"]}
    _require(set(current_records) == set(original_records), "FFmpeg component set changed")
    primary_records = {item["id"]: item for item in primary["components"]}
    for cid, record in current_records.items():
        if cid not in IDS:
            _require(record == original_records[cid], f"non-batch FFmpeg record changed: {cid}")
            continue
        _require(tuple(record) == INVENTORY_KEYS, f"inventory schema changed: {cid}")
        original = original_records[cid]
        for key in INVENTORY_KEYS:
            if key not in {"evidence", "blockers"}:
                _require(record[key] == original[key], f"inventory structural field changed: {cid}.{key}")
        _require(record["linkage"] == "static", f"inventory linkage changed: {cid}")
        for key in ("provider_version", "version_status", "upstream_repository", "immutable_ref", "source_archive_sha256"):
            _require(record[key] == "unresolved", f"inventory field was promoted: {cid}.{key}")
        _require(record["resolution_status"] == "identified-name-only", f"inventory status was promoted: {cid}")
        expected_evidence = [
            item for item in primary_records[cid]["evidence"]
            if item["kind"] in INVENTORY_EVIDENCE_KINDS
        ]
        _require(record["evidence"] == expected_evidence, f"inventory evidence changed: {cid}")
        _require(record["blockers"] == primary_records[cid]["blockers"], f"inventory blockers changed: {cid}")
        _evidence(record["evidence"], f"inventory {cid}")
        _require(tuple(item["kind"] for item in record["evidence"]) == INVENTORY_EVIDENCE_KINDS, f"inventory evidence set changed: {cid}")
    _hygiene(inventory, "source input inventory")


def _verify_package_scoped_identity(
    root: Path,
    primary: dict[str, Any],
    inventory: dict[str, Any],
    aria2_primary: dict[str, Any],
) -> None:
    packages = {item["id"]: item for item in inventory["packages"]}
    ffmpeg = {item["id"]: item for item in packages["ffmpeg"]["external_components"]}
    aria2 = {item["id"]: item for item in packages["aria2"]["external_components"]}
    _require(aria2["gmp"]["provider_version"] == "6.3.0", "protected aria2 GMP identity changed")
    _require(aria2["zlib"]["provider_version"] == "1.3", "protected aria2 zlib identity changed")
    _require(aria2["libssh2"]["provider_version"] == "1.11.0", "protected aria2 libssh2 identity changed")
    for cid in ("gmp", "zlib"):
        record = ffmpeg[cid]
        _require(record["provider_version"] == record["upstream_repository"] == record["immutable_ref"] == record["source_archive_sha256"] == "unresolved", f"cross-package identity transfer detected: ffmpeg/{cid}")
        _require(any("package-scoped" in item["claim"] for item in record["evidence"]), f"package scope evidence is missing: ffmpeg/{cid}")
    _require(ffmpeg["libssh"]["provider_version"] == ffmpeg["libssh"]["upstream_repository"] == ffmpeg["libssh"]["immutable_ref"] == ffmpeg["libssh"]["source_archive_sha256"] == "unresolved", "aria2 libssh2 identity was copied to FFmpeg libssh")
    _require(any("package-scoped" in item["claim"] for item in ffmpeg["libssh"]["evidence"]), "package scope evidence is missing: ffmpeg/libssh")
    selected = json.dumps({"primary": primary, "records": {cid: ffmpeg[cid] for cid in ("gmp", "zlib", "libssh")}}, ensure_ascii=False)
    forbidden = {
        "1.11.0", "https://github.com/libssh2/libssh2",
        "1c3f1b7da588f2652260285529ec3c1f1125eb4e",
        "a488a22625296342ddae862de1d59633e6d446eff8417398e06674a49be3d7c2",
        "https://github.com/madler/zlib",
        "09155eaa2f9270dc4ed1fa13e2b4b2613e6e4851",
        "8a9ba2898e1d0d774eca6ba5b4627a11e5588ba85c8851336eb38de4683050a7",
    }
    _require(not any(value in selected for value in forbidden), "cross-package version, repository, ref, or hash reuse detected")
    _require(aria2_primary == _load_json_bytes(_git_blob(root, "legal/primary-source-evidence-aria2.json"), "baseline aria2 evidence"), "aria2 primary evidence changed")


def _verify_coverage(
    primary: dict[str, Any],
    codec: dict[str, Any],
    support: dict[str, Any],
    hardware: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    batches = [
        tuple(item["id"] for item in codec["components"]),
        tuple(item["id"] for item in support["components"]),
        tuple(item["id"] for item in hardware["components"]),
        tuple(item["id"] for item in primary["components"]),
    ]
    _require(tuple(map(len, batches)) == (16, 14, 14, 11), "dedicated batch counts changed")
    flattened = [cid for batch in batches for cid in batch]
    _require(len(flattened) == len(set(flattened)) == 55, "dedicated evidence coverage is duplicated or incomplete")
    ffmpeg = next(item for item in inventory["packages"] if item["id"] == "ffmpeg")
    inventory_ids = [item["id"] for item in ffmpeg["external_components"]]
    _require(len(inventory_ids) == len(set(inventory_ids)) == 55, "FFmpeg inventory component IDs are duplicated or incomplete")
    _require(set(flattened) == set(inventory_ids), "dedicated evidence coverage does not match FFmpeg inventory")


def _verify_feasibility(
    root: Path,
    paths: dict[str, Path],
    document: dict[str, Any],
    raw: bytes,
    runner: FeasibilityRunner,
) -> None:
    for key in (
        "release_gate_reconsideration_allowed", "legal_compliance_certified",
        "source_assets_created", "source_kits_ready", "assembly_authorized",
    ):
        _require(document[key] is False, f"feasibility gate changed: {key}")
    _require(document["overall_status"] == "blocked-inventory-recorded", "feasibility was marked ready")
    ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
    _require(ffmpeg["verified_immutable_inputs"] == [], "FFmpeg feasibility input was promoted")
    _require(all(cid in ffmpeg["partially_identified_inputs"] for cid in IDS), "remaining-library batch missing from feasibility")
    _require(ffmpeg["source_kit_status"] == "not-ready", "FFmpeg source kit was marked ready")
    outputs = [runner(root, paths), runner(root, paths)]
    _require(outputs[0] == outputs[1], "feasibility regeneration is non-deterministic")
    _require(outputs[0] == raw, "feasibility regeneration differs from committed bytes")


def _generate_feasibility(root: Path, paths: dict[str, Path]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-remaining-feasibility-") as raw:
        output = Path(raw) / "generated.json"
        result = subprocess.run([
            sys.executable, str(root / "scripts/audit_source_kit_feasibility.py"),
            "--source-correspondence", str(paths["source-correspondence"]),
            "--source-kit-requirements", str(paths["source-kit-requirements"]),
            "--source-input-inventory", str(paths["source-input-inventory"]),
            "--output", str(output),
        ], cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _require(
            result.returncode == 0,
            "protected feasibility regeneration failed: "
            + result.stderr.decode("utf-8", errors="replace").strip(),
        )
        return output.read_bytes()


def _verify_release(policy: dict[str, Any], assets: dict[str, Any], feasibility: dict[str, Any]) -> None:
    _require(policy.get("policy_mode") == "fail-closed", "release policy is not fail-closed")
    for key in ("legal_compliance_certified", "source_availability_certified", "release_payload_integrated"):
        _require(policy.get(key) is False, f"release policy gate changed: {key}")
    _require(len(policy.get("releases", [])) == 4 and all(item.get("status") == "blocked" for item in policy["releases"]), "direct release gates changed")
    _require(assets.get("release_readiness") == "blocked", "release assets were marked ready")
    for key in ("legal_compliance_certified", "source_availability_certified", "source_kits_ready"):
        _require(assets.get(key) is False, f"release assets gate changed: {key}")
    _require(all(item.get("status") == "not-ready" for item in assets.get("required_source_asset_templates", [])), "source asset was marked ready")
    _require(feasibility.get("overall_status") == "blocked-inventory-recorded", "feasibility was marked ready")


def _verify_docs(readme: Path, legal_readme: Path, detail: Path) -> None:
    docs = {
        "README": _read_doc(readme),
        "legal README": _read_doc(legal_readme),
        "feasibility document": _read_doc(detail),
    }
    combined = "\n".join(docs.values()).casefold()
    for phrase in (
        "phase 6b2b2a1e",
        "ffmpeg compression, security, networking and remaining library evidence",
        "current batch components: 11", "static components: 11",
        "provider versions verified: 0", "verified immutable inputs: 0",
        "identified-name-only inputs: 11", "provider source archive hashes verified: 0",
        "codec batch: 16", "support batch: 14", "hardware/system batch: 14",
        "remaining-library batch: 11", "total dedicated component coverage: 55/55",
        "provider metadata identifies component names and static linkage",
        "exact provider dependency versions remain unresolved", "no component was promoted",
        "upstream project identification does not prove provider use",
        "no provider archive hash was accepted",
        "ffmpeg gmp and zlib are distinct from package-scoped aria2 records",
        "libssh is distinct from aria2 libssh2", "no source archive was committed",
        "feasibility counts remain unchanged",
        "all 55 ffmpeg external components now have dedicated evidence-batch coverage",
        "component-level coverage does not mean versions are resolved",
        "component-level coverage does not mean toolchain is resolved",
        "component-level coverage does not mean build orchestration is resolved",
        "component-level coverage does not establish source-kit completeness",
        "provider versions complete: false", "toolchain complete: false",
        "build orchestration complete: false", "source-kit completeness: false",
        "no source kit was assembled", "assembly remains unauthorized",
        "publishing remains blocked", "existing releases are not retroactively certified",
        "not legal advice", "aria2 verifier owns aria2 data and shared gates",
        "codec verifier owns the 16 codec records", "support verifier owns the 14 support records",
        "hardware/system verifier owns the 14 hardware/system records",
        "remaining-library verifier owns this 11-component batch",
        "general feasibility verifier owns overall schema and readiness",
        "no component verifier permanently freezes unrelated future evidence",
    ):
        _require(phrase in combined, f"documentation concept is missing: {phrase}")
    detailed = docs["feasibility document"].casefold()
    for cid in IDS:
        _require(f"`{cid}`" in detailed, f"documentation component is missing: {cid}")
    for label, text in docs.items():
        _hygiene(text, label)
        _require(
            not any(pattern.search(text) for pattern in UNSUPPORTED_RES),
            f"unsupported completion claim in {label}",
        )


def _verify_artifacts(
    root: Path,
    tracked_paths: list[str] | None,
    repository_files: list[str] | None,
    introduced_paths: list[str] | None,
) -> None:
    if tracked_paths is None:
        tracked_paths = _git_lines(root, ["ls-files"])
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for item in tracked_paths), "source archive or installer is tracked")
    if introduced_paths is None:
        introduced_paths = sorted(_changed_paths_since_baseline(root))
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES + BINARY_MEDIA_SUFFIXES) for item in introduced_paths), "archive, installer, binary, or media was introduced")
    if repository_files is None:
        repository_files = [
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
            and "__pycache__" not in path.parts and path.name not in OLD_REPORTS
        ]
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for item in repository_files), "source archive or installer exists inside repository")


def _changed_paths_since_baseline(root: Path) -> set[str]:
    changed = set(_git_lines(root, ["diff", "--name-only", BASELINE, "--"]))
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root, check=True, text=True, stdout=subprocess.PIPE,
    ).stdout.splitlines()
    for line in status:
        if line.startswith("?? "):
            path = line[3:].replace("\\", "/")
            if path not in OLD_REPORTS:
                changed.add(path)
    return changed


def _evidence(records: Any, label: str) -> None:
    _require(isinstance(records, list) and records, f"evidence is missing: {label}")
    keys = []
    for record in records:
        _require(isinstance(record, dict) and tuple(record) == EVIDENCE_KEYS, f"evidence schema is invalid: {label}")
        _require(all(isinstance(record[key], str) and record[key] for key in EVIDENCE_KEYS), f"evidence value is invalid: {label}")
        _require(record["status"] in {"verified", "partial", "unresolved"}, f"evidence status is invalid: {label}")
        if record["locator"].startswith("https://"):
            _official(record["locator"], f"{label} evidence")
        else:
            _require(record["locator"].startswith("legal/"), f"non-official locator: {label}")
        keys.append(tuple(record[key] for key in EVIDENCE_KEYS))
    _require(keys == sorted(set(keys)), f"evidence is duplicated or unsorted: {label}")


def _official(locator: str, label: str) -> None:
    parsed = urlparse(locator)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold().rstrip("/")
    _require(parsed.scheme == "https" and host and not parsed.username and not parsed.password, f"official locator must use TLS: {label}")
    if host == "github.com":
        _require(any(path == root or path.startswith(root + "/") for root in GITHUB_ROOTS), f"unapproved GitHub authority: {label}")
    else:
        _require(host in OTHER_HOSTS, f"non-official authority: {label}")
    _require(not parsed.query and not parsed.fragment, f"official locator has query or fragment: {label}")


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    return _load_json_bytes(raw, label), raw


def _load_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{label} contains BOM")
    _require(b"\r" not in raw, f"{label} must use LF")
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    _require(isinstance(document, dict), f"{label} root must be object")
    _require(raw == _canonical_json_bytes(document), f"{label} is not canonical JSON")
    return document


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_doc(path: Path) -> str:
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"documentation contains BOM: {path.name}")
    return raw.decode("utf-8")


def _hygiene(value: object, label: str) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    _require(LOCAL_PATH_RE.search(text) is None, f"local path in {label}")
    _require(TIMESTAMP_RE.search(text) is None, f"timestamp in {label}")
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


def _sorted_nonempty(value: object) -> bool:
    return (
        isinstance(value, list) and bool(value)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    )


def _suffix(path: str, suffixes: tuple[str, ...]) -> bool:
    return any(path.endswith(suffix) for suffix in suffixes)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise FFmpegRemainingLibraryEvidenceError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
