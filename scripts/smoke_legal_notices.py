from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

import verify_legal_notices as verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
Mutation = Callable[[Path], None]


def main() -> int:
    _verify_positive_repository()
    _run_checkout_policy_tests()
    _run_inventory_mutations()
    _run_phase6b1_mutations()
    _run_phase6b2a_mutations()
    _run_phase6b2b1_mutations()
    _run_license_mutations()
    _run_notice_mutations()
    _run_claim_and_hygiene_mutations()
    print("legal notices smoke tests passed")
    return 0


def _verify_positive_repository() -> None:
    inventory = verifier.verify_repository(REPO_ROOT)
    _assert(
        len(verifier._discover_top_level_legal_json(REPO_ROOT))
        == verifier.EXPECTED_TOP_LEVEL_LEGAL_JSON_COUNT,
        "top-level legal JSON count changed",
    )
    _assert(
        (REPO_ROOT / verifier.GITATTRIBUTES_PATH).read_bytes() == verifier.GITATTRIBUTES_BYTES,
        ".gitattributes policy changed",
    )
    _assert(
        verifier.CANONICAL_DOCUMENT_PATHS.count("docs/sbom-generator-feasibility.md") == 1,
        "SBOM feasibility document canonical path count changed",
    )
    _assert_effective_attributes(REPO_ROOT)
    components = inventory["components"]
    _assert(len(components) == 7, "known direct component count changed")
    _assert(
        [component["id"] for component in components] == sorted(verifier.EXPECTED_COMPONENTS),
        "component ordering changed",
    )
    raw_inventory = (REPO_ROOT / "legal/components.json").read_bytes()
    _assert(raw_inventory == verifier.canonical_inventory_bytes(inventory), "inventory is not deterministic")
    artifact_inventory = verifier.built_inventory.load_inventory(
        REPO_ROOT / "legal/built-artifact-inventory.json"
    )
    release_policy = verifier.release_gate.load_policy(REPO_ROOT / "legal/release-policy.json")
    _assert(bool(artifact_inventory["unresolved_native_members"]), "unresolved native members disappeared")
    _assert(len(release_policy["releases"]) == 4, "release policy tag count changed")
    correspondence, source_kits = verifier.source_correspondence.verify_repository(REPO_ROOT)
    _assert(correspondence["corresponding_source_complete"] is False, "source completion changed")
    _assert(all(kit["status"] == "blocked" for kit in source_kits["kits"]), "source kit is not blocked")
    release_assets = verifier.release_payload.load_asset_contract(
        REPO_ROOT / verifier.release_payload.CONTRACT_PATH
    )
    _assert(release_assets["release_readiness"] == "blocked", "release readiness changed")
    _assert(release_assets["source_kits_ready"] is False, "source kits were marked ready")

    for component in components:
        data = (REPO_ROOT / component["local_license_path"]).read_bytes()
        _assert(
            verifier.git_blob_sha1(data) == component["upstream_license_blob_sha1"],
            f"license blob changed: {component['id']}",
        )
        _assert(component["upstream_ref"].casefold() not in verifier.MUTABLE_REFS, "mutable ref found")
    apache = (REPO_ROOT / verifier.APACHE_LICENSE_PATH).read_bytes()
    _assert(hashlib.sha256(apache).hexdigest() == verifier.APACHE_SHA256, "Apache hash changed")

    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for heading in verifier.NOTICE_HEADINGS.values():
        _assert(heading in notices, f"notice heading is missing: {heading}")
    for relative in verifier.ALL_LICENSE_PATHS:
        _assert(f"`{relative}`" in notices, f"notice license path is missing: {relative}")
    _assert("requires-built-artifact-inventory" in (REPO_ROOT / "legal/README.md").read_text(encoding="utf-8"), "unresolved status is missing")
    _assert(
        not any((REPO_ROOT / name).exists() for name in verifier.ROOT_PROJECT_LICENSE_FILES),
        "project license file exists",
    )


def _run_checkout_policy_tests() -> None:
    _verify_temporary_git_worktree()
    _verify_windows_checkout_simulation()
    _verify_later_override_rejected(
        "legal/primary-source-evidence-aria2.json",
        _later_aria2_override,
        "aria2",
    )
    _verify_later_override_rejected("README.md", _later_readme_override, "README")
    mutations = (
        ("missing .gitattributes", _missing_gitattributes, "regular file"),
        ("missing legal JSON wildcard", _missing_legal_json_wildcard, "checkout rules"),
        ("legal JSON wildcard eol=crlf", _legal_json_wildcard_eol_crlf, "checkout rules"),
        ("legal JSON wildcard missing eol=lf", _legal_json_wildcard_missing_eol, "checkout rules"),
        ("legal JSON wildcard marked -text", _legal_json_wildcard_binary, "checkout rules"),
        ("missing README rule", _missing_readme_rule, "checkout rules"),
        ("README eol=crlf", _readme_eol_crlf, "checkout rules"),
        ("missing legal README rule", _missing_legal_readme_rule, "checkout rules"),
        ("legal README eol=crlf", _legal_readme_eol_crlf, "checkout rules"),
        ("missing feasibility document rule", _missing_feasibility_document_rule, "checkout rules"),
        ("feasibility document eol=crlf", _feasibility_document_eol_crlf, "checkout rules"),
        (
            "missing SBOM feasibility document rule",
            _missing_sbom_feasibility_document_rule,
            "checkout rules",
        ),
        (
            "SBOM feasibility document eol=crlf",
            _sbom_feasibility_document_eol_crlf,
            "checkout rules",
        ),
        ("missing built inventory rule", _missing_built_inventory_rule, "checkout rules"),
        ("missing components rule", _missing_components_rule, "checkout rules"),
        ("missing release assets rule", _missing_release_assets_rule, "checkout rules"),
        ("missing release policy rule", _missing_release_policy_rule, "checkout rules"),
        ("missing source correspondence rule", _missing_source_correspondence_rule, "checkout rules"),
        ("missing source kit rule", _missing_source_kit_rule, "checkout rules"),
        ("missing license rule", _missing_license_rule, "checkout rules"),
        ("components eol=crlf", _components_eol_crlf, "checkout rules"),
        ("components missing eol=lf", _components_missing_eol, "checkout rules"),
        ("components marked -text", _components_binary, "checkout rules"),
        ("licenses marked text", _licenses_text, "checkout rules"),
        ("licenses marked text eol=lf", _licenses_text_lf, "checkout rules"),
        ("narrow license rule", _narrow_license_rule, "checkout rules"),
        ("later license override", _later_license_override, "checkout rules"),
        ("catch-all components override", _catch_all_override, "checkout rules"),
        ("CRLF .gitattributes", _crlf_gitattributes, "LF line endings"),
        ("BOM .gitattributes", _bom_gitattributes, "UTF-8 BOM"),
        ("malformed attribute syntax", _malformed_gitattributes, "malformed attribute syntax"),
        ("enabled vendor notice whitespace checks", _enable_vendor_notice_whitespace, "unsupported attributes"),
        ("local absolute attribute path", _local_attribute_path, "local absolute path"),
    )
    for label, mutation, expected_message in mutations:
        _expect_failure(label, mutation, expected_message, initialize_git=True)


def _verify_temporary_git_worktree() -> None:
    with tempfile.TemporaryDirectory(prefix="legal-policy-git-") as temp:
        root = Path(temp) / "repo"
        _copy_fixture(root)
        _initialize_git_repository(root, commit=False)
        verifier.verify_repository(root)
        _assert_effective_attributes(root)


def _verify_windows_checkout_simulation() -> None:
    with tempfile.TemporaryDirectory(prefix="legal-policy-checkout-") as temp:
        temp_root = Path(temp)
        source = temp_root / "source"
        checkout = temp_root / "checkout"
        _copy_fixture(source)
        json_probe_relative = "legal/checkout-policy-probe.json"
        json_probe_bytes = b'{\n  "checkout_policy_probe": true\n}\n'
        docs_probe_relative = "docs/checkout-policy-probe.md"
        docs_probe_bytes = b"# Checkout policy probe\n"
        (source / json_probe_relative).write_bytes(json_probe_bytes)
        (source / docs_probe_relative).write_bytes(docs_probe_bytes)
        commit = _initialize_git_repository(source, commit=True)
        _run_git_command("clone", "--no-local", "--no-checkout", str(source), str(checkout))
        _git(checkout, "config", "core.autocrlf", "true")
        _git(checkout, "config", "core.eol", "native")
        _git(checkout, "checkout", "--detach", commit)

        policy = (checkout / verifier.GITATTRIBUTES_PATH).read_bytes()
        _assert(b"\r" not in policy, ".gitattributes changed line endings in Windows-style checkout")
        _assert(policy == _git_blob(source, commit, verifier.GITATTRIBUTES_PATH), ".gitattributes blob mismatch")

        legal_json_paths = verifier._discover_top_level_legal_json(source)
        _assert(
            len(legal_json_paths) == verifier.EXPECTED_TOP_LEVEL_LEGAL_JSON_COUNT + 1,
            "future legal JSON probe count changed",
        )
        for relative in legal_json_paths:
            data = (checkout / relative).read_bytes()
            _assert(b"\n" in data and b"\r" not in data, f"{relative} is not LF-only after checkout")
            _assert(data == _git_blob(source, commit, relative), f"{relative} blob mismatch")
        _assert(
            (checkout / json_probe_relative).read_bytes() == json_probe_bytes,
            "future legal JSON probe changed",
        )

        for relative in verifier.CANONICAL_DOCUMENT_PATHS:
            data = (checkout / relative).read_bytes()
            _assert(b"\n" in data and b"\r" not in data, f"{relative} is not LF-only after checkout")
            _assert(data == _git_blob(source, commit, relative), f"{relative} blob mismatch")

        json_probe_attributes = _git(
            checkout,
            "check-attr",
            "text",
            "eol",
            "--",
            json_probe_relative,
        ).splitlines()
        _assert(
            f"{json_probe_relative}: text: set" in json_probe_attributes,
            "future legal JSON probe text attribute changed",
        )
        _assert(
            f"{json_probe_relative}: eol: lf" in json_probe_attributes,
            "future legal JSON probe eol attribute changed",
        )

        docs_probe = (checkout / docs_probe_relative).read_bytes()
        docs_probe_blob = _git_blob(source, commit, docs_probe_relative)
        docs_probe_attributes = _git(
            checkout,
            "check-attr",
            "text",
            "eol",
            "--",
            docs_probe_relative,
        ).splitlines()
        _assert(docs_probe_relative not in verifier.CANONICAL_DOCUMENT_PATHS, "docs probe became canonical")
        _assert(
            f"{docs_probe_relative}: text: unspecified" in docs_probe_attributes,
            "unlisted Markdown text attribute changed",
        )
        _assert(
            f"{docs_probe_relative}: eol: unspecified" in docs_probe_attributes,
            "unlisted Markdown eol attribute changed",
        )
        _assert(b"\r" in docs_probe and docs_probe != docs_probe_blob, "unlisted Markdown was unexpectedly pinned")

        preserved = 0
        for relative in verifier.ALL_LICENSE_PATHS:
            data = (checkout / relative).read_bytes()
            _assert(data == _git_blob(source, commit, relative), f"license checkout changed bytes: {relative}")
            preserved += 1
        _assert(preserved == 8, "license checkout preservation count changed")
        _assert_effective_attributes(checkout)
        (checkout / json_probe_relative).unlink()
        verifier.verify_repository(checkout)
        _assert_effective_attributes(checkout)
        _assert(not (REPO_ROOT / json_probe_relative).exists(), "future legal JSON probe escaped fixture")
        _assert(not (REPO_ROOT / docs_probe_relative).exists(), "docs probe escaped fixture")


def _assert_effective_attributes(root: Path) -> None:
    legal_json_paths = verifier._discover_top_level_legal_json(root)
    output = _git(
        root,
        "check-attr",
        "text",
        "eol",
        "--",
        ".gitattributes",
        *legal_json_paths,
        *verifier.CANONICAL_DOCUMENT_PATHS,
    )
    required = {
        ".gitattributes: text: set",
        ".gitattributes: eol: lf",
    }
    for relative in (*legal_json_paths, *verifier.CANONICAL_DOCUMENT_PATHS):
        required.add(f"{relative}: text: set")
        required.add(f"{relative}: eol: lf")
    _assert(required.issubset(set(output.splitlines())), "effective text/eol attributes changed")
    for relative in verifier.ALL_LICENSE_PATHS:
        output = _git(root, "check-attr", "text", "--", relative)
        _assert(output == f"{relative}: text: unset", f"license text attribute changed: {relative}")


def _run_inventory_mutations() -> None:
    _expect_failure("missing component", _missing_component, "component set")
    _expect_failure("duplicate component ID", _duplicate_component, "unique")
    _expect_failure("unsorted component list", _unsorted_components, "sorted")
    _expect_failure("unknown JSON field", _unknown_inventory_field, "top-level schema")
    _expect_failure("changed upstream ref", _changed_upstream_ref, "upstream_ref")
    _expect_failure("mutable latest ref", _latest_upstream_ref, "mutable upstream ref")
    _expect_failure("wrong component version", _wrong_component_version, "version")
    _expect_failure("wrong license path", _wrong_license_path, "local_license_path")
    _expect_failure("missing license file", _missing_license_file, "required file is missing")
    _expect_failure("wrong Git blob SHA", _wrong_git_blob_sha, "upstream_license_blob_sha1")
    _expect_failure("wrong Apache SHA-256", _wrong_apache_sha, "Apache SHA-256")
    _expect_failure("malformed JSON", _malformed_json)
    _expect_failure("altered release integration status", _alter_release_status, "release integration status")


def _run_phase6b1_mutations() -> None:
    _expect_failure("missing built inventory", _missing_built_inventory, "top-level legal JSON count")
    _expect_failure("missing release policy", _missing_release_policy, "top-level legal JSON count")
    _expect_failure("release policy allows publishing", _allow_release, "blocked")
    _expect_failure("release-ready claim", _add_release_ready_claim, "unsupported project claim")
    _expect_failure("source-complete claim", _add_source_complete_claim, "unsupported project claim")
    _expect_failure("exhaustive inventory claim", _add_exhaustive_inventory_claim, "unsupported project claim")


def _run_phase6b2a_mutations() -> None:
    _expect_failure("missing source correspondence", _missing_source_correspondence, "top-level legal JSON count")
    _expect_failure("missing source kit requirements", _missing_source_kit_requirements, "top-level legal JSON count")
    _expect_failure("source kit unblocked", _unblock_source_kit, "must remain blocked")
    _expect_failure("source gate enabled", _enable_source_gate, "fail-closed")
    _expect_failure("source completion enabled", _mark_source_complete, "must remain incomplete")
    _expect_failure(
        "source documentation reference removed",
        _remove_source_documentation_reference,
        "legal/source-correspondence.json",
    )


def _run_phase6b2b1_mutations() -> None:
    _expect_failure("missing release-assets contract", _missing_release_assets, "top-level legal JSON count")
    _expect_failure(
        "missing release-assets v3 contract",
        _missing_release_assets_v3,
        "top-level legal JSON count",
    )
    _expect_failure("wrong bundle format", _wrong_bundle_format, "asset contract")
    _expect_failure("wrong legal payload format", _wrong_legal_payload_format, "asset contract")
    _expect_failure("release readiness ready", _release_readiness_ready, "asset contract")
    _expect_failure("source kits ready", _source_kits_ready, "asset contract")
    _expect_failure("missing source asset template", _missing_source_asset_template, "asset contract")
    _expect_failure("source asset status ready", _source_asset_status_ready, "asset contract")
    _expect_failure(
        "docs claim publishing enabled",
        _add_publishing_enabled_claim,
        "unsupported project claim",
    )
    _expect_failure(
        "docs claim complete Corresponding Source",
        _add_complete_corresponding_source_claim,
        "unsupported project claim",
    )


def _run_license_mutations() -> None:
    for relative in verifier.ALL_LICENSE_PATHS:
        _expect_failure(
            f"modified license byte: {relative}",
            lambda root, relative=relative: _modify_license_byte(root, relative),
            "mismatch",
        )
        _expect_failure(
            f"CRLF license conversion: {relative}",
            lambda root, relative=relative: _convert_license_to_crlf(root, relative),
            "line endings",
        )
        _expect_failure(
            f"BOM license prefix: {relative}",
            lambda root, relative=relative: _prefix_license_bom(root, relative),
            "BOM",
        )


def _run_notice_mutations() -> None:
    _expect_failure("undocumented component", _remove_component_notice, "component is undocumented")
    _expect_failure("undocumented license path", _remove_license_path_notice, "license path is undocumented")
    _expect_failure("removed GPL source warning", _remove_gpl_warning, "GPL source-availability warning")
    _expect_failure("attribution-only GPL claim", _add_attribution_claim, "unsupported attribution claim")


def _run_claim_and_hygiene_mutations() -> None:
    _expect_failure("project MIT claim", _add_mit_project_claim, "unsupported project claim")
    _expect_failure("project openness claim", _add_open_project_claim, "unsupported project claim")
    _expect_failure("complete compliance claim", _add_compliance_claim, "unsupported project claim")
    _expect_failure("root project LICENSE", _add_root_license, "not allowed")
    _expect_failure("local developer path", _add_local_path, "local absolute path")
    _expect_failure("secret-like token", _add_secret_token, "secret-like value")


def _expect_failure(
    label: str,
    mutation: Mutation,
    expected_message: str | None = None,
    *,
    initialize_git: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix="legal-notices-smoke-") as temp:
        root = Path(temp) / "repo"
        _copy_fixture(root)
        mutation(root)
        if initialize_git:
            _initialize_git_repository(root, commit=False)
        try:
            verifier.verify_repository(root)
        except (
            verifier.LegalVerificationError,
            verifier.built_inventory.InventoryError,
            verifier.release_gate.ReleaseLegalGateError,
            verifier.release_payload.LegalPayloadError,
            verifier.source_correspondence.SourceCorrespondenceError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            if expected_message is not None:
                _assert(expected_message.casefold() in str(exc).casefold(), f"{label}: unexpected failure: {exc}")
        else:
            raise AssertionError(f"mutation was not rejected: {label}")


def _copy_fixture(root: Path) -> None:
    root.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / verifier.GITATTRIBUTES_PATH, root / verifier.GITATTRIBUTES_PATH)
    for relative in ("README.md", "THIRD_PARTY_NOTICES.md", "VERSION"):
        shutil.copy2(REPO_ROOT / relative, root / relative)
    shutil.copytree(REPO_ROOT / "legal", root / "legal")
    (root / "docs").mkdir()
    shutil.copy2(
        REPO_ROOT / "docs/source-kit-feasibility.md",
        root / "docs/source-kit-feasibility.md",
    )
    shutil.copy2(
        REPO_ROOT / "docs/release-sbom.md",
        root / "docs/release-sbom.md",
    )
    shutil.copy2(
        REPO_ROOT / "docs/sbom-generator-feasibility.md",
        root / "docs/sbom-generator-feasibility.md",
    )

    (root / ".github").mkdir()
    shutil.copy2(REPO_ROOT / ".github/build-dependencies.json", root / ".github/build-dependencies.json")
    shutil.copytree(REPO_ROOT / ".github/workflows", root / ".github/workflows")

    (root / "scripts").mkdir()
    for name in ("build_release_v1_3_0.ps1", "build_release_v1_3_1.ps1"):
        shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)


def _initialize_git_repository(root: Path, *, commit: bool) -> str:
    _git(root, "init")
    _git(root, "config", "user.name", "Legal Smoke")
    _git(root, "config", "user.email", "legal-smoke@example.invalid")
    if not commit:
        return ""
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "Create legal fixture")
    return _git(root, "rev-parse", "HEAD")


def _git(root: Path, *args: str) -> str:
    result = _run_git_command("-C", str(root), *args)
    return result.stdout.decode("utf-8", errors="replace").strip()


def _git_blob(root: Path, commit: str, relative: str) -> bytes:
    return _run_git_command("-C", str(root), "show", f"{commit}:{relative}").stdout


def _run_git_command(*args: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    _assert(git is not None, "Git is required for legal checkout smoke tests")
    result = subprocess.run([git, *args], capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise AssertionError(f"Git command failed ({' '.join(args)}): {stderr}")
    return result


def _rewrite_gitattributes(root: Path, transform: Callable[[bytes], bytes]) -> None:
    path = root / verifier.GITATTRIBUTES_PATH
    path.write_bytes(transform(path.read_bytes()))


def _missing_gitattributes(root: Path) -> None:
    (root / verifier.GITATTRIBUTES_PATH).unlink()


def _missing_legal_json_wildcard(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/*.json text eol=lf\n", b""))


def _legal_json_wildcard_eol_crlf(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/*.json text eol=lf", b"/legal/*.json text eol=crlf"),
    )


def _legal_json_wildcard_missing_eol(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/*.json text eol=lf", b"/legal/*.json text"),
    )


def _legal_json_wildcard_binary(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/*.json text eol=lf", b"/legal/*.json -text"),
    )


def _missing_readme_rule(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/README.md text eol=lf\n", b""))


def _readme_eol_crlf(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/README.md text eol=lf", b"/README.md text eol=crlf"),
    )


def _missing_legal_readme_rule(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/README.md text eol=lf\n", b""))


def _legal_readme_eol_crlf(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(
            b"/legal/README.md text eol=lf",
            b"/legal/README.md text eol=crlf",
        ),
    )


def _missing_feasibility_document_rule(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/docs/source-kit-feasibility.md text eol=lf\n", b""),
    )


def _feasibility_document_eol_crlf(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(
            b"/docs/source-kit-feasibility.md text eol=lf",
            b"/docs/source-kit-feasibility.md text eol=crlf",
        ),
    )


def _missing_sbom_feasibility_document_rule(root: Path) -> None:
    target = b"/docs/sbom-generator-feasibility.md text eol=lf\n"

    def transform(data: bytes) -> bytes:
        _assert(data.count(target) == 1, "SBOM feasibility document rule target count changed")
        return data.replace(target, b"", 1)

    _rewrite_gitattributes(root, transform)


def _sbom_feasibility_document_eol_crlf(root: Path) -> None:
    target = b"/docs/sbom-generator-feasibility.md text eol=lf"
    replacement = b"/docs/sbom-generator-feasibility.md text eol=crlf"

    def transform(data: bytes) -> bytes:
        _assert(data.count(target) == 1, "SBOM feasibility document eol target count changed")
        return data.replace(target, replacement, 1)

    _rewrite_gitattributes(root, transform)


def _verify_later_override_rejected(
    relative: str,
    mutation: Mutation,
    label: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="legal-policy-later-override-") as temp:
        root = Path(temp) / "repo"
        _copy_fixture(root)
        mutation(root)
        _initialize_git_repository(root, commit=False)
        output = _git(root, "check-attr", "text", "eol", "--", relative).splitlines()
        _assert(f"{relative}: text: set" in output, f"{label} override text attribute changed")
        _assert(f"{relative}: eol: crlf" in output, f"{label} override was not effective")
        try:
            verifier.verify_repository(root)
        except verifier.LegalVerificationError as exc:
            _assert("checkout rules" in str(exc), f"{label} override: unexpected failure: {exc}")
        else:
            raise AssertionError(f"later {label} eol=crlf override was not rejected")


def _later_aria2_override(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data + b"/legal/primary-source-evidence-aria2.json text eol=crlf\n",
    )


def _later_readme_override(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data + b"/README.md text eol=crlf\n")


def _missing_components_rule(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/components.json text eol=lf\n", b""))


def _missing_built_inventory_rule(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/built-artifact-inventory.json text eol=lf\n", b""),
    )


def _missing_release_assets_rule(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/release-assets-v2.json text eol=lf\n", b""),
    )


def _missing_release_policy_rule(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/release-policy.json text eol=lf\n", b""),
    )


def _missing_source_correspondence_rule(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/source-correspondence.json text eol=lf\n", b""),
    )


def _missing_source_kit_rule(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(b"/legal/source-kit-requirements.json text eol=lf\n", b""),
    )


def _missing_source_correspondence(root: Path) -> None:
    (root / verifier.source_correspondence.CORRESPONDENCE_PATH).unlink()


def _missing_source_kit_requirements(root: Path) -> None:
    (root / verifier.source_correspondence.KIT_PATH).unlink()


def _missing_release_assets(root: Path) -> None:
    (root / verifier.release_payload.CONTRACT_PATH).unlink()


def _missing_release_assets_v3(root: Path) -> None:
    (root / "legal" / "release-assets-v3.json").unlink()


def _mutate_release_assets(root: Path, mutation: Callable[[dict], None]) -> None:
    path = root / verifier.release_payload.CONTRACT_PATH
    document = json.loads(path.read_text(encoding="utf-8"))
    mutation(document)
    path.write_bytes((json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))


def _wrong_bundle_format(root: Path) -> None:
    _mutate_release_assets(root, lambda document: document.update(bundle_format="s9h-release-bundle-v1"))


def _wrong_legal_payload_format(root: Path) -> None:
    _mutate_release_assets(root, lambda document: document.update(legal_payload_format="invalid"))


def _release_readiness_ready(root: Path) -> None:
    _mutate_release_assets(root, lambda document: document.update(release_readiness="ready"))


def _source_kits_ready(root: Path) -> None:
    _mutate_release_assets(root, lambda document: document.update(source_kits_ready=True))


def _missing_source_asset_template(root: Path) -> None:
    _mutate_release_assets(root, lambda document: document["required_source_asset_templates"].pop())


def _source_asset_status_ready(root: Path) -> None:
    _mutate_release_assets(
        root,
        lambda document: document["required_source_asset_templates"][0].update(status="ready"),
    )


def _mutate_source_json(root: Path, relative: str, mutation: Callable[[dict], None]) -> None:
    path = root / relative
    document = json.loads(path.read_text(encoding="utf-8"))
    mutation(document)
    path.write_bytes(verifier.source_correspondence.canonical_json_bytes(document))


def _unblock_source_kit(root: Path) -> None:
    _mutate_source_json(
        root,
        verifier.source_correspondence.KIT_PATH,
        lambda document: document["kits"][0].update(status="ready"),
    )


def _enable_source_gate(root: Path) -> None:
    _mutate_source_json(
        root,
        verifier.source_correspondence.CORRESPONDENCE_PATH,
        lambda document: document.update(release_gate_status="open"),
    )


def _mark_source_complete(root: Path) -> None:
    _mutate_source_json(
        root,
        verifier.source_correspondence.CORRESPONDENCE_PATH,
        lambda document: document.update(corresponding_source_complete=True),
    )


def _remove_source_documentation_reference(root: Path) -> None:
    path = root / "README.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("legal/source-correspondence.json", "source-audit.json"),
        encoding="utf-8",
        newline="\n",
    )


def _missing_license_rule(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/licenses/** -text\n", b""))


def _components_eol_crlf(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"components.json text eol=lf", b"components.json text eol=crlf"))


def _components_missing_eol(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"components.json text eol=lf", b"components.json text"))


def _components_binary(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"components.json text eol=lf", b"components.json -text"))


def _licenses_text(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/licenses/** -text", b"/legal/licenses/** text"))


def _licenses_text_lf(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/licenses/** -text", b"/legal/licenses/** text eol=lf"))


def _narrow_license_rule(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"/legal/licenses/** -text", b"/legal/licenses/*.txt -text"))


def _later_license_override(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data + b"/legal/licenses/** text\n")


def _catch_all_override(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data + b"* text eol=crlf\n")


def _crlf_gitattributes(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data.replace(b"\n", b"\r\n"))


def _bom_gitattributes(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: b"\xef\xbb\xbf" + data)


def _malformed_gitattributes(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data + b"malformed\n")


def _enable_vendor_notice_whitespace(root: Path) -> None:
    _rewrite_gitattributes(
        root,
        lambda data: data.replace(
            b"fastjsonschema-2.21.2-LICENSE.txt -text -whitespace",
            b"fastjsonschema-2.21.2-LICENSE.txt -text whitespace",
        ),
    )


def _local_attribute_path(root: Path) -> None:
    _rewrite_gitattributes(root, lambda data: data + b"C:\\Users\\developer\\license.txt text\n")


def _load_inventory(root: Path) -> dict:
    return json.loads((root / "legal/components.json").read_text(encoding="utf-8"))


def _write_inventory(root: Path, inventory: dict) -> None:
    (root / "legal/components.json").write_bytes(verifier.canonical_inventory_bytes(inventory))


def _component(inventory: dict, component_id: str) -> dict:
    return next(component for component in inventory["components"] if component["id"] == component_id)


def _mutate_inventory(root: Path, mutation: Callable[[dict], None]) -> None:
    inventory = _load_inventory(root)
    mutation(inventory)
    _write_inventory(root, inventory)


def _missing_component(root: Path) -> None:
    _mutate_inventory(root, lambda inventory: inventory["components"].pop())


def _duplicate_component(root: Path) -> None:
    _mutate_inventory(root, lambda inventory: inventory["components"].append(copy.deepcopy(inventory["components"][0])))


def _unsorted_components(root: Path) -> None:
    def mutate(inventory: dict) -> None:
        inventory["components"][0], inventory["components"][1] = inventory["components"][1], inventory["components"][0]

    _mutate_inventory(root, mutate)


def _unknown_inventory_field(root: Path) -> None:
    _mutate_inventory(root, lambda inventory: inventory.__setitem__("unexpected", True))


def _changed_upstream_ref(root: Path) -> None:
    _mutate_inventory(root, lambda inventory: _component(inventory, "aria2").__setitem__("upstream_ref", "release-1.36.0"))


def _latest_upstream_ref(root: Path) -> None:
    _mutate_inventory(root, lambda inventory: _component(inventory, "aria2").__setitem__("upstream_ref", "latest"))


def _wrong_component_version(root: Path) -> None:
    _mutate_inventory(root, lambda inventory: _component(inventory, "deno").__setitem__("version", "2.7.13"))


def _wrong_license_path(root: Path) -> None:
    _mutate_inventory(
        root,
        lambda inventory: _component(inventory, "yt-dlp").__setitem__(
            "local_license_path", "legal/licenses/yt-dlp-wrong.txt"
        ),
    )


def _missing_license_file(root: Path) -> None:
    (root / verifier.EXPECTED_COMPONENTS["deno"]["local_license_path"]).unlink()


def _wrong_git_blob_sha(root: Path) -> None:
    _mutate_inventory(
        root,
        lambda inventory: _component(inventory, "python").__setitem__("upstream_license_blob_sha1", "0" * 40),
    )


def _wrong_apache_sha(root: Path) -> None:
    def mutate(inventory: dict) -> None:
        component = _component(inventory, "pyinstaller")
        component["notes"] = [note.replace(verifier.APACHE_SHA256, "0" * 64) for note in component["notes"]]

    _mutate_inventory(root, mutate)


def _malformed_json(root: Path) -> None:
    (root / "legal/components.json").write_bytes(b"{malformed\n")


def _alter_release_status(root: Path) -> None:
    _mutate_inventory(
        root,
        lambda inventory: inventory.__setitem__("release_integration_status", "deferred-to-phase-6b"),
    )


def _missing_built_inventory(root: Path) -> None:
    (root / "legal/built-artifact-inventory.json").unlink()


def _missing_release_policy(root: Path) -> None:
    (root / "legal/release-policy.json").unlink()


def _allow_release(root: Path) -> None:
    path = root / "legal/release-policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["releases"][0]["status"] = "allow"
    path.write_bytes(verifier.release_gate.canonical_policy_bytes(policy))


def _modify_license_byte(root: Path, relative: str) -> None:
    path = root / relative
    data = bytearray(path.read_bytes())
    _assert(bool(data), f"license fixture is empty: {relative}")
    data[0] ^= 1
    path.write_bytes(data)


def _convert_license_to_crlf(root: Path, relative: str) -> None:
    path = root / relative
    data = path.read_bytes()
    _assert(b"\n" in data and b"\r" not in data, f"license fixture is not LF-only: {relative}")
    path.write_bytes(data.replace(b"\n", b"\r\n"))


def _prefix_license_bom(root: Path, relative: str) -> None:
    path = root / relative
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())


def _rewrite_notice(root: Path, transform: Callable[[str], str]) -> None:
    path = root / "THIRD_PARTY_NOTICES.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(transform(text), encoding="utf-8", newline="\n")


def _remove_component_notice(root: Path) -> None:
    _rewrite_notice(root, lambda text: text.replace(verifier.NOTICE_HEADINGS["deno"], "### Unidentified runtime", 1))


def _remove_license_path_notice(root: Path) -> None:
    token = f"`{verifier.APACHE_LICENSE_PATH}`"
    _rewrite_notice(root, lambda text: text.replace(token, "`legal/licenses/unrecorded.txt`"))


def _remove_gpl_warning(root: Path) -> None:
    def transform(text: str) -> str:
        start = text.index("## GPL source-availability warning")
        end = text.index("## Trademarks and affiliation", start)
        return text[:start] + text[end:]

    _rewrite_notice(root, transform)


def _append_notice(root: Path, text: str) -> None:
    path = root / "THIRD_PARTY_NOTICES.md"
    current = path.read_text(encoding="utf-8")
    path.write_text(current.rstrip("\n") + "\n\n" + text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")


def _add_attribution_claim(root: Path) -> None:
    _append_notice(root, "Attribution alone " + "satisfies GPL obligations.")


def _add_mit_project_claim(root: Path) -> None:
    _append_notice(root, "This is an MIT " + "licensed project.")


def _add_open_project_claim(root: Path) -> None:
    _append_notice(root, "The project is " + "open source.")


def _add_compliance_claim(root: Path) -> None:
    _append_notice(root, "The project is " + "fully" + " compliant.")


def _add_release_ready_claim(root: Path) -> None:
    _append_notice(root, "The release is " + "release ready.")


def _add_source_complete_claim(root: Path) -> None:
    _append_notice(root, "The source availability is " + "complete.")


def _add_publishing_enabled_claim(root: Path) -> None:
    _append_notice(root, "Publishing " + "enabled.")


def _add_complete_corresponding_source_claim(root: Path) -> None:
    _append_notice(root, "Complete " + "Corresponding Source.")


def _add_exhaustive_inventory_claim(root: Path) -> None:
    _append_notice(root, "This is an " + "exhaustive binary inventory.")


def _add_root_license(root: Path) -> None:
    (root / "LICENSE").write_text("Project permission grant.\n", encoding="utf-8", newline="\n")


def _add_local_path(root: Path) -> None:
    local_path = "C:" + "\\Users\\developer\\legal-source.txt"
    _append_notice(root, f"Local source: {local_path}")


def _add_secret_token(root: Path) -> None:
    token = "ghp_" + "A" * 32
    _append_notice(root, f"Credential: {token}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
