from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASELINE = "0863c96b36f724cbc75469148c1e4ffe04421f3f"
PACKAGE_HASH = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
PROVIDER_RELEASE = "8.1.2-essentials_build-www.gyan.dev"
CORE_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"

IDS = (
    "libaom", "libgsm", "libmp3lame", "libopencore-amrnb",
    "libopencore-amrwb", "libopenjpeg", "libopus", "libspeex",
    "libtheora", "libvo-amrwbenc", "libvorbis", "libvpx", "libwebp",
    "libx264", "libx265", "libxvid",
)
PROJECTS = {
    "libaom": "Alliance for Open Media AV1 codec",
    "libgsm": "GSM 06.10 reference implementation",
    "libmp3lame": "LAME",
    "libopencore-amrnb": "opencore-amr",
    "libopencore-amrwb": "opencore-amr",
    "libopenjpeg": "OpenJPEG",
    "libopus": "Opus",
    "libspeex": "Speex",
    "libtheora": "Theora",
    "libvo-amrwbenc": "VisualOn AMR-WB encoder library",
    "libvorbis": "Vorbis",
    "libvpx": "WebM libvpx",
    "libwebp": "WebP codec library",
    "libx264": "x264",
    "libx265": "x265",
    "libxvid": "Xvid",
}
REPOSITORIES = {
    "libaom": "https://aomedia.googlesource.com/aom",
    "libgsm": "https://www.quut.com/gsm/",
    "libmp3lame": "https://svn.code.sf.net/p/lame/svn/",
    "libopencore-amrnb": "https://git.code.sf.net/p/opencore-amr/code",
    "libopencore-amrwb": "https://git.code.sf.net/p/opencore-amr/code",
    "libopenjpeg": "https://github.com/uclouvain/openjpeg",
    "libopus": "https://github.com/xiph/opus",
    "libspeex": "https://github.com/xiph/speex",
    "libtheora": "https://github.com/xiph/theora",
    "libvo-amrwbenc": "https://github.com/mstorsjo/vo-amrwbenc",
    "libvorbis": "https://github.com/xiph/vorbis",
    "libvpx": "https://chromium.googlesource.com/webm/libvpx",
    "libwebp": "https://chromium.googlesource.com/webm/libwebp",
    "libx264": "https://code.videolan.org/videolan/x264",
    "libx265": "https://bitbucket.org/multicoreware/x265_git",
    "libxvid": "https://labs.xvid.com/source/",
}
LICENSES = {
    "libaom": "https://aomedia.googlesource.com/aom/+/a9a24c56d8754f72e4f26cc6ff0f3926d1b91163/LICENSE",
    "libgsm": "https://www.quut.com/gsm/COPYRIGHT",
    "libmp3lame": "https://lame.sourceforge.io/license.txt",
    "libopencore-amrnb": "https://sourceforge.net/projects/opencore-amr/",
    "libopencore-amrwb": "https://sourceforge.net/projects/opencore-amr/",
    "libopenjpeg": "https://github.com/uclouvain/openjpeg/blob/402ef5862195b177ea0a7788f2a6ef2804e62285/LICENSE",
    "libopus": "https://github.com/xiph/opus/blob/3da9f7a6db1c05c3996cb363a9d1931a978bf1be/COPYING",
    "libspeex": "https://github.com/xiph/speex/blob/05895229896dc942d453446eba6f9f5ddcf95422/COPYING",
    "libtheora": "https://github.com/xiph/theora/blob/28fd5ec77f0ad0e07a371cef1047828116f6bd8a/COPYING",
    "libvo-amrwbenc": "https://github.com/mstorsjo/vo-amrwbenc/blob/884dd247dbeb6b2bd2cd3291c4872de95700291f/COPYING",
    "libvorbis": "https://github.com/xiph/vorbis/blob/e3c9861ff096d52378e131ff8c334552e09cdffa/COPYING",
    "libvpx": "https://chromium.googlesource.com/webm/libvpx/+/c1f98b5fedfc5c302439587341082542d8e4fd2d/LICENSE",
    "libwebp": "https://chromium.googlesource.com/webm/libwebp/+/733c91e461c18cf1127c9ed0a80dccbcfed599d3/COPYING",
    "libx264": "https://code.videolan.org/videolan/x264/-/blob/0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee/COPYING",
    "libx265": "https://bitbucket.org/multicoreware/x265_git/src/b81f650e21e8aacbe6a9ad04ce14aefc05b932c0/COPYING",
    "libxvid": "https://labs.xvid.com/project/",
}

TOP_KEYS = (
    "schema_version", "target_phase", "baseline_commit", "package_id",
    "binary_package_sha256", "provider_release_identity", "ffmpeg_core_commit",
    "research_policy", "provider_build_evidence", "components", "summary",
    "gate_state",
)
RESEARCH_KEYS = (
    "authority_scope", "network_mode", "source_archive_scope",
    "binary_downloads_allowed", "runtime_execution_allowed",
)
BUILD_KEYS = (
    "exact_package_identified", "exact_historical_recipe_identified",
    "exact_dependency_versions_identified", "exact_toolchain_identified",
    "exact_configure_command_identified", "patch_set_identified", "evidence",
    "blockers",
)
COMPONENT_KEYS = (
    "id", "provider_linkage", "provider_version", "provider_version_status",
    "official_project", "official_repository", "release_identity",
    "source_archive", "license_evidence", "provider_to_upstream_match",
    "resolution_status", "evidence", "blockers",
)
IDENTITY_KEYS = ("kind", "value", "secondary_identity", "resolution_method")
ARCHIVE_KEYS = (
    "filename", "official_locator", "sha256", "independently_hashed",
    "upstream_checksum_status", "signature_status",
)
LICENSE_KEYS = ("classification", "source_path_or_locator", "status", "claim")
EVIDENCE_KEYS = ("kind", "authority", "locator", "claim", "status")
SUMMARY_KEYS = (
    "total_components", "provider_versions_verified", "verified_immutable_inputs",
    "partial_inputs", "identified_name_only", "unresolved_inputs",
    "archive_hashes_verified", "exact_provider_recipe_found",
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
BLOCKERS = (
    "Exact provider component version is unresolved.",
    "Exact provider-to-upstream source mapping is unresolved.",
    "Immutable provider input cannot be selected without provider-version evidence.",
)
PRIMARY_BLOCKERS = tuple(sorted(BLOCKERS + (
    "Provider-selected source archive and independent SHA-256 are unresolved.",
)))
OLD_REPORTS = {
    "bat_ignore_errors_report.txt", "fast_video_scope_report.txt",
    "numbered_two_phase_report.txt",
}
PROTECTED = {
    "source-correspondence": "legal/source-correspondence.json",
    "source-kit-requirements": "legal/source-kit-requirements.json",
    "release-policy": "legal/release-policy.json",
    "release-assets": "legal/release-assets-v2.json",
    "aria2-primary-evidence": "legal/primary-source-evidence-aria2.json",
}
ARCHIVE_SUFFIXES = (
    ".7z", ".bz2", ".gz", ".rar", ".tar", ".tar.bz2", ".tar.gz",
    ".tar.xz", ".tgz", ".txz", ".xz", ".zip",
)
BINARY_MEDIA_SUFFIXES = (
    ".avi", ".dll", ".dylib", ".exe", ".gif", ".jpeg", ".jpg", ".m4a",
    ".mkv", ".mov", ".mp3", ".mp4", ".pyd", ".so", ".wav", ".webm",
    ".webp",
)
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
)


class FFmpegCodecPrimarySourceEvidenceError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify offline FFmpeg codec primary-source evidence"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    for option in (
        "primary-evidence", "source-correspondence", "source-input-inventory",
        "source-kit-feasibility", "source-kit-requirements", "release-policy",
        "release-assets", "aria2-primary-evidence", "readme", "legal-readme",
        "feasibility-doc",
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
        FFmpegCodecPrimarySourceEvidenceError, OSError, UnicodeError,
        json.JSONDecodeError, subprocess.SubprocessError,
    ) as exc:
        print(
            f"FFmpeg codec primary-source evidence verification failed: {exc}",
            file=sys.stderr,
        )
        return 1
    print("FFmpeg codec primary-source evidence verified")
    return 0


def verify_repository(
    root: Path,
    *,
    overrides: dict[str, Path] | None = None,
    tracked_paths: list[str] | None = None,
    repository_files: list[str] | None = None,
    introduced_paths: list[str] | None = None,
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
    _load_json(paths["aria2-primary-evidence"], "aria2 primary evidence")
    _verify_protected(root, paths)
    _verify_primary(primary, correspondence)
    _verify_inventory(root, primary, inventory)
    _verify_feasibility(root, paths, feasibility, feasibility_raw)
    _verify_release(policy, assets, feasibility)
    _verify_docs(paths["readme"], paths["legal-readme"], paths["feasibility-doc"])
    _verify_artifacts(root, tracked_paths, repository_files, introduced_paths)


def _paths(root: Path, overrides: dict[str, Path]) -> dict[str, Path]:
    paths = {
        "primary-evidence": root / "legal/primary-source-evidence-ffmpeg-codecs.json",
        "source-correspondence": root / "legal/source-correspondence.json",
        "source-input-inventory": root / "legal/source-input-inventory.json",
        "source-kit-feasibility": root / "legal/source-kit-feasibility.json",
        "source-kit-requirements": root / "legal/source-kit-requirements.json",
        "release-policy": root / "legal/release-policy.json",
        "release-assets": root / "legal/release-assets-v2.json",
        "aria2-primary-evidence": root / "legal/primary-source-evidence-aria2.json",
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


def _verify_primary(document: dict[str, Any], correspondence: dict[str, Any]) -> None:
    _require(tuple(document) == TOP_KEYS, "primary evidence schema or field order is invalid")
    expected_top = {
        "schema_version": 1,
        "target_phase": "6B2B2A1b",
        "baseline_commit": BASELINE,
        "package_id": "ffmpeg",
        "binary_package_sha256": PACKAGE_HASH,
        "provider_release_identity": PROVIDER_RELEASE,
        "ffmpeg_core_commit": CORE_COMMIT,
    }
    for key, value in expected_top.items():
        _require(document[key] == value, f"primary identity changed: {key}")
    package = next(item for item in correspondence["packages"] if item["id"] == "ffmpeg")
    _require(package["binary_package"]["sha256"] == PACKAGE_HASH, "FFmpeg correspondence package hash changed")
    _require(package["provider"]["release_identity"] == PROVIDER_RELEASE, "FFmpeg correspondence provider changed")
    _require(package["core_source"]["commit"] == CORE_COMMIT, "FFmpeg correspondence core changed")
    policy = document["research_policy"]
    _require(isinstance(policy, dict) and tuple(policy) == RESEARCH_KEYS, "research policy schema is invalid")
    _require(policy == {
        "authority_scope": "official-primary-sources-only",
        "network_mode": "read-only",
        "source_archive_scope": "provider-matched-source-only",
        "binary_downloads_allowed": False,
        "runtime_execution_allowed": False,
    }, "research policy changed")
    _verify_build(document["provider_build_evidence"])
    components = document["components"]
    _require(isinstance(components, list) and [item.get("id") for item in components] == list(IDS), "component batch is missing, duplicated, invented, or unsorted")
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
    for key in BUILD_KEYS[1:6]:
        _require(record[key] is False, f"unsupported provider build promotion: {key}")
    _evidence(record["evidence"], "provider build evidence")
    _require(any(item["kind"] == "official-provider-page" and item["locator"] == "https://www.gyan.dev/ffmpeg/builds/" for item in record["evidence"]), "official provider page is missing")
    _require(_sorted_nonempty(record["blockers"]), "provider build blockers are missing")


def _verify_component(component: Any, source: dict[str, Any]) -> None:
    _require(isinstance(component, dict) and tuple(component) == COMPONENT_KEYS, "component schema is invalid")
    cid = component["id"]
    _require(component["provider_linkage"] == source["linkage"] == "static", f"linkage changed: {cid}")
    _require(source["version"] == "unverified", f"provider correspondence version changed: {cid}")
    _require(component["provider_version"] == component["provider_version_status"] == "unresolved", f"provider version was promoted: {cid}")
    _require(component["official_project"] == PROJECTS[cid] and component["official_repository"] == REPOSITORIES[cid], f"upstream authority changed: {cid}")
    _official(component["official_repository"], f"{cid} repository")
    identity = component["release_identity"]
    _require(isinstance(identity, dict) and tuple(identity) == IDENTITY_KEYS, f"identity schema is invalid: {cid}")
    _require(identity["kind"] == identity["value"] == "unresolved" and identity["secondary_identity"] == "not-applicable", f"provider input identity was invented: {cid}")
    _require("exact Gyan dependency version is unresolved" in identity["resolution_method"], f"provider-version gap is missing: {cid}")
    archive = component["source_archive"]
    _require(isinstance(archive, dict) and tuple(archive) == ARCHIVE_KEYS, f"archive schema is invalid: {cid}")
    _require(archive == {
        "filename": "unresolved", "official_locator": "unresolved",
        "sha256": "unresolved", "independently_hashed": False,
        "upstream_checksum_status": "unresolved", "signature_status": "unresolved",
    }, f"provider archive or hash was invented: {cid}")
    license_record = component["license_evidence"]
    _require(isinstance(license_record, dict) and tuple(license_record) == LICENSE_KEYS, f"license schema is invalid: {cid}")
    _require(license_record["source_path_or_locator"] == LICENSES[cid] and license_record["status"] == "partial", f"license context changed: {cid}")
    _official(license_record["source_path_or_locator"], f"{cid} license")
    _require("not a provider-version match" in license_record["claim"], f"license evidence overclaims provider use: {cid}")
    _require(component["provider_to_upstream_match"] == "unresolved", f"provider mapping was promoted: {cid}")
    _require(component["resolution_status"] == "identified-name-only", f"component was promoted: {cid}")
    _evidence(component["evidence"], cid)
    _require([item["kind"] for item in component["evidence"]] == [
        "official-license-research", "official-repository-research",
        "provider-package-metadata",
    ], f"component evidence set changed: {cid}")
    _require(all(item["status"] == "partial" for item in component["evidence"]), f"component evidence was promoted: {cid}")
    _require(any(item["kind"] == "official-repository-research" and item["locator"] == REPOSITORIES[cid] and "no release or commit is represented as the Gyan provider input" in item["claim"] for item in component["evidence"]), f"contextual repository finding is missing: {cid}")
    _require(any(item["kind"] == "provider-package-metadata" and item["locator"].endswith("/" + cid) and "does not identify its version" in item["claim"] for item in component["evidence"]), f"provider-version gap evidence is missing: {cid}")
    _require(tuple(component["blockers"]) == PRIMARY_BLOCKERS, f"component blockers changed: {cid}")


def _verify_summary(summary: Any) -> None:
    _require(isinstance(summary, dict) and tuple(summary) == SUMMARY_KEYS, "summary schema is invalid")
    expected = {
        "total_components": 16, "provider_versions_verified": 0,
        "verified_immutable_inputs": 0, "partial_inputs": 0,
        "identified_name_only": 16, "unresolved_inputs": 0,
        "archive_hashes_verified": 0, "exact_provider_recipe_found": False,
        "all_batch_inputs_resolved": False,
        "source_kit_assembly_authorized": False,
    }
    for key, value in expected.items():
        _require(summary[key] == value, f"summary value changed: {key}")
    _require(_sorted_nonempty(summary["remaining_blockers"]), "summary blockers are missing")


def _verify_inventory(root: Path, primary: dict[str, Any], inventory: dict[str, Any]) -> None:
    baseline = _load_json_bytes(_git_blob(root, "legal/source-input-inventory.json"), "baseline inventory")
    normalized = copy.deepcopy(inventory)
    current_ffmpeg = next(item for item in normalized["packages"] if item["id"] == "ffmpeg")
    baseline_ffmpeg = next(item for item in baseline["packages"] if item["id"] == "ffmpeg")
    current = {item["id"]: item for item in current_ffmpeg["external_components"]}
    original = {item["id"]: item for item in baseline_ffmpeg["external_components"]}
    _require(set(current) == set(original), "FFmpeg inventory component set changed")
    for cid in current:
        current[cid]["evidence"] = copy.deepcopy(original[cid]["evidence"])
        current[cid]["blockers"] = copy.deepcopy(original[cid]["blockers"])
    _require(
        normalized == baseline,
        "inventory structural state or shared ownership changed",
    )
    primary_records = {item["id"]: item for item in primary["components"]}
    ffmpeg = next(item for item in inventory["packages"] if item["id"] == "ffmpeg")
    records = {item["id"]: item for item in ffmpeg["external_components"]}
    for cid in IDS:
        record = records[cid]
        _require(tuple(record) == INVENTORY_KEYS, f"inventory schema changed: {cid}")
        _require(record["linkage"] == "static", f"inventory linkage changed: {cid}")
        for key in ("provider_version", "version_status", "upstream_repository", "immutable_ref", "source_archive_sha256"):
            _require(record[key] == "unresolved", f"inventory field was promoted: {cid}.{key}")
        _require(record["resolution_status"] == "identified-name-only", f"inventory status was promoted: {cid}")
        _require(tuple(record["blockers"]) == BLOCKERS, f"inventory blockers changed: {cid}")
        _evidence(record["evidence"], f"inventory {cid}")
        _require(record["evidence"] == [
            {
                "kind": "official-upstream-research", "authority": PROJECTS[cid],
                "locator": REPOSITORIES[cid],
                "claim": f"Official upstream identity for {cid} was researched separately, but no upstream version, release, commit, or archive is mapped to the Gyan package.",
                "status": "partial",
            },
            {
                "kind": "provider-package-metadata", "authority": "Gyan FFmpeg builds",
                "locator": f"legal/source-correspondence.json#packages/ffmpeg/external_components/{cid}",
                "claim": f"Provider metadata identifies component {cid} with static linkage but does not identify a version.",
                "status": "partial",
            },
        ], f"inventory evidence changed: {cid}")
        _require(primary_records[cid]["provider_version"] == record["provider_version"], f"primary/inventory version mismatch: {cid}")
    _hygiene(inventory, "source input inventory")


def _verify_feasibility(root: Path, paths: dict[str, Path], document: dict[str, Any], raw: bytes) -> None:
    for key in (
        "release_gate_reconsideration_allowed", "legal_compliance_certified",
        "source_assets_created", "source_kits_ready", "assembly_authorized",
    ):
        _require(document[key] is False, f"feasibility gate changed: {key}")
    ffmpeg = next(item for item in document["packages"] if item["id"] == "ffmpeg")
    _require(ffmpeg["verified_immutable_inputs"] == [], "FFmpeg feasibility input was promoted")
    _require(all(cid in ffmpeg["partially_identified_inputs"] for cid in IDS), "batch missing from feasibility")
    _require(ffmpeg["source_kit_status"] == "not-ready", "FFmpeg source kit was marked ready")
    outputs = []
    with tempfile.TemporaryDirectory(prefix="s9h-ffmpeg-feasibility-") as raw_dir:
        for name in ("a.json", "b.json"):
            output = Path(raw_dir) / name
            result = subprocess.run([
                sys.executable, str(root / "scripts/audit_source_kit_feasibility.py"),
                "--source-correspondence", str(paths["source-correspondence"]),
                "--source-kit-requirements", str(paths["source-kit-requirements"]),
                "--source-input-inventory", str(paths["source-input-inventory"]),
                "--output", str(output),
            ], cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _require(result.returncode == 0, "protected feasibility regeneration failed: " + result.stderr.decode("utf-8", errors="replace").strip())
            outputs.append(output.read_bytes())
    _require(outputs[0] == outputs[1] == raw, "feasibility regeneration differs")


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
        "README": _read_doc(readme), "legal README": _read_doc(legal_readme),
        "feasibility document": _read_doc(detail),
    }
    combined = "\n".join(docs.values()).casefold()
    for phrase in (
        "phase 6b2b2a1b", "16 ffmpeg codec-library names",
        "exact provider dependency versions remain unresolved",
        "upstream project identification does not prove provider use",
        "no component was promoted", "provider versions verified: 0",
        "verified immutable inputs: 0", "identified-name-only inputs: 16",
        "provider source archive hashes verified: 0",
        "feasibility counts remain unchanged",
        "no source archive hash was accepted as a provider input",
        "no source archive was committed", "no source kit was assembled",
        "historical recipe", "toolchain", "configure command", "patch set",
        "aria2 verifier owns aria2 semantics and shared gates",
        "ffmpeg batch verifier owns this ffmpeg evidence batch",
        "neither weakens the general source-kit feasibility gate",
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
    root: Path, tracked_paths: list[str] | None,
    repository_files: list[str] | None, introduced_paths: list[str] | None,
) -> None:
    if tracked_paths is None:
        tracked_paths = _git_lines(root, ["ls-files"])
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES) for item in tracked_paths), "source archive is tracked")
    if introduced_paths is None:
        introduced_paths = sorted(_changed_paths_since_baseline(root))
    _require(not any(_suffix(item.casefold(), BINARY_MEDIA_SUFFIXES) for item in introduced_paths), "binary or media was introduced")
    if repository_files is None:
        repository_files = [
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
        ]
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES) for item in repository_files), "source archive exists inside repository")


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
    approved = {
        "aomedia.googlesource.com", "bitbucket.org", "chromium.googlesource.com",
        "code.videolan.org", "git.code.sf.net", "github.com", "labs.xvid.com",
        "lame.sourceforge.io", "sourceforge.net", "svn.code.sf.net",
        "www.gyan.dev", "www.quut.com",
    }
    _require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password, f"official locator must use TLS: {label}")
    _require(parsed.hostname.casefold() in approved, f"non-official authority: {label}")
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
        raise FFmpegCodecPrimarySourceEvidenceError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
