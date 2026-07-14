from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

import verify_legal_notices as verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
Mutation = Callable[[Path], None]


def main() -> int:
    _verify_positive_repository()
    _run_inventory_mutations()
    _run_license_mutations()
    _run_notice_mutations()
    _run_claim_and_hygiene_mutations()
    print("legal notices smoke tests passed")
    return 0


def _verify_positive_repository() -> None:
    inventory = verifier.verify_repository(REPO_ROOT)
    components = inventory["components"]
    _assert(len(components) == 7, "known direct component count changed")
    _assert(
        [component["id"] for component in components] == sorted(verifier.EXPECTED_COMPONENTS),
        "component ordering changed",
    )
    raw_inventory = (REPO_ROOT / "legal/components.json").read_bytes()
    _assert(raw_inventory == verifier.canonical_inventory_bytes(inventory), "inventory is not deterministic")

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


def _expect_failure(label: str, mutation: Mutation, expected_message: str | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="legal-notices-smoke-") as temp:
        root = Path(temp) / "repo"
        _copy_fixture(root)
        mutation(root)
        try:
            verifier.verify_repository(root)
        except (verifier.LegalVerificationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            if expected_message is not None:
                _assert(expected_message.casefold() in str(exc).casefold(), f"{label}: unexpected failure: {exc}")
        else:
            raise AssertionError(f"mutation was not rejected: {label}")


def _copy_fixture(root: Path) -> None:
    root.mkdir(parents=True)
    for relative in ("README.md", "THIRD_PARTY_NOTICES.md", "VERSION"):
        shutil.copy2(REPO_ROOT / relative, root / relative)
    shutil.copytree(REPO_ROOT / "legal", root / "legal")

    (root / ".github").mkdir()
    shutil.copy2(REPO_ROOT / ".github/build-dependencies.json", root / ".github/build-dependencies.json")
    shutil.copytree(REPO_ROOT / ".github/workflows", root / ".github/workflows")

    (root / "scripts").mkdir()
    for name in ("build_release_v1_3_0.ps1", "build_release_v1_3_1.ps1"):
        shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)


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
