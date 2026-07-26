from __future__ import annotations

import builtins
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import release_sbom
import smoke_release_bundle as bundle_smoke


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "generate_release_sbom.py"
VERIFIER = REPO_ROOT / "scripts" / "verify_release_sbom.py"
WINDOWS_RESERVED_DEVICE_NAMES = (
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
    "COM\u00b9",
    "COM\u00b2",
    "COM\u00b3",
    "LPT\u00b9",
    "LPT\u00b2",
    "LPT\u00b3",
    "CONIN$",
    "CONOUT$",
)


def main() -> int:
    positive = 0
    negative = 0
    with tempfile.TemporaryDirectory(prefix="s9h-release-sbom-smoke-") as temp:
        root = Path(temp)
        fixture = bundle_smoke._release_fixture(root / "fixture", bundle_smoke.RC_TAG)
        evidence_path = Path(fixture["sbom_input"])
        evidence = release_sbom.load_input(evidence_path)
        _require(release_sbom.VALIDATOR_DISTRIBUTION == "fastjsonschema")
        _require(release_sbom.VALIDATOR_VERSION == "2.21.2")
        _require(
            release_sbom.importlib_metadata.version(release_sbom.VALIDATOR_DISTRIBUTION)
            == release_sbom.VALIDATOR_VERSION
        )
        positive += 1

        sbom_path = root / release_sbom.expected_filename(evidence["release"]["version"])
        output = _run(GENERATOR, "--input", evidence_path, "--output", sbom_path)
        _require(output == "Deterministic SPDX 2.3 SBOM generated: " + sbom_path.name)
        verify_output = _run(VERIFIER, "--input", evidence_path, "--sbom", sbom_path)
        _require(verify_output == "Deterministic SPDX 2.3 SBOM verified")
        positive += 1

        windows_positive, windows_negative = _test_windows_path_contract(
            evidence,
            root,
            sbom_path,
        )
        positive += windows_positive
        negative += windows_negative

        sbom_bytes = sbom_path.read_bytes()
        document = release_sbom.verify_document(sbom_bytes, evidence)
        _verify_canonical_document(sbom_bytes, document, evidence)
        positive += 1
        _verify_immutable_notices()
        positive += 1
        _verify_staged_release_sequence()
        positive += 1

        repeated = release_sbom.generate_bytes(evidence)
        _require(repeated == sbom_bytes)
        permuted = _permuted_input(evidence)
        _require(release_sbom.generate_bytes(permuted) == sbom_bytes)
        positive += 2

        _verify_relationships(document)
        positive += 2
        _verify_authoritative_and_unresolved(document)
        positive += 2

        bundle_root = root / "bundle"
        bundle_smoke._create_bundle(
            bundle_smoke._load_bundle_module(),
            fixture,
            bundle_root,
            bundle_smoke.RC_TAG,
            True,
        )
        manifest = json.loads((bundle_root / "RELEASE_MANIFEST.json").read_text("utf-8"))
        checksum_bytes = (bundle_root / "assets" / "SHA256SUMS.txt").read_bytes()
        integrated_sbom = bundle_root / "assets" / sbom_path.name
        release_sbom.verify_document(
            integrated_sbom.read_bytes(),
            evidence,
            final_manifest=manifest,
            final_checksum_bytes=checksum_bytes,
        )
        positive += 2
        negative += _test_final_bundle_windows_path_contract(
            sbom_bytes,
            evidence,
            manifest,
            checksum_bytes,
        )

        input_cases = _input_cases(evidence)
        for label, expected, mutated in input_cases:
            _expect_error(label, expected, lambda mutated=mutated: release_sbom.generate_bytes(mutated))
            negative += 1

        document_cases = _document_cases(document, evidence)
        for label, expected, mutated in document_cases:
            raw = release_sbom.canonical_json_bytes(mutated)
            _expect_error(
                label,
                expected,
                lambda raw=raw: release_sbom.verify_document(raw, evidence),
            )
            negative += 1

        with mock.patch.object(
            release_sbom.importlib_metadata,
            "version",
            side_effect=release_sbom.importlib_metadata.PackageNotFoundError(
                release_sbom.VALIDATOR_DISTRIBUTION
            ),
        ):
            _expect_error(
                "validator distribution metadata unavailable",
                "fastjsonschema distribution metadata is unavailable",
                lambda: release_sbom.generate_bytes(evidence),
            )
        negative += 1

        with mock.patch.object(
            release_sbom.importlib_metadata,
            "version",
            return_value="2.21.1",
        ):
            _expect_error(
                "wrong validator version",
                "fastjsonschema version must be exactly 2.21.2",
                lambda: release_sbom.generate_bytes(evidence),
            )
        negative += 1

        missing_schema_import = builtins.__import__

        def reject_validator(name, *args, **kwargs):
            if name == "fastjsonschema":
                raise ImportError("synthetic missing validator")
            return missing_schema_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=reject_validator):
            _expect_error(
                "schema validation unavailable",
                "real SPDX schema validation capability is unavailable",
                lambda: release_sbom.generate_bytes(evidence),
            )
        negative += 1

        changed_schema = root / "changed-spdx-schema.json"
        changed_schema.write_bytes(release_sbom.SCHEMA_PATH.read_bytes() + b" ")
        _expect_error(
            "schema immutable identity changed",
            "SPDX schema immutable identity is invalid",
            lambda: release_sbom.generate_bytes(evidence, schema_path=changed_schema),
        )
        negative += 1

        original_generate = release_sbom.generate_document
        calls = 0

        def nondeterministic(value):
            nonlocal calls
            calls += 1
            generated = original_generate(value)
            if calls == 2:
                generated["comment"] += " changed"
            return generated

        with mock.patch.object(release_sbom, "generate_document", side_effect=nondeterministic):
            _expect_error(
                "nondeterministic output",
                "SBOM output is nondeterministic",
                lambda: release_sbom.generate_bytes(evidence),
            )
        negative += 1

        _expect_error(
            "semantic reconciliation unavailable",
            "release manifest and checksum reconciliation must be available together",
            lambda: release_sbom.verify_document(
                sbom_bytes,
                evidence,
                final_manifest=manifest,
            ),
        )
        negative += 1

        manifest_without_sbom = copy.deepcopy(manifest)
        manifest_without_sbom["assets"] = [
            item for item in manifest_without_sbom["assets"] if item["role"] != "release-sbom"
        ]
        _expect_error(
            "SBOM omitted from bundle",
            "SBOM is missing from the integrated release bundle",
            lambda: release_sbom.reconcile_final_bundle(
                sbom_bytes,
                evidence,
                manifest_without_sbom,
                checksum_bytes,
            ),
        )
        negative += 1

        bad_manifest = copy.deepcopy(manifest)
        next(item for item in bad_manifest["assets"] if item["role"] == "release-sbom")[
            "sha256"
        ] = "0" * 64
        _expect_error(
            "SBOM manifest digest mismatch",
            "SBOM bytes differ from manifest or checksum evidence",
            lambda: release_sbom.reconcile_final_bundle(
                sbom_bytes,
                evidence,
                bad_manifest,
                checksum_bytes,
            ),
        )
        negative += 1

        bad_checksums = checksum_bytes.replace(
            next(
                item["sha256"]
                for item in manifest["assets"]
                if item["role"] == "release-sbom"
            ).encode("ascii"),
            b"0" * 64,
            1,
        )
        bad_checksum_manifest = copy.deepcopy(manifest)
        bad_checksum_manifest["checksum_file"] = {
            "name": "SHA256SUMS.txt",
            "size": len(bad_checksums),
            "sha256": hashlib.sha256(bad_checksums).hexdigest(),
        }
        _expect_error(
            "SBOM checksum digest mismatch",
            "SBOM bytes differ from manifest or checksum evidence",
            lambda: release_sbom.reconcile_final_bundle(
                sbom_bytes,
                evidence,
                bad_checksum_manifest,
                bad_checksums,
            ),
        )
        negative += 1

        bad_final_identity = copy.deepcopy(manifest)
        bad_final_identity["source_commit"] = "0" * 40
        _expect_error(
            "final manifest identity mismatch",
            "release manifest mismatch",
            lambda: release_sbom.reconcile_final_bundle(
                sbom_bytes,
                evidence,
                bad_final_identity,
                checksum_bytes,
            ),
        )
        negative += 1

        bad_checksum_record = copy.deepcopy(manifest)
        bad_checksum_record["checksum_file"]["sha256"] = "0" * 64
        _expect_error(
            "final manifest checksum record mismatch",
            "release manifest mismatch",
            lambda: release_sbom.reconcile_final_bundle(
                sbom_bytes,
                evidence,
                bad_checksum_record,
                checksum_bytes,
            ),
        )
        negative += 1

        tampered = bytearray(sbom_bytes)
        tampered[len(tampered) // 2] ^= 1
        _expect_error(
            "one-byte SBOM tamper",
            "",
            lambda: release_sbom.verify_document(bytes(tampered), evidence),
        )
        negative += 1

        wrong_output = root / "wrong.spdx.json"
        failed = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--input",
                str(evidence_path),
                "--output",
                str(wrong_output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        _require(failed.returncode == 1)
        _require("SBOM output filename is invalid" in failed.stderr)
        negative += 1

    print(f"release SBOM smoke tests passed: {positive} positive, {negative} negative")
    return 0


def _verify_canonical_document(
    raw: bytes,
    document: dict,
    evidence: dict,
) -> None:
    _require(not raw.startswith(b"\xef\xbb\xbf"))
    _require(b"\r" not in raw and raw.endswith(b"\n") and not raw.endswith(b"\n\n"))
    _require(raw == release_sbom.canonical_json_bytes(document))
    _require(document["spdxVersion"] == "SPDX-2.3")
    _require(document["dataLicense"] == "CC0-1.0")
    _require(document["name"] == release_sbom.expected_filename(evidence["release"]["version"]))
    _require(document["creationInfo"]["creators"] == [
        "Tool: s9h-project-owned-deterministic-spdx-generator-1.0.0"
    ])
    comment = json.loads(document["comment"])
    _require(comment["predicate_type"] == "https://spdx.dev/Document/v2.3")
    _require(comment["source_commit"] == evidence["release"]["source_commit"])
    _require(comment["control_commit"] == evidence["release"]["control_commit"])
    _require(comment["release_tag"] == evidence["release"]["tag"])
    _require(comment["synthetic"] is True)


def _verify_immutable_notices() -> None:
    identity_path = REPO_ROOT / "schemas" / "spdx-2.3" / "IDENTITY.json"
    identity_raw = identity_path.read_bytes()
    identity = json.loads(identity_raw)
    _require(identity_raw == release_sbom.canonical_json_bytes(identity))
    _require(identity["schema_blob_sha1"] == release_sbom.SCHEMA_BLOB_SHA1)


def _verify_staged_release_sequence() -> None:
    policy = json.loads(
        (REPO_ROOT / "legal" / "release-assurance-policy.json").read_text(encoding="utf-8")
    )
    sequence = policy["release_integration"]["sequence"]
    ordered = [
        "Authenticode-sign first-party executable",
        "verify Authenticode signature and timestamp",
        "assemble the portable package using the signed executable",
        "calculate final artifact checksums",
        "generate and validate the final SBOM",
        "synchronize checksums, release notes and release manifest",
        "generate provenance and SBOM attestations over final immutable subjects",
    ]
    _require([sequence.index(item) for item in ordered] == sorted(sequence.index(item) for item in ordered))

    for value in policy["claims"].values():
        _require(value is False)
    for value in policy["release_integration"]["readiness"].values():
        _require(value is False)
    _require(
        policy["sbom"]["implementation_evidence"]["production_sbom_generated"] is False
    )
    _require(
        policy["sbom"]["implementation_evidence"]["production_sbom_reconciled"] is False
    )

    release_doc = (REPO_ROOT / "docs" / "release-sbom.md").read_text(encoding="utf-8")
    feasibility_doc = (
        REPO_ROOT / "docs" / "sbom-generator-feasibility.md"
    ).read_text(encoding="utf-8")
    combined = release_doc + "\n" + feasibility_doc
    for phrase in (
        "provisional inventory",
        "synthetic SBOM",
        "production SBOM",
        "final signed production SBOM",
        "Authenticode-signed",
        "unsigned bytes must not be called final immutable release bytes",
    ):
        _require(phrase in combined)
    _require(
        "Phase 7B-R2 is limited to controlled final-build inventory collection"
        not in combined
    )
    schema_license = REPO_ROOT / "schemas" / "spdx-2.3" / "LICENSE"
    _require(
        release_sbom._git_blob_sha1(schema_license.read_bytes())
        == "44a22d370bba8d13c7dd7449d71b40ea8842788e"
    )
    validator_license = (
        REPO_ROOT
        / "scripts"
        / "vendor-notices"
        / "fastjsonschema-2.21.2-LICENSE.txt"
    )
    _require(
        hashlib.sha256(validator_license.read_bytes()).hexdigest()
        == "9ccddf69eb3998a60148debe85b94c5afed53691b6474692e78abcc0a0e544f1"
    )


def _verify_relationships(document: dict) -> None:
    relationships = document["relationships"]
    _require(sum(item["relationshipType"] == "DESCRIBES" for item in relationships) == 1)
    _require(
        sum(item["relationshipType"] == "CONTAINS" for item in relationships)
        == len(document["files"])
    )
    _require(
        sum(item["relationshipType"] == "DEPENDS_ON" for item in relationships)
        == len(document["packages"]) - 1
    )
    release_sbom._verify_semantic_relationships(
        document,
        {
            "release": {
                "version": document["name"]
                .removeprefix("Youtube-Downloaderbs-v")
                .removesuffix(".spdx.json")
            }
        },
    )


def _verify_authoritative_and_unresolved(document: dict) -> None:
    deno = next(item for item in document["packages"] if item["name"] == "Deno synthetic runtime")
    _require(deno["versionInfo"] == "2.7.14")
    _require(deno["licenseDeclared"] == "MIT")
    details = json.loads(deno["comment"])
    _require(
        details["field_provenance"]["license_declared"]
        == "controlled synthetic fixture declaration"
    )
    _require(details["unresolved"])
    unresolved = details["unresolved"][0]
    _require(unresolved["reason"])
    _require(unresolved["source"])
    _require(unresolved["provenance"])


def _permuted_input(evidence: dict) -> dict:
    changed = copy.deepcopy(evidence)
    for key in (
        "final_artifacts",
        "portable_files",
        "native_members",
        "legal_components",
        "unresolved_components",
        "checksum_records",
        "python_packages",
        "external_runtimes",
    ):
        changed[key].reverse()
    changed["release_manifest"]["assets"].reverse()
    changed["release_manifest"]["release_blockers"].reverse()
    changed["pyinstaller_inventory"]["carchive_members"].reverse()
    changed["pyinstaller_inventory"]["source_inventory"].reverse()
    changed["python_runtime"]["files"].reverse()
    for item in changed["python_packages"] + changed["external_runtimes"]:
        item["files"].reverse()
    return changed


def _test_windows_path_contract(
    evidence: dict,
    root: Path,
    sbom_path: Path,
) -> tuple[int, int]:
    positive = 0
    negative = 0

    _require(len(WINDOWS_RESERVED_DEVICE_NAMES) == 30)
    _require(
        {name.casefold() for name in WINDOWS_RESERVED_DEVICE_NAMES}
        == release_sbom.WINDOWS_RESERVED_DEVICE_BASENAMES
    )
    positive += 1

    safe_paths = (
        "conduit.txt",
        "auxiliary.txt",
        "com0.txt",
        "com10.txt",
        "lpt0.txt",
        "lpt10.txt",
        "name with internal spaces.txt",
        "file.name.txt",
        "company/file.txt",
    )
    for path in safe_paths:
        _require(release_sbom._canonical_path(path, "Windows path fixture") == path)
        positive += 1

    unsafe_paths: list[tuple[str, str]] = []
    for reserved in WINDOWS_RESERVED_DEVICE_NAMES:
        variants = (
            reserved,
            reserved.lower(),
            reserved[:1].lower() + reserved[1:].upper(),
        )
        for index, variant in enumerate(variants, start=1):
            unsafe_paths.append((f"{reserved} case variant {index}", variant))
        unsafe_paths.append((f"{reserved} extension", reserved + ".txt"))
        unsafe_paths.append((f"{reserved} parent segment", reserved + "/file.txt"))
    unsafe_paths.extend(
        [
            ("reserved basename with pre-extension space", "CON .txt"),
            ("trailing dot", "assets/file."),
            ("trailing space", "assets/file "),
            ("nested trailing dot", "assets/name."),
            ("nested trailing space", "assets/name "),
            ("dot-space suffix", "assets/name. "),
            ("forbidden less-than", "bad<name.txt"),
            ("forbidden greater-than", "bad>name.txt"),
            ("forbidden colon", "bad:name.txt"),
            ('forbidden quote', 'bad"name.txt'),
            ("forbidden backslash", "bad\\name.txt"),
            ("forbidden pipe", "bad|name.txt"),
            ("forbidden question mark", "bad?.txt"),
            ("forbidden asterisk", "bad*.txt"),
        ]
    )
    unsafe_paths.extend(
        (f"ASCII control U+{codepoint:04X}", f"bad{chr(codepoint)}name.txt")
        for codepoint in range(0x20)
    )
    for label, path in unsafe_paths:
        _expect_error(
            f"direct Windows path: {label}",
            "",
            lambda path=path: release_sbom._canonical_path(path, "Windows path fixture"),
        )
        negative += 1

    for label, paths in (
        ("trailing-dot alias", ["assets/file", "assets/file."]),
        ("trailing-space alias", ["assets/name", "assets/name "]),
        ("reserved-extension alias", ["dir/NUL", "dir/NUL.txt"]),
    ):
        _expect_error(
            label,
            "",
            lambda paths=paths: [
                release_sbom._canonical_path(path, "Windows alias fixture")
                for path in paths
            ],
        )
        negative += 1
    _expect_error(
        "case-only collision",
        "Windows case collision",
        lambda: release_sbom._require_unique_canonical_paths(
            ["assets/File.txt", "assets/file.txt"],
            duplicate_message="duplicate canonical path",
            collision_message="Windows case collision",
        ),
    )
    negative += 1

    boundary_mutations = (
        (
            "final executable",
            lambda data: data["final_executable"].__setitem__("path", "CON"),
        ),
        (
            "final artifact records",
            lambda data: data["final_artifacts"][0].__setitem__("path", "CON"),
        ),
        (
            "portable file records",
            lambda data: data["portable_files"][0].__setitem__("path", "CON"),
        ),
        (
            "PyInstaller CArchive members",
            lambda data: data["pyinstaller_inventory"]["carchive_members"].__setitem__(
                0, "CON"
            ),
        ),
        (
            "PyInstaller source inventory",
            lambda data: data["pyinstaller_inventory"]["source_inventory"][0].__setitem__(
                "path", "CON"
            ),
        ),
        (
            "Python runtime file list",
            lambda data: data["python_runtime"]["files"].__setitem__(0, "CON"),
        ),
        (
            "Python package file list",
            lambda data: data["python_packages"][0]["files"].__setitem__(0, "CON"),
        ),
        (
            "native member list",
            lambda data: data["native_members"].__setitem__(0, "CON"),
        ),
        (
            "external runtime file list",
            lambda data: data["external_runtimes"][0]["files"].__setitem__(0, "CON"),
        ),
        (
            "release manifest asset names",
            lambda data: data["release_manifest"]["assets"][0].__setitem__("name", "CON"),
        ),
        (
            "release manifest checksum record",
            lambda data: data["release_manifest"]["checksum_file"].__setitem__(
                "name", "CON"
            ),
        ),
        (
            "release manifest release-notes record",
            lambda data: data["release_manifest"]["release_notes"].__setitem__(
                "name", "CON"
            ),
        ),
        (
            "checksum record names",
            lambda data: data["checksum_records"][0].__setitem__("name", "CON"),
        ),
    )
    for label, mutate in boundary_mutations:
        changed = copy.deepcopy(evidence)
        mutate(changed)
        _expect_error(
            f"full-evidence Windows boundary: {label}",
            "reserved Windows device basename",
            lambda changed=changed: release_sbom.generate_bytes(changed),
        )
        negative += 1

    verifier_cases = (
        ("reserved device", "CON"),
        ("trailing dot", "assets/file."),
        ("trailing space", "assets/file "),
        ("forbidden character", "assets/bad?.txt"),
        ("ASCII control", "assets/bad\u0001name.txt"),
    )
    for index, (label, unsafe_path) in enumerate(verifier_cases, start=1):
        changed = copy.deepcopy(evidence)
        changed["portable_files"][0]["path"] = unsafe_path
        input_path = root / f"unsafe-windows-input-{index:02d}.json"
        input_path.write_bytes(release_sbom.canonical_json_bytes(changed))
        result = subprocess.run(
            [
                sys.executable,
                str(VERIFIER),
                "--input",
                str(input_path),
                "--sbom",
                str(sbom_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        _require(result.returncode == 1)
        _require("Release SBOM verification error:" in result.stderr)
        _require("Deterministic SPDX 2.3 SBOM verified" not in result.stdout)
        negative += 1

    return positive, negative


def _test_final_bundle_windows_path_contract(
    sbom_bytes: bytes,
    evidence: dict,
    manifest: dict,
    checksum_bytes: bytes,
) -> int:
    negative = 0

    unsafe_manifest = copy.deepcopy(manifest)
    unsafe_manifest["assets"][0]["name"] = "CON"
    unsafe_manifest["assets"].sort(key=lambda item: item["name"])
    _expect_error(
        "final bundle manifest reserved device",
        "reserved Windows device basename",
        lambda: release_sbom.reconcile_final_bundle(
            sbom_bytes,
            evidence,
            unsafe_manifest,
            checksum_bytes,
        ),
    )
    negative += 1

    colliding_manifest = copy.deepcopy(manifest)
    collision_record = copy.deepcopy(colliding_manifest["assets"][0])
    collision_record["name"] = collision_record["name"].swapcase()
    colliding_manifest["assets"].append(collision_record)
    colliding_manifest["assets"].sort(key=lambda item: item["name"])
    _expect_error(
        "final bundle manifest Windows collision",
        "release manifest mismatch",
        lambda: release_sbom.reconcile_final_bundle(
            sbom_bytes,
            evidence,
            colliding_manifest,
            checksum_bytes,
        ),
    )
    negative += 1

    checksum_lines = checksum_bytes.decode("utf-8").rstrip("\n").split("\n")
    digest, _ = checksum_lines[0].split("  ", 1)
    unsafe_checksum_bytes = (
        "\n".join(sorted([f"{digest}  CON", *checksum_lines[1:]])) + "\n"
    ).encode("utf-8")
    unsafe_checksum_manifest = copy.deepcopy(manifest)
    unsafe_checksum_manifest["checksum_file"] = {
        "name": "SHA256SUMS.txt",
        "size": len(unsafe_checksum_bytes),
        "sha256": hashlib.sha256(unsafe_checksum_bytes).hexdigest(),
    }
    _expect_error(
        "final checksum reserved device",
        "reserved Windows device basename",
        lambda: release_sbom.reconcile_final_bundle(
            sbom_bytes,
            evidence,
            unsafe_checksum_manifest,
            unsafe_checksum_bytes,
        ),
    )
    negative += 1

    first_digest, first_name = checksum_lines[0].split("  ", 1)
    colliding_checksum_bytes = (
        "\n".join(
            sorted(
                [
                    *checksum_lines,
                    f"{first_digest}  {first_name.swapcase()}",
                ]
            )
        )
        + "\n"
    ).encode("utf-8")
    colliding_checksum_manifest = copy.deepcopy(manifest)
    colliding_checksum_manifest["checksum_file"] = {
        "name": "SHA256SUMS.txt",
        "size": len(colliding_checksum_bytes),
        "sha256": hashlib.sha256(colliding_checksum_bytes).hexdigest(),
    }
    _expect_error(
        "final checksum Windows collision",
        "Windows case collision",
        lambda: release_sbom.reconcile_final_bundle(
            sbom_bytes,
            evidence,
            colliding_checksum_manifest,
            colliding_checksum_bytes,
        ),
    )
    negative += 1

    return negative


def _input_cases(evidence: dict) -> list[tuple[str, str, dict]]:
    cases = []

    def add(label: str, expected: str, mutate) -> None:
        changed = copy.deepcopy(evidence)
        mutate(changed)
        cases.append((label, expected, changed))

    add("missing final package inventory", "final package inventory is missing", lambda d: d["final_artifacts"].clear())
    add("missing checksum input", "checksum input is missing", lambda d: d["checksum_records"].clear())
    add("checksum mismatch", "checksum input does not match", lambda d: d["checksum_records"][0].__setitem__("sha256", "0" * 64))
    add("duplicate canonical path", "duplicate canonical path", lambda d: d["portable_files"].append(copy.deepcopy(d["portable_files"][0])))
    add("unsafe backslash path", "unsafe path", lambda d: d["portable_files"][0].__setitem__("path", "bad\\path"))
    add("absolute path", "absolute path", lambda d: d["portable_files"][0].__setitem__("path", "C:/bad"))
    add("parent traversal", "parent traversal", lambda d: d["portable_files"][0].__setitem__("path", "../bad"))
    add("Windows case collision", "Windows case collision", lambda d: _append_case_collision(d))
    add("release manifest mismatch", "release manifest mismatch", lambda d: d["release_manifest"]["assets"][0].__setitem__("size", 1))
    add("unaccounted distributed file", "unaccounted distributed file", lambda d: _clear_file_association(d))
    add("unresolved file without record", "unresolved association lacks an explicit record", lambda d: _missing_unresolved_association(d))
    add("external runtime omitted", "required external runtime is omitted", lambda d: d["external_runtimes"].pop())
    add("Python runtime omitted", "Python runtime is omitted", lambda d: d["python_runtime"].update({"component_id": "application", "files": ["app.pyz"]}))
    add("PyInstaller source mismatch", "PyInstaller inventory/source mismatch", lambda d: d["pyinstaller_inventory"]["carchive_members"].pop())
    add("malformed source commit", "source commit is invalid", lambda d: d["release"].__setitem__("source_commit", "bad"))
    add("malformed control commit", "control commit is invalid", lambda d: d["release"].__setitem__("control_commit", "bad"))
    add("malformed release tag", "release tag is invalid", lambda d: d["release"].__setitem__("tag", "release"))
    add("native member omitted", "native member inventory is invalid", lambda d: d["native_members"].pop())
    add("fabricated component association", "unknown component", lambda d: d["portable_files"][0].__setitem__("component_id", "fabricated"))
    add("missing unresolved reason", "reason/source/provenance is required", lambda d: d["unresolved_components"][0].__setitem__("reason", ""))
    add("file unresolved record has component", "unresolved association lacks an explicit record", lambda d: _component_bound_unresolved_file(d))
    add("invalid checksum filename", "release manifest evidence record is invalid", lambda d: d["release_manifest"]["checksum_file"].__setitem__("name", "OTHER.txt"))
    add("invalid release notes filename", "release manifest evidence record is invalid", lambda d: d["release_manifest"]["release_notes"].__setitem__("name", "OTHER.md"))
    add("production-labeled synthetic fixture", "synthetic fixture cannot be represented as production evidence", lambda d: d.update({"evidence_type": "production-release", "synthetic": False, "distribution_allowed": True}))
    add("synthetic distribution enabled", "synthetic fixture cannot be represented as production evidence", lambda d: d.__setitem__("distribution_allowed", True))
    add("final executable hash changed", "final executable identity does not match", lambda d: d["final_executable"].__setitem__("sha256", "0" * 64))
    add("component duplicate", "component IDs are duplicated", lambda d: d["legal_components"].append(copy.deepcopy(d["legal_components"][0])))
    add("schema identity malformed", "SBOM input fields are invalid", lambda d: d.__setitem__("unexpected", False))
    add("v2 evidence passed as v3", "release manifest mismatch", lambda d: d["release_manifest"].update({"schema_version": 2, "bundle_format": "s9h-release-bundle-v2"}))
    return cases


def _document_cases(document: dict, evidence: dict) -> list[tuple[str, str, dict]]:
    cases = []

    def add(label: str, expected: str, mutate) -> None:
        changed = copy.deepcopy(document)
        mutate(changed)
        cases.append((label, expected, changed))

    add("relationship omission", "SPDX semantic reconciliation failed", lambda d: d["relationships"].pop())
    add("DESCRIBES omission", "SPDX semantic reconciliation failed", lambda d: d["relationships"].__setitem__(slice(None), [r for r in d["relationships"] if r["relationshipType"] != "DESCRIBES"]))
    add("malformed SPDX identity", "SPDX semantic reconciliation failed", lambda d: d.__setitem__("SPDXID", "invalid"))
    add("schema-invalid document", "SPDX schema validation failed", lambda d: d.pop("dataLicense"))
    add("semantic-invalid schema-valid document", "SPDX semantic reconciliation failed", lambda d: d.__setitem__("comment", d["comment"] + " "))
    add("fabricated authoritative field", "SPDX semantic reconciliation failed", lambda d: d["packages"][0].__setitem__("supplier", "Organization: Fabricated"))
    add("SBOM identity mismatch", "SPDX semantic reconciliation failed", lambda d: d.__setitem__("name", "Youtube-Downloaderbs-v9.9.9.spdx.json"))
    add("file checksum changed", "SPDX semantic reconciliation failed", lambda d: d["files"][0]["checksums"][0].__setitem__("checksumValue", "0" * 64))
    return cases


def _append_case_collision(data: dict) -> None:
    record = copy.deepcopy(data["portable_files"][0])
    record["path"] = record["path"].upper()
    data["portable_files"].append(record)


def _clear_file_association(data: dict) -> None:
    record = next(item for item in data["portable_files"] if item["component_id"] == "release")
    record["component_id"] = None
    record["unresolved_id"] = None


def _missing_unresolved_association(data: dict) -> None:
    record = next(item for item in data["portable_files"] if item["component_id"] == "release")
    record["component_id"] = None
    record["unresolved_id"] = "missing-record"


def _component_bound_unresolved_file(data: dict) -> None:
    record = next(item for item in data["portable_files"] if item["component_id"] == "release")
    unresolved = next(item for item in data["unresolved_components"] if item["component_id"] == "release")
    unresolved["fields"].append("component_association")
    record["component_id"] = None
    record["unresolved_id"] = unresolved["id"]


def _run(script: Path, *arguments: object) -> str:
    result = subprocess.run(
        [sys.executable, str(script), *(str(value) for value in arguments)],
        capture_output=True,
        text=True,
        check=True,
    )
    _require(not result.stderr)
    return result.stdout.strip()


def _expect_error(label: str, expected: str, callback) -> None:
    try:
        callback()
    except release_sbom.SbomError as exc:
        if expected and expected not in str(exc):
            raise AssertionError(f"{label}: unexpected error: {exc}") from exc
        return
    raise AssertionError(f"mutation was accepted: {label}")


def _require(condition: bool) -> None:
    if not condition:
        raise AssertionError("release SBOM smoke assertion failed")


if __name__ == "__main__":
    raise SystemExit(main())
