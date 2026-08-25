from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import verify_ffmpeg_support_primary_source_evidence as support_verifier


BASELINE = "24084860f442517647d3a0137628d23c4dd08549"
PACKAGE_HASH = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
PROVIDER_RELEASE = "8.1.2-essentials_build-www.gyan.dev"
CORE_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"

IDS = (
    "amf", "cuda", "cuda-llvm", "cuvid", "d3d11va", "d3d12va",
    "dxva2", "ffnvcodec", "libmfx", "libvpl", "mediafoundation",
    "nvdec", "nvenc", "vaapi",
)
STATIC_IDS = {
    "amf", "cuda", "cuda-llvm", "cuvid", "ffnvcodec", "libmfx",
    "libvpl", "nvdec", "nvenc",
}
SYSTEM_IDS = {"d3d11va", "d3d12va", "dxva2", "mediafoundation", "vaapi"}
NATURES = {
    "amf": "vendor-sdk",
    "cuda": "toolkit-interface",
    "cuda-llvm": "toolkit-interface",
    "cuvid": "hardware-api",
    "d3d11va": "system-api",
    "d3d12va": "system-api",
    "dxva2": "system-api",
    "ffnvcodec": "header-sdk",
    "libmfx": "source-library",
    "libvpl": "source-library",
    "mediafoundation": "system-api",
    "nvdec": "hardware-api",
    "nvenc": "hardware-api",
    "vaapi": "system-api",
}
AUTHORITIES = {
    "amf": "Advanced Micro Devices, Inc.",
    "cuda": "NVIDIA Corporation",
    "cuda-llvm": "LLVM Project",
    "cuvid": "NVIDIA Corporation",
    "d3d11va": "Microsoft Corporation",
    "d3d12va": "Microsoft Corporation",
    "dxva2": "Microsoft Corporation",
    "ffnvcodec": "FFmpeg project",
    "libmfx": "Intel Corporation",
    "libvpl": "Intel Corporation",
    "mediafoundation": "Microsoft Corporation",
    "nvdec": "NVIDIA Corporation",
    "nvenc": "NVIDIA Corporation",
    "vaapi": "libva project",
}
PROJECTS = {
    "amf": "Advanced Media Framework (AMF) SDK",
    "cuda": "CUDA Toolkit and programming interfaces",
    "cuda-llvm": "Clang CUDA compilation interface",
    "cuvid": "NVDECODE (CUVID) API",
    "d3d11va": "Direct3D 11 Video APIs",
    "d3d12va": "Direct3D 12 Video APIs",
    "dxva2": "DirectX Video Acceleration 2.0",
    "ffnvcodec": "FFmpeg nv-codec-headers",
    "libmfx": "Intel Media SDK (libmfx)",
    "libvpl": "Intel oneAPI Video Processing Library (oneVPL)",
    "mediafoundation": "Microsoft Media Foundation",
    "nvdec": "NVDECODE API",
    "nvenc": "NVENCODE API",
    "vaapi": "Video Acceleration API (VA-API)",
}
LOCATORS = {
    "amf": "https://github.com/GPUOpen-LibrariesAndSDKs/AMF",
    "cuda": "https://docs.nvidia.com/cuda/index.html",
    "cuda-llvm": "https://llvm.org/docs/CompileCudaWithLLVM.html",
    "cuvid": "https://docs.nvidia.com/video-technologies/video-codec-sdk/",
    "d3d11va": "https://learn.microsoft.com/en-us/windows/win32/medfound/direct3d-11-video-apis",
    "d3d12va": "https://learn.microsoft.com/en-us/windows/win32/medfound/direct3d-12-video-apis",
    "dxva2": "https://learn.microsoft.com/en-us/windows/win32/medfound/about-dxva-2-0",
    "ffnvcodec": "https://github.com/FFmpeg/nv-codec-headers",
    "libmfx": "https://github.com/Intel-Media-SDK/MediaSDK",
    "libvpl": "https://github.com/intel/libvpl",
    "mediafoundation": "https://learn.microsoft.com/en-us/windows/win32/medfound/microsoft-media-foundation-sdk",
    "nvdec": "https://docs.nvidia.com/video-technologies/video-codec-sdk/",
    "nvenc": "https://docs.nvidia.com/video-technologies/video-codec-sdk/",
    "vaapi": "https://github.com/intel/libva",
}
TREATMENTS = {
    "amf": "sdk-or-header-input-requires-version-resolution",
    "cuda": "provider-label-needs-build-recipe",
    "cuda-llvm": "provider-label-needs-build-recipe",
    "cuvid": "sdk-or-header-input-requires-version-resolution",
    "d3d11va": "system-interface-documentation-only",
    "d3d12va": "system-interface-documentation-only",
    "dxva2": "system-interface-documentation-only",
    "ffnvcodec": "sdk-or-header-input-requires-version-resolution",
    "libmfx": "source-archive-required-if-version-resolved",
    "libvpl": "source-archive-required-if-version-resolved",
    "mediafoundation": "system-interface-documentation-only",
    "nvdec": "provider-label-needs-build-recipe",
    "nvenc": "provider-label-needs-build-recipe",
    "vaapi": "system-interface-documentation-only",
}
ARCHIVE_APPLICABILITY = {
    cid: (
        "not-applicable-system-interface" if cid in SYSTEM_IDS
        else "required-if-version-resolved" if cid in {"libmfx", "libvpl"}
        else "sdk-package-if-version-resolved"
    )
    for cid in IDS
}

TOP_KEYS = (
    "schema_version", "target_phase", "baseline_commit", "package_id",
    "binary_package_sha256", "provider_release_identity", "ffmpeg_core_commit",
    "research_policy", "provider_build_evidence", "components", "summary",
    "gate_state",
)
RESEARCH_KEYS = (
    "authority_scope", "network_mode", "source_archive_scope",
    "sdk_installer_downloads_allowed", "binary_downloads_allowed",
    "runtime_execution_allowed",
)
BUILD_KEYS = (
    "exact_package_identified", "exact_historical_recipe_identified",
    "exact_dependency_versions_identified", "exact_toolkit_versions_identified",
    "exact_windows_sdk_identified", "exact_toolchain_identified",
    "exact_configure_command_identified", "patch_set_identified", "evidence",
    "blockers",
)
COMPONENT_KEYS = (
    "id", "provider_linkage", "component_nature", "provider_version",
    "provider_version_status", "official_authority",
    "official_project_or_interface", "official_repository_or_documentation",
    "release_identity", "source_archive", "license_or_terms_evidence",
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
    "total_components", "static_components", "system_components",
    "provider_versions_verified", "verified_immutable_inputs",
    "identified_name_only", "system_component_candidates", "unresolved_inputs",
    "archive_hashes_verified", "exact_provider_recipe_found",
    "exact_toolkit_versions_found", "exact_windows_sdk_found",
    "all_batch_inputs_resolved", "source_kit_assembly_authorized",
    "remaining_blockers",
)
GATE_KEYS = (
    "legal_compliance_certified", "source_availability_certified",
    "source_assets_created", "source_kits_ready", "assembly_authorized",
    "release_gate_reconsideration_allowed", "publishing_allowed",
)
INVENTORY_KEYS = (
    "id", "linkage", "provider_version", "version_status",
    "upstream_repository", "immutable_ref", "source_archive_sha256", "evidence",
    "resolution_status", "blockers",
)
STATIC_BLOCKERS = tuple(sorted((
    "Exact provider component or SDK version is unresolved.",
    "Exact provider-to-official mapping is unresolved.",
    "Immutable provider input cannot be selected without provider-version evidence.",
    "Provider build recipe does not establish source or SDK incorporation.",
    "Source or SDK package identity and independent SHA-256 are unresolved.",
)))
OLD_REPORTS = {
    "bat_ignore_errors_report.txt", "fast_video_scope_report.txt",
    "numbered_two_phase_report.txt",
}
PROTECTED = {
    "codec-primary-evidence": "legal/primary-source-evidence-ffmpeg-codecs.json",
    "support-primary-evidence": "legal/primary-source-evidence-ffmpeg-support.json",
    "aria2-primary-evidence": "legal/primary-source-evidence-aria2.json",
    "source-correspondence": "legal/source-correspondence.json",
    "source-kit-requirements": "legal/source-kit-requirements.json",
    "release-policy": "legal/release-policy.json",
    "release-assets": "legal/release-assets-v2.json",
}
CURRENT_RELEASE_PROTECTED_SHA256 = {
    "legal/release-policy.json": "6b2fc3d061287f57bf04e6e02e64d56d5bf36af490db16bba129f160c374fdb7",
    "legal/release-assets-v2.json": "6983a68fe45c66b936ac055179b1ee895c87523ea239b6e035a71372c265a234",
}
ARCHIVE_SUFFIXES = (
    ".7z", ".bz2", ".gz", ".rar", ".tar", ".tar.bz2", ".tar.gz",
    ".tar.xz", ".tgz", ".txz", ".xz", ".zip",
)
INSTALLER_SUFFIXES = (
    ".appx", ".cab", ".deb", ".dmg", ".iso", ".msi", ".msix", ".pkg",
    ".rpm", ".run", ".whl",
)
BINARY_MEDIA_SUFFIXES = (
    ".avi", ".dll", ".dylib", ".exe", ".gif", ".jpeg", ".jpg", ".m4a",
    ".mkv", ".mov", ".mp3", ".mp4", ".pdb", ".pyd", ".so", ".wav",
    ".webm", ".webp",
)
GITHUB_ROOTS = {
    "/ffmpeg/nv-codec-headers", "/gpuopen-librariesandsdks/amf",
    "/intel-media-sdk/mediasdk", "/intel/libva", "/intel/libvpl",
}
OTHER_HOSTS = {"docs.nvidia.com", "learn.microsoft.com", "llvm.org", "www.gyan.dev"}
LOCAL_PATH_RE = re.compile(
    r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
TIMESTAMP_RE = re.compile(r"(?i)\b(?:19|20)\d{2}-\d{2}-\d{2}[t ][0-9]{2}:[0-9]{2}")
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
    re.compile(r"(?i)\bcorresponding source (?:is )?complete\b"),
    re.compile(r"(?i)\blegal compliance (?:is )?certified\b"),
    re.compile(r"(?i)\brelease (?:is )?(?:approved|ready)\b"),
    re.compile(r"(?i)\bbuild (?:is )?reproducible\b"),
    re.compile(r"(?i)\bexact (?:cuda toolkit|windows sdk|amf sdk|onevpl|nvidia codec-header|va-api implementation|provider toolchain) version (?:is|was) (?:known|verified|identified)\b"),
)

FeasibilityRunner = Callable[[Path, dict[str, Path]], bytes]


class FFmpegHardwareSystemEvidenceError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify offline FFmpeg hardware and system evidence"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    for option in (
        "primary-evidence", "codec-primary-evidence", "support-primary-evidence",
        "aria2-primary-evidence", "source-correspondence",
        "source-input-inventory", "source-kit-feasibility",
        "source-kit-requirements", "release-policy", "release-assets",
        "readme", "legal-readme", "feasibility-doc",
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
        FFmpegHardwareSystemEvidenceError, OSError, UnicodeError,
        json.JSONDecodeError, subprocess.SubprocessError,
    ) as exc:
        print(f"FFmpeg hardware and system evidence verification failed: {exc}", file=sys.stderr)
        return 1
    print("FFmpeg hardware and system evidence verified")
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
    _verify_feasibility(
        root, paths, feasibility, feasibility_raw,
        feasibility_runner or _generate_feasibility,
    )
    _verify_release(policy, assets, feasibility)
    _verify_docs(paths["readme"], paths["legal-readme"], paths["feasibility-doc"])
    _verify_artifacts(root, tracked_paths, repository_files, introduced_paths)


def _paths(root: Path, overrides: dict[str, Path]) -> dict[str, Path]:
    paths = {
        "primary-evidence": root / "legal/primary-source-evidence-ffmpeg-hardware-system.json",
        "codec-primary-evidence": root / "legal/primary-source-evidence-ffmpeg-codecs.json",
        "support-primary-evidence": root / "legal/primary-source-evidence-ffmpeg-support.json",
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
        current = paths[key].read_bytes()
        expected_sha256 = CURRENT_RELEASE_PROTECTED_SHA256.get(relative)
        if expected_sha256 is not None:
            _require(hashlib.sha256(current).hexdigest() == expected_sha256, f"protected file changed: {relative}")
        else:
            _require(current == _git_blob(root, relative), f"protected file changed: {relative}")
    _require((root / "VERSION").read_text(encoding="utf-8").strip() == "1.3.2", "VERSION changed")


def _verify_prior_owners(
    root: Path,
    paths: dict[str, Path],
    tracked_paths: list[str] | None,
    repository_files: list[str] | None,
    introduced_paths: list[str] | None,
) -> None:
    try:
        support_verifier.verify_repository(
            root,
            overrides={
                "primary-evidence": paths["support-primary-evidence"],
                "codec-primary-evidence": paths["codec-primary-evidence"],
                "aria2-primary-evidence": paths["aria2-primary-evidence"],
                "source-correspondence": paths["source-correspondence"],
                "source-input-inventory": paths["source-input-inventory"],
                "source-kit-feasibility": paths["source-kit-feasibility"],
                "source-kit-requirements": paths["source-kit-requirements"],
                "release-policy": paths["release-policy"],
                "release-assets": paths["release-assets"],
                "readme": paths["readme"],
                "legal-readme": paths["legal-readme"],
                "feasibility-doc": paths["feasibility-doc"],
            },
            tracked_paths=tracked_paths,
            repository_files=repository_files,
            introduced_paths=introduced_paths,
        )
    except support_verifier.FFmpegSupportPrimarySourceEvidenceError as exc:
        raise FFmpegHardwareSystemEvidenceError(
            f"prior component-owner verifier rejected the fixture: {exc}"
        ) from exc


def _verify_primary(document: dict[str, Any], correspondence: dict[str, Any]) -> None:
    _require(tuple(document) == TOP_KEYS, "primary evidence schema or field order is invalid")
    expected_top = {
        "schema_version": 1,
        "target_phase": "6B2B2A1d",
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
    gate = document["gate_state"]
    _require(isinstance(gate, dict) and tuple(gate) == GATE_KEYS, "gate schema is invalid")
    _require(all(gate[key] is False for key in GATE_KEYS), "primary evidence gate changed")
    _hygiene(document, "primary evidence")


def _verify_build(record: Any) -> None:
    _require(isinstance(record, dict) and tuple(record) == BUILD_KEYS, "provider build schema is invalid")
    _require(record["exact_package_identified"] is True, "exact package is not identified")
    for key in BUILD_KEYS[1:8]:
        _require(record[key] is False, f"unsupported provider build promotion: {key}")
    _evidence(record["evidence"], "provider build evidence")
    _require(any(
        item["kind"] == "official-provider-page"
        and item["locator"] == "https://www.gyan.dev/ffmpeg/builds/"
        for item in record["evidence"]
    ), "official provider page is missing")
    _require(_sorted_nonempty(record["blockers"]), "provider build blockers are missing")
    _require(any("all 14 components" in item for item in record["blockers"]), "batch version blocker is missing")


def _verify_component(component: Any, source: dict[str, Any]) -> None:
    _require(isinstance(component, dict) and tuple(component) == COMPONENT_KEYS, "component schema is invalid")
    cid = component["id"]
    linkage = "static" if cid in STATIC_IDS else "system"
    resolution = "identified-name-only" if cid in STATIC_IDS else "system-component-candidate"
    _require(component["provider_linkage"] == source["linkage"] == linkage, f"linkage changed: {cid}")
    _require(source["version"] == "unverified", f"provider correspondence version changed: {cid}")
    _require(component["component_nature"] == NATURES[cid], f"component nature changed: {cid}")
    _require(component["provider_version"] == component["provider_version_status"] == "unresolved", f"provider version was promoted: {cid}")
    _require(component["official_authority"] == AUTHORITIES[cid], f"official authority changed: {cid}")
    _require(component["official_project_or_interface"] == PROJECTS[cid], f"official interface changed: {cid}")
    _require(component["official_repository_or_documentation"] == LOCATORS[cid], f"official locator changed: {cid}")
    _official(component["official_repository_or_documentation"], f"{cid} official documentation")
    identity = component["release_identity"]
    _require(isinstance(identity, dict) and tuple(identity) == IDENTITY_KEYS, f"identity schema is invalid: {cid}")
    _require(identity["kind"] == identity["value"] == "unresolved", f"provider identity was invented: {cid}")
    _require(identity["secondary_identity"] == "not-applicable", f"secondary identity changed: {cid}")
    _require("exact Gyan" in identity["resolution_method"] and "unresolved" in identity["resolution_method"], f"provider-version gap is missing: {cid}")
    _require("provider input" in identity["resolution_method"], f"provider-input disclaimer is missing: {cid}")
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
    }, f"provider archive or installer was invented: {cid}")
    terms = component["license_or_terms_evidence"]
    _require(isinstance(terms, dict) and tuple(terms) == LICENSE_KEYS, f"terms schema is invalid: {cid}")
    _require(terms["source_path_or_locator"] == LOCATORS[cid] and terms["status"] == "partial", f"terms context changed: {cid}")
    _official(terms["source_path_or_locator"], f"{cid} terms")
    _require("does not establish" in terms["claim"], f"terms evidence overclaims provider use: {cid}")
    _require(component["provider_to_official_mapping"] == "unresolved", f"provider mapping was promoted: {cid}")
    _require(component["source_kit_treatment"] == TREATMENTS[cid], f"source-kit treatment changed: {cid}")
    _require(component["resolution_status"] == resolution, f"resolution was promoted: {cid}")
    _evidence(component["evidence"], cid)
    _require([item["kind"] for item in component["evidence"]] == [
        "component-nature-classification", "official-interface-research",
        "provider-package-metadata", "provider-version-gap", "source-kit-treatment",
    ], f"component evidence set changed: {cid}")
    status_by_kind = {item["kind"]: item["status"] for item in component["evidence"]}
    _require(status_by_kind["provider-version-gap"] == "unresolved", f"provider gap was promoted: {cid}")
    _require(all(
        status_by_kind[kind] == "partial" for kind in status_by_kind
        if kind != "provider-version-gap"
    ), f"contextual evidence was promoted: {cid}")
    _require(any(
        item["kind"] == "official-interface-research"
        and item["locator"] == LOCATORS[cid]
        and "historical Gyan input" in item["claim"]
        for item in component["evidence"]
    ), f"official contextual finding is missing: {cid}")
    _require(any(
        item["kind"] == "component-nature-classification"
        and NATURES[cid] in item["claim"]
        for item in component["evidence"]
    ), f"component nature evidence is missing: {cid}")
    _require(any(
        item["kind"] == "provider-package-metadata"
        and item["locator"].endswith("/" + cid)
        and f"{linkage} linkage" in item["claim"]
        for item in component["evidence"]
    ), f"provider linkage evidence is missing: {cid}")
    _require(any(
        item["kind"] == "source-kit-treatment"
        and TREATMENTS[cid] in item["claim"]
        and "does not authorize assembly" in item["claim"]
        for item in component["evidence"]
    ), f"source-kit treatment evidence is missing: {cid}")
    expected_blockers = STATIC_BLOCKERS if cid in STATIC_IDS else _system_blockers(cid)
    _require(tuple(component["blockers"]) == expected_blockers, f"component blockers changed: {cid}")


def _system_blockers(cid: str) -> tuple[str, ...]:
    version = (
        "Exact VA-API interface and implementation version is unresolved."
        if cid == "vaapi" else
        "Exact Windows SDK or system-interface version is unresolved."
    )
    return tuple(sorted((
        "Exact provider build dependency mapping is unresolved.",
        version,
        "Provider configure and build evidence is incomplete.",
        "System-component treatment requires final legal and build review.",
    )))


def _verify_summary(summary: Any) -> None:
    _require(isinstance(summary, dict) and tuple(summary) == SUMMARY_KEYS, "summary schema is invalid")
    expected = {
        "total_components": 14,
        "static_components": 9,
        "system_components": 5,
        "provider_versions_verified": 0,
        "verified_immutable_inputs": 0,
        "identified_name_only": 9,
        "system_component_candidates": 5,
        "unresolved_inputs": 0,
        "archive_hashes_verified": 0,
        "exact_provider_recipe_found": False,
        "exact_toolkit_versions_found": False,
        "exact_windows_sdk_found": False,
        "all_batch_inputs_resolved": False,
        "source_kit_assembly_authorized": False,
    }
    for key, value in expected.items():
        _require(summary[key] == value, f"summary value changed: {key}")
    _require(_sorted_nonempty(summary["remaining_blockers"]), "summary blockers are missing")


def _verify_inventory(root: Path, primary: dict[str, Any], inventory: dict[str, Any]) -> None:
    baseline = _load_json_bytes(_git_blob(root, "legal/source-input-inventory.json"), "baseline inventory")
    for key in (
        "release_gate_reconsideration_allowed", "legal_compliance_certified",
        "source_assets_created",
    ):
        _require(inventory[key] is False, f"inventory shared gate changed: {key}")
    _require(all(item["package_status"] == "blocked" for item in inventory["packages"]), "inventory package gate changed")
    normalized = copy.deepcopy(inventory)
    current_ffmpeg = next(item for item in normalized["packages"] if item["id"] == "ffmpeg")
    baseline_ffmpeg = next(item for item in baseline["packages"] if item["id"] == "ffmpeg")
    current = {item["id"]: item for item in current_ffmpeg["external_components"]}
    original = {item["id"]: item for item in baseline_ffmpeg["external_components"]}
    _require(set(current) == set(original), "FFmpeg inventory component set changed")
    for cid in current:
        current[cid]["evidence"] = copy.deepcopy(original[cid]["evidence"])
        current[cid]["blockers"] = copy.deepcopy(original[cid]["blockers"])
    _require(normalized == baseline, "inventory structural state or shared ownership changed")
    primary_records = {item["id"]: item for item in primary["components"]}
    ffmpeg = next(item for item in inventory["packages"] if item["id"] == "ffmpeg")
    records = {item["id"]: item for item in ffmpeg["external_components"]}
    for cid in IDS:
        record = records[cid]
        linkage = "static" if cid in STATIC_IDS else "system"
        resolution = "identified-name-only" if cid in STATIC_IDS else "system-component-candidate"
        _require(tuple(record) == INVENTORY_KEYS, f"inventory schema changed: {cid}")
        _require(record["linkage"] == linkage, f"inventory linkage changed: {cid}")
        for key in (
            "provider_version", "version_status", "upstream_repository",
            "immutable_ref", "source_archive_sha256",
        ):
            _require(record[key] == "unresolved", f"inventory field was promoted: {cid}.{key}")
        _require(record["resolution_status"] == resolution, f"inventory status was promoted: {cid}")
        _require(record["evidence"] == primary_records[cid]["evidence"], f"inventory evidence changed: {cid}")
        _require(record["blockers"] == primary_records[cid]["blockers"], f"inventory blockers changed: {cid}")
        _evidence(record["evidence"], f"inventory {cid}")
    _hygiene(inventory, "source input inventory")


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
    _require(all(cid in ffmpeg["partially_identified_inputs"] for cid in IDS), "hardware/system batch missing from feasibility")
    _require(ffmpeg["source_kit_status"] == "not-ready", "FFmpeg source kit was marked ready")
    outputs = [runner(root, paths), runner(root, paths)]
    _require(outputs[0] == outputs[1], "feasibility regeneration is non-deterministic")
    _require(outputs[0] == raw, "feasibility regeneration differs from committed bytes")


def _generate_feasibility(root: Path, paths: dict[str, Path]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-hardware-feasibility-") as raw:
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
    _require(len(policy.get("releases", [])) == 5 and all(item.get("status") == "blocked" for item in policy["releases"]), "direct release gates changed")
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
        "phase 6b2b2a1d",
        "ffmpeg hardware acceleration and system-interface evidence",
        "total components: 14", "static candidates: 9", "system candidates: 5",
        "provider versions verified: 0", "verified immutable inputs: 0",
        "identified-name-only inputs: 9", "system-component candidates: 5",
        "provider archive hashes verified: 0",
        "provider metadata identifies names and linkage",
        "exact toolkit, sdk and system-interface versions remain unresolved",
        "no static candidate was promoted", "no system candidate was declared fully resolved",
        "official vendor documentation does not prove historical provider version",
        "system apis are not automatically conventional source archives",
        "no provider source archive hash was accepted",
        "no source archive or sdk installer was committed",
        "feasibility counts remain unchanged", "no source kit was assembled",
        "exact historical recipe remains unresolved", "exact toolchain remains unresolved",
        "exact configure command remains unresolved", "patch set remains unresolved",
        "aria2 verifier owns aria2 data and shared gates",
        "codec verifier owns the prior 16 codec records",
        "support verifier owns the prior 14 support records",
        "hardware/system verifier owns this 14-component batch",
        "general feasibility verifier owns overall schema and readiness",
        "no component verifier permanently freezes unrelated future evidence",
        "assembly remains unauthorized", "publishing remains blocked",
        "existing releases are not retroactively certified", "not legal advice",
    ):
        _require(phrase in combined, f"documentation concept is missing: {phrase}")
    detailed = docs["feasibility document"].casefold()
    for cid in IDS:
        _require(f"`{cid}`" in detailed, f"documentation component is missing: {cid}")
    for label, text in docs.items():
        _hygiene(text, label)
        _require(not any(pattern.search(text) for pattern in UNSUPPORTED_RES), f"unsupported completion claim in {label}")


def _verify_artifacts(
    root: Path,
    tracked_paths: list[str] | None,
    repository_files: list[str] | None,
    introduced_paths: list[str] | None,
) -> None:
    if tracked_paths is None:
        tracked_paths = _git_lines(root, ["ls-files"])
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for item in tracked_paths), "source archive or SDK installer is tracked")
    if introduced_paths is None:
        introduced_paths = sorted(_changed_paths_since_baseline(root))
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES + BINARY_MEDIA_SUFFIXES) for item in introduced_paths), "archive, SDK installer, binary, or media was introduced")
    if repository_files is None:
        repository_files = [
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
        ]
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES + INSTALLER_SUFFIXES) for item in repository_files), "source archive or SDK installer exists inside repository")


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
        raise FFmpegHardwareSystemEvidenceError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
