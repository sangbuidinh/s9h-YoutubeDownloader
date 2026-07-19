from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import inventory_built_executable as built_inventory
import verify_release_legal_gate as release_gate
import verify_release_legal_payload as release_payload
import verify_source_correspondence as source_correspondence


REPO_ROOT = Path(__file__).resolve().parents[1]

TOP_LEVEL_KEYS = (
    "schema_version",
    "inventory_scope",
    "project_license_status",
    "legal_compliance_certified",
    "release_integration_status",
    "components",
)
COMPONENT_KEYS = (
    "id",
    "name",
    "version",
    "role",
    "distribution_paths",
    "upstream_repository",
    "upstream_ref",
    "upstream_license_path",
    "upstream_license_blob_sha1",
    "license_label",
    "local_license_path",
    "notice_status",
    "source_distribution_status",
    "notes",
)

EXPECTED_COMPONENTS: dict[str, dict[str, Any]] = {
    "aria2": {
        "name": "aria2",
        "version": "1.37.0",
        "role": "external executable in portable ZIP",
        "distribution_paths": ["data/bin/aria2c.exe"],
        "upstream_repository": "aria2/aria2",
        "upstream_ref": "release-1.37.0",
        "upstream_license_path": "COPYING",
        "upstream_license_blob_sha1": "d159169d1050894d3ea3b98e1c965c4058208fe1",
        "license_label": "GNU General Public License, Version 2, as present in the upstream COPYING file",
        "local_license_path": "legal/licenses/aria2-1.37.0-GPLv2.txt",
        "notice_status": "verified-license-text",
        "source_distribution_status": "not-certified",
    },
    "deno": {
        "name": "Deno",
        "version": "2.7.14",
        "role": "external executable in portable ZIP",
        "distribution_paths": ["data/bin/deno.exe"],
        "upstream_repository": "denoland/deno",
        "upstream_ref": "v2.7.14",
        "upstream_license_path": "LICENSE.md",
        "upstream_license_blob_sha1": "b641c75c0b04ccfddcb1145d2554ab49b6ca9411",
        "license_label": "MIT",
        "local_license_path": "legal/licenses/Deno-2.7.14-MIT.txt",
        "notice_status": "verified-license-text",
        "source_distribution_status": "not-assessed",
    },
    "ffmpeg": {
        "name": "FFmpeg and ffprobe",
        "version": "8.1.2",
        "role": "external executables from the Gyan essentials static Windows build in portable ZIP",
        "distribution_paths": ["data/bin/ffmpeg.exe", "data/bin/ffprobe.exe"],
        "upstream_repository": "FFmpeg/FFmpeg",
        "upstream_ref": "n8.1.2",
        "upstream_license_path": "COPYING.GPLv3",
        "upstream_license_blob_sha1": "94a9ed024d3859793618152ea559a168bbcbb5e2",
        "license_label": "GPLv3 static build, as identified by the Gyan build distributor",
        "local_license_path": "legal/licenses/FFmpeg-8.1.2-GPLv3.txt",
        "notice_status": "verified-license-text",
        "source_distribution_status": "not-certified",
    },
    "pyinstaller": {
        "name": "PyInstaller",
        "version": "6.21.0",
        "role": "build tool with bootloader and related files embedded in the standalone executable",
        "distribution_paths": ["Youtube.Downloaderbs.exe"],
        "upstream_repository": "pyinstaller/pyinstaller",
        "upstream_ref": "v6.21.0",
        "upstream_license_path": "COPYING.txt",
        "upstream_license_blob_sha1": "51bf4bd138e68f0ccafce4fbf7723842dceaa1d1",
        "license_label": "GPL-2.0-or-later with the PyInstaller bootloader exception; runtime hooks described as Apache-2.0",
        "local_license_path": "legal/licenses/PyInstaller-6.21.0-COPYING.txt",
        "notice_status": "verified-license-text",
        "source_distribution_status": "requires-built-artifact-inventory",
    },
    "python": {
        "name": "Python",
        "version": "3.11.9",
        "role": "runtime embedded in the PyInstaller-built standalone executable",
        "distribution_paths": ["Youtube.Downloaderbs.exe"],
        "upstream_repository": "python/cpython",
        "upstream_ref": "v3.11.9",
        "upstream_license_path": "LICENSE",
        "upstream_license_blob_sha1": "f26bcf4d2de6eb136e31006ca3ab447d5e488adf",
        "license_label": "Python Software Foundation License Version 2 and incorporated notices contained in the complete upstream LICENSE file",
        "local_license_path": "legal/licenses/Python-3.11.9-LICENSE.txt",
        "notice_status": "verified-license-text",
        "source_distribution_status": "requires-built-artifact-inventory",
    },
    "tcl-tk": {
        "name": "Tcl/Tk",
        "version": "not-verified",
        "role": "conservative notice for Tcl/Tk runtime material that may be embedded through Tkinter",
        "distribution_paths": ["Youtube.Downloaderbs.exe"],
        "upstream_repository": "tcltk/tcl",
        "upstream_ref": "core-8-6-13",
        "upstream_license_path": "license.terms",
        "upstream_license_blob_sha1": "d8049cd9e7ca055f7e584a76f88861a294b30c9c",
        "license_label": "Tcl upstream license terms preserved as a conservative notice",
        "local_license_path": "legal/licenses/Tcl-Tk-license.terms",
        "notice_status": "conservative-notice",
        "source_distribution_status": "requires-built-artifact-inventory",
    },
    "yt-dlp": {
        "name": "yt-dlp",
        "version": "2026.03.17",
        "role": "external executable in portable ZIP",
        "distribution_paths": ["data/bin/yt-dlp.exe"],
        "upstream_repository": "yt-dlp/yt-dlp",
        "upstream_ref": "2026.03.17",
        "upstream_license_path": "LICENSE",
        "upstream_license_blob_sha1": "68a49daad8ff7e35068f2b7a97d643aab440eaec",
        "license_label": "The Unlicense / public-domain dedication",
        "local_license_path": "legal/licenses/yt-dlp-2026.03.17-UNLICENSE.txt",
        "notice_status": "verified-license-text",
        "source_distribution_status": "not-assessed",
    },
}

APACHE_LICENSE_PATH = "legal/licenses/Apache-2.0.txt"
APACHE_SOURCE = "https://www.apache.org/licenses/LICENSE-2.0.txt"
APACHE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
ALL_LICENSE_PATHS = tuple(
    sorted(
        [APACHE_LICENSE_PATH]
        + [component["local_license_path"] for component in EXPECTED_COMPONENTS.values()],
        key=str.casefold,
    )
)

GITATTRIBUTES_PATH = ".gitattributes"
GITATTRIBUTES_LINES = (
    "/.gitattributes text eol=lf",
    "/README.md text eol=lf",
    "/docs/source-kit-feasibility.md text eol=lf",
    "/docs/sbom-generator-feasibility.md text eol=lf",
    "/legal/*.json text eol=lf",
    "/legal/README.md text eol=lf",
    "/legal/built-artifact-inventory.json text eol=lf",
    "/legal/components.json text eol=lf",
    "/legal/release-assets-v2.json text eol=lf",
    "/legal/release-policy.json text eol=lf",
    "/legal/source-correspondence.json text eol=lf",
    "/legal/source-kit-requirements.json text eol=lf",
    "/legal/licenses/** -text",
)
GITATTRIBUTES_BYTES = ("\n".join(GITATTRIBUTES_LINES) + "\n").encode("utf-8")
CANONICAL_DOCUMENT_PATHS = (
    "README.md",
    "docs/source-kit-feasibility.md",
    "docs/sbom-generator-feasibility.md",
    "legal/README.md",
)
EXPECTED_TOP_LEVEL_LEGAL_JSON_COUNT = 17

NOTICE_HEADINGS = {
    "aria2": "### aria2 1.37.0",
    "deno": "### Deno 2.7.14",
    "ffmpeg": "### FFmpeg and ffprobe 8.1.2",
    "pyinstaller": "### PyInstaller 6.21.0",
    "python": "### Python 3.11.9",
    "tcl-tk": "### Tcl/Tk conservative notice",
    "yt-dlp": "### yt-dlp 2026.03.17",
}
ROOT_PROJECT_LICENSE_FILES = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "COPYING",
    "COPYING.txt",
    "NOTICE",
    "NOTICE.txt",
)
MUTABLE_REFS = {"main", "master", "latest", "head"}
FORBIDDEN_CLAIMS = tuple(
    " ".join(parts)
    for parts in (
        ("fully", "compliant"),
        ("full", "compliance"),
        ("gpl", "compliant"),
        ("license", "compliant"),
        ("certified", "compliant"),
        ("all", "third-party", "dependencies"),
        ("complete", "dependency", "inventory"),
        ("exhaustive", "binary", "inventory"),
        ("release", "ready"),
        ("ready", "for", "release"),
        ("approved", "to", "publish"),
        ("publishing", "enabled"),
        ("source", "kit", "complete"),
        ("source", "availability", "complete"),
        ("source", "availability", "is", "complete"),
        ("corresponding", "source", "is", "complete"),
        ("project", "is", "open", "source"),
        ("mit", "licensed", "project"),
    )
)
DOCUMENTATION_ONLY_FORBIDDEN_CLAIMS = (
    "complete corresponding source",
    "legally compliant",
    "release approved",
    "safe to distribute",
    "all source available",
)
UNSUPPORTED_ATTRIBUTION_CLAIMS = tuple(
    " ".join(parts)
    for parts in (
        ("attribution", "alone", "satisfies", "gpl"),
        ("attribution", "alone", "is", "sufficient", "for", "gpl"),
    )
)
LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)


class LegalVerificationError(AssertionError):
    pass


def main() -> int:
    try:
        verify_repository(REPO_ROOT)
    except (
        LegalVerificationError,
        built_inventory.InventoryError,
        release_gate.ReleaseLegalGateError,
        release_payload.LegalPayloadError,
        source_correspondence.SourceCorrespondenceError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"legal notices verification failed: {exc}", file=sys.stderr)
        return 1
    print("legal notices verified")
    return 0


def verify_repository(root: Path) -> dict[str, Any]:
    root = Path(root)
    verify_checkout_policy(root)
    inventory, inventory_text = _verify_inventory(root)
    artifact_path = root / "legal/built-artifact-inventory.json"
    policy_path = root / "legal/release-policy.json"
    _require(artifact_path.is_file(), "required file is missing: legal/built-artifact-inventory.json")
    _require(policy_path.is_file(), "required file is missing: legal/release-policy.json")
    artifact_inventory = built_inventory.load_inventory(artifact_path)
    release_policy = release_gate.load_policy(policy_path)
    _verify_license_files(root, inventory)
    notices = _read_authored_text(root, "THIRD_PARTY_NOTICES.md")
    legal_readme = _read_authored_text(root, "legal/README.md")
    product_readme = _read_authored_text(root, "README.md")
    _verify_notices(notices, inventory)
    _verify_readmes(product_readme, legal_readme)
    _verify_phase6b1_artifacts(artifact_inventory, release_policy)
    _verify_sources_of_truth(root, inventory)
    correspondence, source_kits = source_correspondence.verify_repository(root)
    _verify_phase6b2a_artifacts(correspondence, source_kits)
    release_assets = release_payload.load_asset_contract(root / release_payload.CONTRACT_PATH)
    _verify_phase6b2b1_artifacts(release_assets, release_policy, source_kits)
    _verify_project_claims_and_hygiene(
        {
            "README.md": product_readme,
            "THIRD_PARTY_NOTICES.md": notices,
            "legal/README.md": legal_readme,
            "legal/components.json": inventory_text,
            "legal/built-artifact-inventory.json": (
                root / "legal/built-artifact-inventory.json"
            ).read_text(encoding="utf-8"),
            "legal/release-policy.json": (root / "legal/release-policy.json").read_text(encoding="utf-8"),
            "legal/release-assets-v2.json": (
                root / "legal/release-assets-v2.json"
            ).read_text(encoding="utf-8"),
            "legal/source-correspondence.json": (
                root / "legal/source-correspondence.json"
            ).read_text(encoding="utf-8"),
            "legal/source-kit-requirements.json": (
                root / "legal/source-kit-requirements.json"
            ).read_text(encoding="utf-8"),
        }
    )
    _verify_no_project_license(root)
    return inventory


def canonical_inventory_bytes(inventory: dict[str, Any]) -> bytes:
    return (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def git_blob_sha1(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def verify_checkout_policy(root: Path) -> None:
    root = Path(root)
    _verify_checkout_policy(root)


def _verify_checkout_policy(root: Path) -> None:
    legal_json_paths = _discover_top_level_legal_json(root)
    _require(
        len(legal_json_paths) == EXPECTED_TOP_LEVEL_LEGAL_JSON_COUNT,
        f"top-level legal JSON count must be {EXPECTED_TOP_LEVEL_LEGAL_JSON_COUNT}",
    )
    path = root / GITATTRIBUTES_PATH
    _require(
        path.is_file() and not path.is_symlink(),
        f"required regular file is missing: {GITATTRIBUTES_PATH}",
    )
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{GITATTRIBUTES_PATH} contains a UTF-8 BOM")
    _require(b"\0" not in raw, f"{GITATTRIBUTES_PATH} contains NUL")
    _require(b"\r" not in raw, f"{GITATTRIBUTES_PATH} must use LF line endings")
    text = raw.decode("utf-8")
    _require(text.endswith("\n") and not text.endswith("\n\n"), f"{GITATTRIBUTES_PATH} must have one final newline")
    _verify_attribute_text_hygiene(text)

    lines = tuple(text.removesuffix("\n").split("\n"))
    _require(lines == GITATTRIBUTES_LINES, f"{GITATTRIBUTES_PATH} checkout rules are invalid")
    _require(raw == GITATTRIBUTES_BYTES, f"{GITATTRIBUTES_PATH} bytes are not canonical")

    attributes = _expected_checkout_attributes(legal_json_paths)
    git_attributes = _read_git_checkout_attributes(root, legal_json_paths)
    if git_attributes is not None:
        attributes = git_attributes
    _require(attributes[GITATTRIBUTES_PATH]["text"] == "set", ".gitattributes text attribute must be set")
    _require(attributes[GITATTRIBUTES_PATH]["eol"] == "lf", ".gitattributes eol attribute must be lf")
    for relative in legal_json_paths:
        _require(attributes[relative]["text"] == "set", f"{relative} text attribute must be set")
        _require(attributes[relative]["eol"] == "lf", f"{relative} eol attribute must be lf")
    for relative in CANONICAL_DOCUMENT_PATHS:
        _require(attributes[relative]["text"] == "set", f"{relative} text attribute must be set")
        _require(attributes[relative]["eol"] == "lf", f"{relative} eol attribute must be lf")
        _verify_canonical_document_bytes(
            root,
            relative,
            compare_git_blob=git_attributes is not None,
        )
    for relative in ALL_LICENSE_PATHS:
        _require(attributes[relative]["text"] == "unset", f"{relative} text attribute must be unset")


def _verify_attribute_text_hygiene(text: str) -> None:
    _require(LOCAL_PATH_PATTERN.search(text) is None, f"local absolute path in {GITATTRIBUTES_PATH}")
    for pattern in SECRET_PATTERNS:
        _require(pattern.search(text) is None, f"secret-like value in {GITATTRIBUTES_PATH}")
    for line in text.splitlines():
        _require(line and not line.startswith("#"), f"{GITATTRIBUTES_PATH} contains an unsupported line")
        _require(line == line.strip() and "  " not in line, f"{GITATTRIBUTES_PATH} contains invalid whitespace")
        fields = line.split(" ")
        _require(len(fields) >= 2, f"{GITATTRIBUTES_PATH} contains malformed attribute syntax")
        _require("\\" not in fields[0], f"{GITATTRIBUTES_PATH} contains a local or invalid path")
        _require(
            all(field in {"text", "-text", "eol=lf", "eol=crlf"} for field in fields[1:]),
            f"{GITATTRIBUTES_PATH} contains malformed or unsupported attributes",
        )


def _discover_top_level_legal_json(root: Path) -> tuple[str, ...]:
    legal_root = root / "legal"
    _require(
        legal_root.is_dir() and not legal_root.is_symlink(),
        "required directory is missing: legal",
    )
    paths = tuple(
        sorted(
            (
                path.relative_to(root).as_posix()
                for path in legal_root.iterdir()
                if path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".json"
            ),
            key=lambda value: (value.casefold(), value),
        )
    )
    _require(paths, "no top-level legal JSON files found")
    return paths


def _verify_canonical_document_bytes(
    root: Path,
    relative: str,
    *,
    compare_git_blob: bool,
) -> None:
    path = root / relative
    _require(
        path.is_file() and not path.is_symlink(),
        f"required regular file is missing: {relative}",
    )
    raw = path.read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{relative} contains a UTF-8 BOM")
    _require(b"\r" not in raw, f"noncanonical documentation: {relative}")
    _require(b"\0" not in raw, f"{relative} contains NUL")
    raw.decode("utf-8")
    if compare_git_blob:
        blob = _read_git_blob(root, relative)
        if blob is not None:
            _require(raw == blob, f"canonical documentation differs from Git blob: {relative}")


def _expected_checkout_attributes(
    legal_json_paths: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    attributes = {
        GITATTRIBUTES_PATH: {"text": "set", "eol": "lf"},
    }
    attributes.update({relative: {"text": "set", "eol": "lf"} for relative in legal_json_paths})
    attributes.update({relative: {"text": "set", "eol": "lf"} for relative in CANONICAL_DOCUMENT_PATHS})
    attributes.update({relative: {"text": "unset", "eol": "unspecified"} for relative in ALL_LICENSE_PATHS})
    return attributes


def _read_git_checkout_attributes(
    root: Path,
    legal_json_paths: tuple[str, ...],
) -> dict[str, dict[str, str]] | None:
    git = shutil.which("git")
    if git is None:
        return None
    probe = subprocess.run(
        [git, "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if probe.returncode != 0:
        return None
    try:
        top_level = Path(probe.stdout.strip()).resolve()
    except OSError:
        return None
    if top_level != root.resolve():
        return None

    paths = (
        GITATTRIBUTES_PATH,
        *legal_json_paths,
        *CANONICAL_DOCUMENT_PATHS,
        *ALL_LICENSE_PATHS,
    )
    result = subprocess.run(
        [git, "-C", str(root), "check-attr", "-z", "text", "eol", "--", *paths],
        capture_output=True,
        check=False,
    )
    _require(result.returncode == 0, "git check-attr failed for legal checkout policy")
    fields = result.stdout.split(b"\0")
    _require(fields[-1] == b"" and (len(fields) - 1) % 3 == 0, "git check-attr returned malformed output")
    attributes: dict[str, dict[str, str]] = {relative: {} for relative in paths}
    for index in range(0, len(fields) - 1, 3):
        try:
            relative = fields[index].decode("utf-8")
            attribute = fields[index + 1].decode("ascii")
            value = fields[index + 2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise LegalVerificationError("git check-attr returned unsupported encoding") from exc
        _require(relative in attributes, f"git check-attr returned an unexpected path: {relative}")
        _require(attribute in {"text", "eol"}, f"git check-attr returned an unexpected attribute: {attribute}")
        attributes[relative][attribute] = value
    _require(all(set(values) == {"text", "eol"} for values in attributes.values()), "git check-attr output is incomplete")
    return attributes


def _read_git_blob(root: Path, relative: str) -> bytes | None:
    git = shutil.which("git")
    if git is None:
        return None
    result = subprocess.run(
        [git, "-C", str(root), "show", f"HEAD:{relative}"],
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _verify_inventory(root: Path) -> tuple[dict[str, Any], str]:
    relative = "legal/components.json"
    raw = _read_required_bytes(root, relative)
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{relative} contains a UTF-8 BOM")
    _require(b"\r" not in raw, f"{relative} must use LF line endings")
    _require(b"\0" not in raw, f"{relative} contains NUL")
    text = raw.decode("utf-8")
    inventory = json.loads(text)
    _require(isinstance(inventory, dict), "inventory root must be an object")
    _require(tuple(inventory) == TOP_LEVEL_KEYS, "inventory top-level schema or field order is invalid")
    _require(inventory["schema_version"] == 1, "inventory schema_version must be 1")
    _require(inventory["inventory_scope"] == "known-direct-components", "inventory scope is invalid")
    _require(inventory["project_license_status"] == "not-selected", "project license status is invalid")
    _require(inventory["legal_compliance_certified"] is False, "legal compliance must not be certified")
    _require(
        inventory["release_integration_status"] == "blocked-pending-phase-6b2",
        "release integration status is invalid",
    )
    _require(raw == canonical_inventory_bytes(inventory), "inventory JSON is not deterministic")

    components = inventory["components"]
    _require(isinstance(components, list), "components must be an array")
    ids = [component.get("id") if isinstance(component, dict) else None for component in components]
    _require(len(ids) == len(set(ids)), "component IDs must be unique")
    _require(ids == sorted(ids), "components must be sorted by id")
    _require(set(ids) == set(EXPECTED_COMPONENTS), "component set is invalid")

    local_paths: list[str] = []
    for component in components:
        component_id = component["id"]
        _require(tuple(component) == COMPONENT_KEYS, f"{component_id} schema or field order is invalid")
        _require(
            component["upstream_ref"].casefold() not in MUTABLE_REFS,
            f"{component_id} uses a mutable upstream ref",
        )
        expected = EXPECTED_COMPONENTS[component_id]
        for field, expected_value in expected.items():
            _require(component[field] == expected_value, f"{component_id} {field} is invalid")
        _require(
            re.fullmatch(r"[0-9a-f]{40}", component["upstream_license_blob_sha1"]) is not None,
            f"{component_id} upstream blob is invalid",
        )
        _require(
            isinstance(component["distribution_paths"], list)
            and component["distribution_paths"] == sorted(component["distribution_paths"]),
            f"{component_id} distribution paths are invalid",
        )
        _require(
            isinstance(component["notes"], list)
            and component["notes"]
            and all(isinstance(note, str) and note for note in component["notes"]),
            f"{component_id} notes are invalid",
        )
        local_paths.append(component["local_license_path"])

    _require(len(local_paths) == len(set(local_paths)), "local license paths must be unique")
    apache_record = "\n".join(
        next(component for component in components if component["id"] == "pyinstaller")["notes"]
    )
    _require(APACHE_SOURCE in apache_record, "Apache canonical source is not recorded")
    _require(APACHE_LICENSE_PATH in apache_record, "Apache local license path is not recorded")
    _require(APACHE_SHA256 in apache_record, "Apache SHA-256 is not recorded")

    ffmpeg_notes = "\n".join(next(c for c in components if c["id"] == "ffmpeg")["notes"])
    _require("complete Corresponding Source" in ffmpeg_notes, "FFmpeg source warning is missing")
    tcl_notes = "\n".join(next(c for c in components if c["id"] == "tcl-tk")["notes"])
    _require("not verified in Phase 6A" in tcl_notes, "Tcl/Tk embedded version status is missing")
    return inventory, text


def _verify_license_files(root: Path, inventory: dict[str, Any]) -> None:
    component_paths = {component["local_license_path"] for component in inventory["components"]}
    _require(
        component_paths | {APACHE_LICENSE_PATH} == set(ALL_LICENSE_PATHS),
        "license path set is invalid",
    )
    for component in inventory["components"]:
        relative = component["local_license_path"]
        data = _read_license_bytes(root, relative)
        actual = git_blob_sha1(data)
        _require(actual == component["upstream_license_blob_sha1"], f"{relative} Git blob SHA-1 mismatch")

    apache = _read_license_bytes(root, APACHE_LICENSE_PATH)
    _require(hashlib.sha256(apache).hexdigest() == APACHE_SHA256, "Apache license SHA-256 mismatch")


def _verify_notices(notices: str, inventory: dict[str, Any]) -> None:
    for required in (
        "# Third-Party Notices",
        "## Scope and limitations",
        "not a project license",
        "not legal advice",
        "pending Phase 6B2",
        "## Distributed runtime tools",
        "## Packaged application runtime",
        "## GPL source-availability warning",
        "## Phase 6B2B1 release legal payload",
        "portable packages will include verified legal materials",
        "standalone EXE releases require a companion legal ZIP",
        "legal materials do not replace source-distribution obligations",
        "release bundle v2 requires both source assets",
        "publishing remains disabled",
        "Phase 6B2B2 remains required",
        "## Trademarks and affiliation",
        "## License files",
    ):
        _require(required in notices, f"third-party notices missing: {required}")
    for component in inventory["components"]:
        _require(NOTICE_HEADINGS[component["id"]] in notices, f"component is undocumented: {component['id']}")
    for relative in ALL_LICENSE_PATHS:
        _require(f"`{relative}`" in notices, f"license path is undocumented: {relative}")
    for required in (
        "Separation does not eliminate",
        "Attribution or a copied license text alone does not resolve",
        "Corresponding Source",
        "source-offer handling",
        "does not imply endorsement",
    ):
        _require(required in notices, f"third-party notices missing source or endorsement warning: {required}")


def _verify_readmes(product_readme: str, legal_readme: str) -> None:
    for required in (
        "## License and third-party software",
        "[Third-Party Notices](THIRD_PARTY_NOTICES.md)",
        "[Legal Materials](legal/README.md)",
        "No project license has been selected",
        "not a grant of permission",
        "blocked pending Phase 6B2",
        "## Phase 6B1 controlled build inventory",
        "legal/built-artifact-inventory.json",
        "## Release gate",
        "legal/release-policy.json",
        "before dependency installation, runtime acquisition, and application build",
        "Existing releases are not retroactively certified",
        "## Source availability status",
        "verified source kits and equivalent release access",
        "## Phase 6B2A source correspondence audit",
        "legal/source-correspondence.json",
        "legal/source-kit-requirements.json",
        "source correspondence partially identified",
        "source kit not ready",
        "no compliance certification",
        "## Phase 6B2B1 release legal payload",
        "legal/release-assets-v2.json",
        "s9h-release-bundle-v2",
        "s9h-release-legal-payload-v1",
        "standalone EXE releases require a companion legal ZIP",
        "source asset names are defined but assets are not ready",
        "publishing remains disabled",
        "Phase 6B2B2 remains required",
    ):
        _require(required in product_readme, f"product README missing legal statement: {required}")
    _require(
        "A project license and bundled third-party notices are being prepared" not in product_readme,
        "product README still contains the old license placeholder",
    )

    for required in (
        "# Legal Materials",
        "## Project licensing status",
        "No project license is selected in Phase 6A",
        "requires-built-artifact-inventory",
        "## Phase 6B1 controlled build inventory",
        "legal/built-artifact-inventory.json",
        "OpenSSL",
        "SQLite",
        "zlib",
        "libffi",
        "Tcl/Tk subcomponents",
        "Microsoft runtime components",
        "## Source and binary correspondence",
        "v1.2.7",
        "## FFmpeg-specific warning",
        "## Release gate",
        "legal/release-policy.json",
        "before dependency installation, release runtime acquisition, and application build",
        "## Source availability status",
        "## Phase 6B2 requirements",
        "verified source kits",
        "portable ZIP",
        "standalone EXE",
        "source availability or source-offer",
        "release bundle schema",
        "release gate",
        "## Phase 6B2A source correspondence audit",
        "legal/source-correspondence.json",
        "legal/source-kit-requirements.json",
        "source correspondence partially identified",
        "source kit not ready",
        "no compliance certification",
        "## Phase 6B2B1 release legal payload",
        "legal/release-assets-v2.json",
        "s9h-release-bundle-v2",
        "s9h-release-legal-payload-v1",
        "standalone EXE releases require a companion legal ZIP",
        "source asset names are defined but assets are not ready",
        "publishing remains disabled",
        "Phase 6B2B2 remains required",
    ):
        _require(required in legal_readme, f"legal README missing status or requirement: {required}")


def _verify_phase6b1_artifacts(
    artifact_inventory: dict[str, Any], release_policy: dict[str, Any]
) -> None:
    _require(
        artifact_inventory["source_commit"] == built_inventory.BASELINE_COMMIT,
        "built-artifact inventory source commit changed",
    )
    _require(artifact_inventory["python_version"] == "3.11.9", "built-artifact Python changed")
    _require(artifact_inventory["pyinstaller_version"] == "6.21.0", "built-artifact PyInstaller changed")
    _require(artifact_inventory["target_platform"] == "windows-x86_64", "built-artifact platform changed")
    native = artifact_inventory["native_members"]
    _require(bool(native), "built-artifact native member inventory is empty")
    _require(
        artifact_inventory["unresolved_native_members"]
        == [record["name"] for record in native if record["status"] == "unresolved"],
        "unresolved native members are not retained",
    )
    _require(
        tuple(release["tag"] for release in release_policy["releases"]) == release_gate.EXPECTED_TAGS,
        "release policy tags changed",
    )
    _require(all(release["status"] == "blocked" for release in release_policy["releases"]), "release policy is not blocked")
    _require(release_policy["legal_compliance_certified"] is False, "legal compliance was certified")
    _require(release_policy["source_availability_certified"] is False, "source availability was certified")
    _require(release_policy["release_payload_integrated"] is False, "release payload was marked integrated")


def _verify_phase6b2a_artifacts(
    correspondence: dict[str, Any], source_kits: dict[str, Any]
) -> None:
    _require(
        correspondence["baseline_commit"] == source_correspondence.BASELINE_COMMIT,
        "source correspondence baseline changed",
    )
    _require(
        correspondence["corresponding_source_complete"] is False,
        "Corresponding Source was marked complete",
    )
    _require(
        correspondence["legal_compliance_certified"] is False,
        "source correspondence certified legal compliance",
    )
    _require(
        correspondence["release_gate_status"] == "fail-closed",
        "source correspondence release gate changed",
    )
    _require(
        [package["id"] for package in correspondence["packages"]] == ["aria2", "ffmpeg"],
        "source correspondence package set changed",
    )
    _require(
        source_kits["release_gate_reconsideration_allowed"] is False,
        "source kit allowed release-gate reconsideration",
    )
    _require(
        source_kits["legal_compliance_certified"] is False,
        "source kit certified legal compliance",
    )
    _require(
        all(kit["status"] == "blocked" for kit in source_kits["kits"]),
        "source kit is not blocked",
    )


def _verify_phase6b2b1_artifacts(
    release_assets: dict[str, Any],
    release_policy: dict[str, Any],
    source_kits: dict[str, Any],
) -> None:
    _require(
        release_assets["bundle_format"] == "s9h-release-bundle-v2",
        "release bundle format changed",
    )
    _require(
        release_assets["legal_payload_format"] == "s9h-release-legal-payload-v1",
        "release legal payload format changed",
    )
    _require(release_assets["release_readiness"] == "blocked", "release readiness changed")
    _require(release_assets["legal_compliance_certified"] is False, "legal compliance was certified")
    _require(
        release_assets["source_availability_certified"] is False,
        "source availability was certified",
    )
    _require(release_assets["source_kits_ready"] is False, "source kits were marked ready")
    _require(
        [item["id"] for item in release_assets["required_source_asset_templates"]]
        == ["aria2", "ffmpeg"],
        "required source asset templates changed",
    )
    _require(
        all(
            item["status"] == "not-ready"
            for item in release_assets["required_source_asset_templates"]
        ),
        "required source asset status changed",
    )
    _require(release_policy["policy_mode"] == "fail-closed", "release policy mode changed")
    _require(release_policy["release_payload_integrated"] is False, "release payload was marked integrated")
    _require(
        source_kits["release_gate_reconsideration_allowed"] is False,
        "source gate was reconsidered",
    )
    _require(
        all(kit["status"] == "blocked" for kit in source_kits["kits"]),
        "source kit must remain blocked",
    )


def _verify_sources_of_truth(root: Path, inventory: dict[str, Any]) -> None:
    build_dependencies = json.loads(_read_required_bytes(root, ".github/build-dependencies.json").decode("utf-8"))
    _require(build_dependencies["target"]["python"] == "3.11.9", "build lock Python version changed")
    _require(build_dependencies["build_root"] == {"name": "PyInstaller", "version": "6.21.0"}, "build root changed")
    direct_pyinstaller = [
        package
        for package in build_dependencies["packages"]
        if package.get("name", "").casefold() == "pyinstaller" and package.get("direct") is True
    ]
    _require(
        len(direct_pyinstaller) == 1 and direct_pyinstaller[0].get("version") == "6.21.0",
        "locked direct PyInstaller version changed",
    )

    versions = {component["id"]: component["version"] for component in inventory["components"]}
    _require(versions["python"] == build_dependencies["target"]["python"], "inventory Python version mismatch")
    _require(versions["pyinstaller"] == build_dependencies["build_root"]["version"], "inventory PyInstaller mismatch")

    required_release_snippets = (
        "releases/download/2026.03.17/yt-dlp.exe",
        "ffmpeg-8.1.2-essentials_build.zip",
        '8.1.2-essentials_build-www.gyan.dev',
        "release-1.37.0/aria2-1.37.0-win-64bit-build1.zip",
        "v2.7.14/deno-x86_64-pc-windows-msvc.zip",
    )
    for relative in ("scripts/build_release_v1_3_0.ps1", "scripts/build_release_v1_3_1.ps1"):
        script = _read_required_bytes(root, relative).decode("utf-8-sig")
        for snippet in required_release_snippets:
            _require(snippet in script, f"{relative} runtime identity changed: {snippet}")

    workflow_dir = root / ".github" / "workflows"
    workflows = sorted(workflow_dir.glob("*.yml"))
    pinned_workflows = 0
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8-sig")
        if "actions/setup-python@" not in text:
            continue
        pinned_workflows += 1
        _require('python-version: "3.11.9"' in text, f"{workflow.name} Python pin changed")
        _require("Python 3.11.9" in text, f"{workflow.name} Python runtime check changed")
    _require(pinned_workflows > 0, "no pinned Python workflow was found")
    _require(_read_required_bytes(root, "VERSION").decode("utf-8-sig").strip() == "1.3.1", "VERSION changed")


def _verify_project_claims_and_hygiene(documents: dict[str, str]) -> None:
    for relative, text in documents.items():
        folded = text.casefold()
        for claim in FORBIDDEN_CLAIMS:
            _require(claim not in folded, f"unsupported project claim in {relative}: {claim}")
        if relative in {"README.md", "THIRD_PARTY_NOTICES.md", "legal/README.md"}:
            for claim in DOCUMENTATION_ONLY_FORBIDDEN_CLAIMS:
                _require(
                    claim not in folded,
                    f"unsupported project claim in {relative}: {claim}",
                )
        for claim in UNSUPPORTED_ATTRIBUTION_CLAIMS:
            _require(claim not in folded, f"unsupported attribution claim in {relative}: {claim}")
        _require(LOCAL_PATH_PATTERN.search(text) is None, f"local absolute path in {relative}")
        for pattern in SECRET_PATTERNS:
            _require(pattern.search(text) is None, f"secret-like value in {relative}")


def _verify_no_project_license(root: Path) -> None:
    present = [name for name in ROOT_PROJECT_LICENSE_FILES if (root / name).exists()]
    _require(not present, f"project license or notice file is not allowed in Phase 6A: {present}")


def _read_authored_text(root: Path, relative: str) -> str:
    raw = _read_required_bytes(root, relative)
    _require(not raw.startswith(b"\xef\xbb\xbf"), f"{relative} contains a UTF-8 BOM")
    _require(b"\0" not in raw, f"{relative} contains NUL")
    normalized = raw.replace(b"\r\n", b"\n")
    _require(b"\r" not in normalized, f"{relative} contains unsupported line endings")
    text = normalized.decode("utf-8")
    _require(text.endswith("\n") and not text.endswith("\n\n"), f"{relative} must have one final newline")
    return text


def _read_license_bytes(root: Path, relative: str) -> bytes:
    data = _read_required_bytes(root, relative)
    _require(data, f"{relative} is empty")
    _require(not data.startswith(b"\xef\xbb\xbf"), f"{relative} contains a UTF-8 BOM")
    _require(b"\r" not in data, f"{relative} line endings differ from the selected upstream bytes")
    _require(b"\0" not in data, f"{relative} contains NUL")
    data.decode("utf-8")
    return data


def _read_required_bytes(root: Path, relative: str) -> bytes:
    path = root / relative
    _require(path.is_file(), f"required file is missing: {relative}")
    return path.read_bytes()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LegalVerificationError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
