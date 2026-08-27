from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import audit_source_kit_feasibility as feasibility_audit


BASELINE = "323dec5c118b62d78dde1f9a38d525ad44a5255f"
PACKAGE_HASH = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
CORE_REPOSITORY = "aria2/aria2"
CORE_COMMIT = "02f2d0d8472b3c38c29b4dba8c75ebd5fdd2899a"
CORE_ARCHIVE_HASH = "f0839604196b45959f72f766fdfa9df5eae6c292d48020c6475ca7eacd5d15e7"
TOP_KEYS = (
    "schema_version", "target_phase", "baseline_commit", "package_id",
    "binary_package_sha256", "provider_evidence", "research_policy",
    "components", "summary", "gate_state",
)
PROVIDER_KEYS = (
    "source_document", "provider_name", "provider_release_identity",
    "component_version_evidence", "status",
)
RESEARCH_KEYS = (
    "authority_scope", "network_mode", "source_archive_scope",
    "binary_downloads_allowed", "runtime_execution_allowed",
)
COMPONENT_KEYS = (
    "id", "provider_version", "provider_linkage", "official_project",
    "official_repository", "release_identity", "source_archive",
    "license_evidence", "version_match", "resolution_status", "evidence",
    "blockers",
)
IDENTITY_KEYS = ("kind", "value", "secondary_identity", "resolution_method")
ARCHIVE_KEYS = (
    "filename", "official_locator", "sha256", "independently_hashed",
    "upstream_checksum_status", "signature_status",
)
LICENSE_KEYS = ("classification", "source_path_or_locator", "status", "claim")
EVIDENCE_KEYS = ("kind", "authority", "locator", "claim", "status")
SUMMARY_KEYS = (
    "total_components", "verified_immutable_inputs", "partial_inputs",
    "unresolved_inputs", "archive_hashes_verified", "all_static_inputs_resolved",
    "source_kit_assembly_authorized", "remaining_blockers",
)
GATE_KEYS = (
    "legal_compliance_certified", "source_assets_created", "source_kits_ready",
    "assembly_authorized", "release_gate_reconsideration_allowed",
    "publishing_allowed",
)
IDS = ("c-ares", "expat", "gmp", "libssh2", "sqlite", "zlib")
VERIFIED = ("c-ares", "expat", "libssh2", "sqlite", "zlib")
VERSIONS = {
    "c-ares": "1.19.1", "expat": "2.5.0", "gmp": "6.3.0",
    "libssh2": "1.11.0", "sqlite": "3.43.1", "zlib": "1.3",
}
PROJECTS = {
    "c-ares": "c-ares", "expat": "Expat", "gmp": "GNU MP",
    "libssh2": "libssh2", "sqlite": "SQLite", "zlib": "zlib",
}
REPOSITORIES = {
    "c-ares": "https://github.com/c-ares/c-ares",
    "expat": "https://github.com/libexpat/libexpat",
    "gmp": "https://gmplib.org/repo/gmp/",
    "libssh2": "https://github.com/libssh2/libssh2",
    "sqlite": "https://github.com/sqlite/sqlite",
    "zlib": "https://github.com/madler/zlib",
}
IDENTITIES = {
    "c-ares": "6360e96b5cf8e5980c887ce58ef727e53d77243a",
    "expat": "654d2de0da85662fcc7644a7acd7c2dd2cfb21f0",
    "gmp": "unresolved",
    "libssh2": "1c3f1b7da588f2652260285529ec3c1f1125eb4e",
    "sqlite": "2d3a40c05c49e1a49264912b1a05bc2143ac0e7c3df588276ce80a4cbc9bd1b0",
    "zlib": "09155eaa2f9270dc4ed1fa13e2b4b2613e6e4851",
}
INVENTORY_REFS = {
    "c-ares": IDENTITIES["c-ares"], "expat": IDENTITIES["expat"],
    "libssh2": IDENTITIES["libssh2"],
    "sqlite": "f1f6a0bba16895215150081e55dda0d960494773",
    "zlib": IDENTITIES["zlib"],
}
ARCHIVES = {
    "c-ares": ("c-ares-1.19.1.tar.gz", "https://github.com/c-ares/c-ares/releases/download/cares-1_19_1/c-ares-1.19.1.tar.gz", "321700399b72ed0e037d0074c629e7741f6b2ec2dda92956abe3e9671d3e268e"),
    "expat": ("expat-2.5.0.tar.xz", "https://github.com/libexpat/libexpat/releases/download/R_2_5_0/expat-2.5.0.tar.xz", "ef2420f0232c087801abf705e89ae65f6257df6b7931d37846a193ef2e8cdcbe"),
    "gmp": ("gmp-6.3.0.tar.xz", "https://gmplib.org/download/gmp/gmp-6.3.0.tar.xz", "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898"),
    "libssh2": ("libssh2-1.11.0.tar.xz", "https://github.com/libssh2/libssh2/releases/download/libssh2-1.11.0/libssh2-1.11.0.tar.xz", "a488a22625296342ddae862de1d59633e6d446eff8417398e06674a49be3d7c2"),
    "sqlite": ("sqlite-version-3.43.1.tar.gz", "https://github.com/sqlite/sqlite/archive/refs/tags/version-3.43.1.tar.gz", "fe1bf29c5af379444ff5744f8317ad246fb865ceacc937903fe0fec0281fba2a"),
    "zlib": ("zlib-1.3.tar.xz", "https://github.com/madler/zlib/releases/download/v1.3/zlib-1.3.tar.xz", "8a9ba2898e1d0d774eca6ba5b4627a11e5588ba85c8851336eb38de4683050a7"),
}
GITHUB_ROOTS = (
    "/c-ares/c-ares", "/libexpat/libexpat", "/libssh2/libssh2",
    "/madler/zlib", "/sqlite/sqlite",
)
OTHER_HOSTS = {
    "c-ares.org", "gmplib.org", "libexpat.github.io", "libssh2.org",
    "sqlite.org", "www.sqlite.org", "zlib.net", "www.zlib.net",
}
MUTABLE = {"head", "latest", "main", "master", "nightly", "release", "stable"}
ARCHIVE_SUFFIXES = (".7z", ".gz", ".rar", ".tar", ".tar.gz", ".tar.xz", ".tgz", ".xz", ".zip")
BINARY_SUFFIXES = (".dll", ".dylib", ".exe", ".pyd", ".so")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


class Aria2PrimarySourceEvidenceError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify offline aria2 primary-source evidence")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    options = (
        "primary-evidence", "source-correspondence", "source-input-inventory",
        "source-kit-feasibility", "source-kit-requirements", "release-policy",
        "release-assets", "readme", "legal-readme", "feasibility-doc",
    )
    for option in options:
        parser.add_argument(f"--{option}", type=Path)
    args = parser.parse_args()
    overrides = {
        key.replace("_", "-"): value for key, value in vars(args).items()
        if key != "root" and value is not None
    }
    try:
        verify_repository(args.root, overrides=overrides)
    except (
        Aria2PrimarySourceEvidenceError, feasibility_audit.SourceKitFeasibilityError,
        OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError,
    ) as exc:
        print(f"aria2 primary-source evidence verification failed: {exc}", file=sys.stderr)
        return 1
    print("aria2 primary-source evidence verified")
    return 0


def verify_repository(
    root: Path,
    *,
    overrides: dict[str, Path] | None = None,
    tracked_paths: list[str] | None = None,
) -> None:
    root = root.resolve()
    _require(root.is_dir(), "repository root is unavailable")
    paths = _paths(root, overrides or {})
    primary, _ = _load_json(paths["primary-evidence"], "primary evidence")
    correspondence, requirements, inventory = feasibility_audit.load_inputs(
        paths["source-correspondence"], paths["source-kit-requirements"],
        paths["source-input-inventory"],
    )
    feasibility, feasibility_raw = feasibility_audit.load_feasibility(
        paths["source-kit-feasibility"], correspondence, requirements, inventory
    )
    _verify_primary(primary, correspondence)
    _verify_inventory(primary, inventory)
    _verify_feasibility(
        primary, feasibility, feasibility_raw, correspondence, requirements, inventory
    )
    _verify_release(paths["release-policy"], paths["release-assets"], root=root)
    _verify_docs(paths["readme"], paths["legal-readme"], paths["feasibility-doc"])
    _verify_artifacts(root, tracked_paths)


def _paths(root: Path, overrides: dict[str, Path]) -> dict[str, Path]:
    values = {
        "primary-evidence": root / "legal/primary-source-evidence-aria2.json",
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
    _require(not (set(overrides) - set(values)), "unknown path override")
    values.update(overrides)
    return values


def _verify_primary(document: dict[str, Any], correspondence: dict[str, Any]) -> None:
    _require(tuple(document) == TOP_KEYS, "primary evidence schema or field order is invalid")
    _require(document["schema_version"] == 1, "schema_version must be 1")
    _require(document["target_phase"] == "6B2B2A1a", "target phase is invalid")
    _require(document["baseline_commit"] == BASELINE, "baseline is invalid")
    _require(document["package_id"] == "aria2", "package ID is invalid")
    _require(document["binary_package_sha256"] == PACKAGE_HASH, "aria2 binary hash changed")
    provider = document["provider_evidence"]
    _require(isinstance(provider, dict) and tuple(provider) == PROVIDER_KEYS, "provider evidence schema is invalid")
    aria2_source = next(item for item in correspondence["packages"] if item["id"] == "aria2")
    _require(aria2_source["binary_package"]["sha256"] == PACKAGE_HASH, "source correspondence aria2 hash changed")
    _require(aria2_source["core_source"]["repository"] == CORE_REPOSITORY, "source correspondence aria2 repository changed")
    _require(aria2_source["core_source"]["commit"] == CORE_COMMIT, "source correspondence aria2 commit changed")
    _require(aria2_source["core_source"]["archive_sha256"] == CORE_ARCHIVE_HASH, "source correspondence aria2 archive changed")
    _require(provider == {
        "source_document": "legal/source-correspondence.json#packages/aria2/provider",
        "provider_name": aria2_source["provider"]["name"],
        "provider_release_identity": aria2_source["provider"]["release_identity"],
        "component_version_evidence": "legal/source-correspondence.json#packages/aria2/external_components",
        "status": "verified",
    }, "provider evidence changed")
    policy = document["research_policy"]
    _require(isinstance(policy, dict) and tuple(policy) == RESEARCH_KEYS, "research policy schema is invalid")
    _require(policy == {
        "authority_scope": "official-primary-sources-only", "network_mode": "read-only",
        "source_archive_scope": "source-only-temporary",
        "binary_downloads_allowed": False, "runtime_execution_allowed": False,
    }, "research policy changed")
    components = document["components"]
    _require(isinstance(components, list) and [item.get("id") for item in components] == list(IDS), "component IDs are missing, duplicated, invented, or unsorted")
    source_components = {item["id"]: item for item in aria2_source["external_components"]}
    for component in components:
        _verify_component(component, source_components[component["id"]])
    _verify_summary(document["summary"], components)
    gate = document["gate_state"]
    _require(isinstance(gate, dict) and tuple(gate) == GATE_KEYS, "gate state schema is invalid")
    for key in GATE_KEYS:
        _require(gate[key] is False, f"gate flag must remain false: {key}")
    _hygiene(document, "primary evidence")


def _verify_component(component: Any, source: dict[str, Any]) -> None:
    _require(isinstance(component, dict) and tuple(component) == COMPONENT_KEYS, "component schema is invalid")
    cid = component["id"]
    _require(component["provider_version"] == VERSIONS[cid] == source["version"], f"provider version changed: {cid}")
    _require(component["provider_linkage"] == source["linkage"] == "static", f"linkage changed: {cid}")
    _require(component["official_project"] == PROJECTS[cid], f"official project changed: {cid}")
    _require(component["official_repository"] == REPOSITORIES[cid], f"official repository changed: {cid}")
    _official(component["official_repository"], f"{cid} repository")
    identity = component["release_identity"]
    _require(isinstance(identity, dict) and tuple(identity) == IDENTITY_KEYS, f"identity schema is invalid: {cid}")
    _require(identity["value"].casefold() not in MUTABLE, f"mutable identity is forbidden: {cid}")
    _require(identity["value"] == IDENTITIES[cid], f"immutable identity changed: {cid}")
    _require(isinstance(identity["resolution_method"], str) and identity["resolution_method"], f"resolution method is missing: {cid}")
    if cid in {"c-ares", "expat", "libssh2", "zlib"}:
        _require(identity["kind"] == "git-commit" and COMMIT_RE.fullmatch(identity["value"]), f"Git identity is malformed: {cid}")
        _require(re.fullmatch(r"git-tag-object:[0-9a-f]{40}", identity["secondary_identity"]) is not None, f"tag identity is malformed: {cid}")
    elif cid == "sqlite":
        _require(identity["kind"] == "fossil-checkin" and HASH_RE.fullmatch(identity["value"]), "SQLite Fossil identity is malformed")
        _require(identity["secondary_identity"] == "git-commit:f1f6a0bba16895215150081e55dda0d960494773", "SQLite mirror mapping is missing")
    else:
        _require(identity["kind"] == "unresolved" and identity["secondary_identity"] == "not-applicable", "GMP unresolved identity is inconsistent")
    archive = component["source_archive"]
    _require(isinstance(archive, dict) and tuple(archive) == ARCHIVE_KEYS, f"archive schema is invalid: {cid}")
    filename, locator, digest = ARCHIVES[cid]
    _require((archive["filename"], archive["official_locator"], archive["sha256"]) == (filename, locator, digest), f"archive identity changed: {cid}")
    _require(HASH_RE.fullmatch(archive["sha256"]) is not None, f"archive SHA-256 is malformed: {cid}")
    _require(archive["independently_hashed"] is True, f"archive was not independently hashed: {cid}")
    _require(archive["upstream_checksum_status"] in {"verified", "published-but-not-independently-verified", "not-published", "unresolved"}, f"checksum status is invalid: {cid}")
    _require(archive["signature_status"] in {"verified", "published-but-not-verified", "not-published", "unresolved"}, f"signature status is invalid: {cid}")
    _official(locator, f"{cid} archive")
    license_record = component["license_evidence"]
    _require(isinstance(license_record, dict) and tuple(license_record) == LICENSE_KEYS, f"license schema is invalid: {cid}")
    _require(all(isinstance(license_record[key], str) and license_record[key] for key in LICENSE_KEYS), f"license evidence is incomplete: {cid}")
    _require(license_record["status"] == "verified", f"license evidence is not verified: {cid}")
    evidence = component["evidence"]
    _evidence(evidence, cid)
    _require(any(item["authority"] == "aria2 project Windows release" and item["locator"].startswith("legal/") for item in evidence), f"provider evidence is missing: {cid}")
    official_count = sum(item["status"] == "verified" and item["locator"].startswith("https://") for item in evidence)
    if cid in VERIFIED:
        _require(component["version_match"] == "exact", f"verified version match is not exact: {cid}")
        _require(component["resolution_status"] == "verified-immutable-input", f"verified status changed: {cid}")
        _require(official_count >= 2, f"primary evidence is insufficient: {cid}")
        _require(component["blockers"] == [], f"verified record retains blockers: {cid}")
    else:
        _require(component["version_match"] == "partial", "GMP version match must remain partial")
        _require(component["resolution_status"] == "partial-primary-source", "GMP status must remain partial")
        _require(_sorted_nonempty(component["blockers"]), "GMP blockers are missing or unsorted")
        _require(any("Mercurial" in item for item in component["blockers"]), "GMP Mercurial blocker is missing")
        _require(component["official_repository"].startswith("https://gmplib.org/"), "GMP unofficial mirror is forbidden")
        _require(any(item["locator"].startswith("https://gmplib.org/") for item in evidence), "GMP lacks official authority")
    if cid == "sqlite":
        fossil = identity["value"]
        mirror = identity["secondary_identity"].removeprefix("git-commit:")
        _require(any(item["kind"] == "official-fossil-release" and fossil in item["claim"] and "sqlite.org" in item["locator"] for item in evidence), "SQLite official identity evidence is missing")
        _require(any(item["kind"] == "official-mirror-mapping" and fossil in item["claim"] and mirror in item["claim"] for item in evidence), "SQLite identity mapping evidence is missing")


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
            _require(record["locator"].startswith("legal/"), f"non-official locator is forbidden: {label}")
        keys.append(tuple(record[key] for key in EVIDENCE_KEYS))
    _require(keys == sorted(set(keys)), f"evidence is duplicated or unsorted: {label}")


def _official(locator: str, label: str) -> None:
    parsed = urlparse(locator)
    _require(parsed.scheme == "https" and not parsed.username and not parsed.password, f"official locator must use TLS: {label}")
    host, path = (parsed.hostname or "").casefold(), parsed.path.casefold()
    if host == "github.com":
        _require(any(path == root or path.startswith(root + "/") for root in GITHUB_ROOTS), f"unapproved GitHub authority: {label}")
    else:
        _require(host in OTHER_HOSTS, f"non-official authority: {label}")
    _require(not parsed.query and not parsed.fragment, f"official locator contains query or fragment: {label}")


def _verify_summary(summary: Any, components: list[dict[str, Any]]) -> None:
    _require(isinstance(summary, dict) and tuple(summary) == SUMMARY_KEYS, "summary schema is invalid")
    verified = sorted(item["id"] for item in components if item["resolution_status"] == "verified-immutable-input")
    partial = sorted(item["id"] for item in components if item["resolution_status"] == "partial-primary-source")
    unresolved = sorted(item["id"] for item in components if item["resolution_status"] == "unresolved")
    hashed = sorted(item["id"] for item in components if item["source_archive"]["independently_hashed"] and HASH_RE.fullmatch(item["source_archive"]["sha256"]))
    _require(summary["total_components"] == 6, "summary component total changed")
    _require(summary["verified_immutable_inputs"] == verified == list(VERIFIED), "verified summary is stale")
    _require(summary["partial_inputs"] == partial == ["gmp"], "partial summary is stale")
    _require(summary["unresolved_inputs"] == unresolved == [], "unresolved summary is stale")
    _require(summary["archive_hashes_verified"] == hashed == list(IDS), "archive summary is stale")
    _require(summary["all_static_inputs_resolved"] is False, "static inputs cannot be marked resolved")
    _require(summary["source_kit_assembly_authorized"] is False, "assembly cannot be authorized")
    _require(_sorted_nonempty(summary["remaining_blockers"]), "remaining blockers are missing or unsorted")


def _verify_inventory(primary: dict[str, Any], inventory: dict[str, Any]) -> None:
    for key in (
        "release_gate_reconsideration_allowed",
        "legal_compliance_certified",
        "source_assets_created",
    ):
        _require(inventory[key] is False, f"inventory shared gate must remain false: {key}")
    _require(
        all(package["package_status"] == "blocked" for package in inventory["packages"]),
        "source input package gate changed",
    )
    aria2 = next(item for item in inventory["packages"] if item["id"] == "aria2")
    _require(aria2["id"] == "aria2", "inventory aria2 package ID changed")
    _require(aria2["binary_package_sha256"] == PACKAGE_HASH, "inventory aria2 hash changed")
    core = aria2["core_source"]
    _require(core["repository"] == CORE_REPOSITORY, "inventory aria2 core repository changed")
    _require(core["commit"] == CORE_COMMIT, "inventory aria2 core commit changed")
    _require(core["archive_sha256"] == CORE_ARCHIVE_HASH, "inventory aria2 core archive changed")
    _require(core["license_path"] == "COPYING", "inventory aria2 core license path changed")
    _require(core["status"] == "verified" and core["blockers"] == [], "inventory aria2 core state changed")
    _evidence(core["evidence"], "aria2 core source")
    records = {item["id"]: item for item in aria2["external_components"]}
    evidence = {item["id"]: item for item in primary["components"]}
    _require(tuple(sorted(records)) == IDS, "inventory component set changed")
    for cid in IDS:
        record, primary_record = records[cid], evidence[cid]
        _require(record["provider_version"] == primary_record["provider_version"], f"inventory version mismatch: {cid}")
        _require(record["linkage"] == primary_record["provider_linkage"], f"inventory linkage mismatch: {cid}")
        if cid in VERIFIED:
            _require(primary_record["resolution_status"] == "verified-immutable-input", f"promotion lacks evidence: {cid}")
            _require(record["version_status"] == "verified", f"inventory version is not verified: {cid}")
            _require(record["upstream_repository"] == primary_record["official_repository"], f"inventory repository mismatch: {cid}")
            _require(record["immutable_ref"] == INVENTORY_REFS[cid], f"inventory commit mismatch: {cid}")
            _require(record["source_archive_sha256"] == primary_record["source_archive"]["sha256"], f"inventory archive mismatch: {cid}")
            _require(record["resolution_status"] == "verified-immutable-input" and record["blockers"] == [], f"inventory verified state is inconsistent: {cid}")
        else:
            _require(record["version_status"] == "provider-identified", "GMP inventory version status changed")
            _require(record["upstream_repository"] == record["immutable_ref"] == record["source_archive_sha256"] == "unresolved", "GMP promotion fields must remain unresolved")
            _require(record["resolution_status"] == "identified-version-only" and _sorted_nonempty(record["blockers"]), "GMP inventory state is inconsistent")
        _evidence(record["evidence"], f"aria2 inventory {cid}")
    toolchain = aria2["toolchain"]
    _require(toolchain["host"] == "Ubuntu Linux" and toolchain["compiler"] == "mingw-w64", "aria2 toolchain identity changed")
    _require(toolchain["compiler_version"] == "unresolved" and toolchain["status"] == "partial", "aria2 toolchain state changed")
    _require(_sorted_nonempty(toolchain["blockers"]), "aria2 toolchain blockers are missing")
    orchestration = aria2["build_orchestration"]
    for key in ("provider_repository", "immutable_ref", "exact_configuration", "patch_status", "reproducible_entrypoint"):
        _require(orchestration[key] == "unresolved", f"aria2 build orchestration field changed: {key}")
    _require(orchestration["status"] == "partial" and _sorted_nonempty(orchestration["blockers"]), "aria2 build orchestration state changed")
    _require(aria2["package_status"] == "blocked", "aria2 package is not blocked")
    _require(_sorted_nonempty(aria2["blockers"]), "aria2 package blockers are missing")


def _verify_feasibility(
    primary: dict[str, Any], feasibility: dict[str, Any], raw: bytes,
    correspondence: dict[str, Any], requirements: dict[str, Any], inventory: dict[str, Any],
) -> None:
    generated = feasibility_audit.generate_feasibility(correspondence, requirements, inventory)
    _require(feasibility == generated, "committed feasibility data is stale")
    _require(raw == feasibility_audit.canonical_json_bytes(generated), "stale generated feasibility bytes")
    for key in ("release_gate_reconsideration_allowed", "legal_compliance_certified", "source_assets_created", "source_kits_ready", "assembly_authorized"):
        _require(feasibility[key] is False, f"feasibility flag must remain false: {key}")
    _require(feasibility["overall_status"] == "blocked-inventory-recorded" and feasibility["next_phase"] == "6B2B2B-not-authorized", "feasibility gate state changed")
    aria2 = next(item for item in feasibility["packages"] if item["id"] == "aria2")
    _require(aria2["binary_package_sha256"] == PACKAGE_HASH, "aria2 feasibility package hash changed")
    _require(aria2["total_external_components"] == 6, "aria2 feasibility component count changed")
    _require(aria2["static_components"] == list(IDS) and aria2["system_components"] == [], "aria2 feasibility component set changed")
    _require(aria2["verified_immutable_inputs"] == primary["summary"]["verified_immutable_inputs"], "evidence/feasibility verified mismatch")
    _require(aria2["partially_identified_inputs"] == primary["summary"]["partial_inputs"], "evidence/feasibility partial mismatch")
    _require(aria2["unresolved_inputs"] == primary["summary"]["unresolved_inputs"], "evidence/feasibility unresolved mismatch")
    _require(aria2["toolchain_status"] == "partial", "aria2 feasibility toolchain state changed")
    _require(aria2["build_orchestration_status"] == "partial", "aria2 feasibility build orchestration state changed")
    _require(aria2["source_kit_status"] == "not-ready", "aria2 source kit must remain not ready")


def _verify_release(policy_path: Path, assets_path: Path, *, root: Path) -> None:
    policy, _ = _load_json(policy_path, "release policy")
    assets, _ = _load_json(assets_path, "release assets")
    import verify_release_legal_gate as live_gate
    try:
        policy = json.loads(live_gate.historical_control_bytes(root, "legal/release-policy.json", policy_path))
        assets = json.loads(live_gate.historical_control_bytes(root, "legal/release-assets-v2.json", assets_path))
    except live_gate.ReleaseLegalGateError as exc:
        raise Aria2PrimarySourceEvidenceError(str(exc)) from exc
    _require(policy.get("policy_mode") == "fail-closed", "release policy is not fail-closed")
    for key in ("legal_compliance_certified", "source_availability_certified", "release_payload_integrated"):
        _require(policy.get(key) is False, f"release policy flag must remain false: {key}")
    _require(len(policy.get("releases", [])) == 5 and all(item.get("status") == "blocked" for item in policy["releases"]), "direct release gates changed")
    _require(assets.get("release_readiness") == "blocked", "release assets are not blocked")
    for key in ("legal_compliance_certified", "source_availability_certified", "source_kits_ready"):
        _require(assets.get(key) is False, f"release asset flag must remain false: {key}")
    _require(all(item.get("status") == "not-ready" for item in assets.get("required_source_asset_templates", [])), "source asset template is ready")


def _verify_docs(readme: Path, legal_readme: Path, detail: Path) -> None:
    docs = {"README": _read_doc(readme), "legal README": _read_doc(legal_readme), "feasibility document": _read_doc(detail)}
    combined = "\n".join(docs.values()).casefold()
    for phrase in ("phase 6b2b2a1a", "5 verified", "1 partial", "0 unresolved", "no source kit was assembled", "assembly remains unauthorized", "publishing remains blocked", "existing releases are not retroactively certified", "not legal advice"):
        _require(phrase in combined, f"documentation concept is missing: {phrase}")
    detailed = docs["feasibility document"].casefold()
    for phrase in ("provider identification is not the same as immutable upstream resolution", "upstream resolution is not proof of exact provider build reproduction", "archive hashing does not prove the binary incorporated unmodified source", "toolchain and build orchestration remain incomplete"):
        _require(phrase in detailed, f"feasibility concept is missing: {phrase}")
    for label, text in docs.items():
        _hygiene(text, label)
        _require(not any(pattern.search(text) for pattern in feasibility_audit.UNSUPPORTED_CLAIM_RES), f"unsupported readiness claim in {label}")


def _verify_artifacts(root: Path, tracked_paths: list[str] | None) -> None:
    if tracked_paths is None:
        result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, stdout=subprocess.PIPE)
        tracked_paths = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    _require(not any(_suffix(item.casefold(), ARCHIVE_SUFFIXES) for item in tracked_paths), "source archive is tracked in Git")
    changed = subprocess.run(["git", "diff", "--name-only", BASELINE, "--"], cwd=root, check=True, text=True, stdout=subprocess.PIPE).stdout.splitlines()
    _require(not any(_suffix(item.casefold(), BINARY_SUFFIXES) for item in changed), "binary or runtime was introduced")
    import verify_release_legal_gate as live_gate
    paths = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]
    for rel in live_gate.exclude_verified_release_inputs(root, paths):
        _require(not _suffix(rel.casefold(), ARCHIVE_SUFFIXES), f"source archive is present under repository: {rel}")


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{label} contains UTF-8 BOM")
    _require(b"\r" not in raw, f"{label} must use LF")
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    _require(isinstance(document, dict), f"{label} root must be object")
    _require(raw == _canonical_json_bytes(document), f"{label} JSON is not canonical")
    _hygiene(document, label)
    return document, raw


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
    _require(feasibility_audit.LOCAL_PATH_RE.search(text) is None, f"local path in {label}")
    _require(feasibility_audit.TIMESTAMP_RE.search(text) is None, f"timestamp in {label}")
    _require(not any(pattern.search(text) for pattern in feasibility_audit.SECRET_RES), f"secret or signed URL in {label}")


def _sorted_nonempty(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value) and value == sorted(set(value))


def _suffix(path: str, suffixes: tuple[str, ...]) -> bool:
    return any(path.endswith(suffix) for suffix in suffixes)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise Aria2PrimarySourceEvidenceError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
