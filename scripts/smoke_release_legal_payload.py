from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path
from typing import Callable
from unittest import mock

import prepare_release_legal_payload as builder
import verify_release_legal_payload as verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
TAG = "v0.0.0-test"
SOURCE_COMMIT = "1" * 40
CONTROL_COMMIT = "2" * 40
PORTABLE_NAME = f"Youtube-Downloaderbs-{TAG}.zip"
LEGAL_NAME = f"Youtube-Downloaderbs-{TAG}-legal.zip"
EXE_CHECKSUM = "a" * 64


def main() -> int:
    _test_positive_contract()
    _test_release_notes_rejections()
    _test_transactional_rollback()
    _run_negative_mutations()
    print("release legal payload smoke tests passed")
    return 0


def _test_positive_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="legal-payload-positive-") as temp:
        root = Path(temp)
        first = _release_fixture(root / "first")
        second = _release_fixture(root / "second")
        first_original_portable = first["portable"].read_bytes()
        first_original_notes = first["notes"].read_bytes()
        original_checksum = verifier.sha256_file(first["portable"])

        _create(**first)
        _create(**second)
        final_checksum = verifier.sha256_file(first["portable"])
        _require(original_checksum != final_checksum, "portable ZIP checksum did not change")
        _require(first["portable"].read_bytes() != first_original_portable, "portable ZIP was not injected")
        _require(
            verifier.verify_release_notes_checksum(first["notes"], first["portable"])
            == final_checksum,
            "release notes checksum was not updated",
        )
        restored_notes = verifier.replace_release_notes_checksum(
            first["notes"].read_bytes(),
            PORTABLE_NAME,
            final_checksum,
            original_checksum,
        )
        _require(restored_notes == first_original_notes, "non-checksum release note bytes changed")
        _require(EXE_CHECKSUM.encode("ascii") in first["notes"].read_bytes(), "EXE checksum changed")
        _require(first["legal"].read_bytes() == second["legal"].read_bytes(), "legal ZIP is not deterministic")
        _require(
            first["portable"].read_bytes() == second["portable"].read_bytes(),
            "portable ZIP injection is not deterministic",
        )
        _require(first["notes"].read_bytes() == second["notes"].read_bytes(), "release notes update is not deterministic")
        manifest = verifier.verify_release_legal_payload(
            control_root=first["control"],
            portable_zip=first["portable"],
            legal_zip=first["legal"],
            release_notes=first["notes"],
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
        )
        legal_entries = verifier.read_zip_entries(first["legal"], "legal ZIP", deterministic=True)
        portable_entries = verifier.read_zip_entries(first["portable"], "portable ZIP", deterministic=True)
        portable_legal = {
            name: data for name, data in portable_entries.items() if name.startswith("legal/")
        }
        _require(legal_entries == portable_legal, "companion and portable payload bytes differ")
        _require(len(legal_entries) == 16, "generated legal payload count changed")
        _require(len(manifest["files"]) == 15, "manifest file count changed")
        _require(manifest["project_license_status"] == "not-selected", "project license status changed")
        _require(manifest["legal_compliance_certified"] is False, "compliance status changed")
        _require(manifest["source_availability_certified"] is False, "source status changed")
        _require(manifest["source_kits_ready"] is False, "source kit status changed")
        license_names = [name for name in legal_entries if name.startswith("legal/licenses/")]
        _require(len(license_names) == 8, "license payload count changed")
        source_kits = json.loads((first["control"] / "legal/source-kit-requirements.json").read_text("utf-8"))
        _require(all(kit["status"] == "blocked" for kit in source_kits["kits"]), "source kit is not blocked")

        for index, (newline, bom, heading) in enumerate(
            (
                (b"\r\n", False, "## Checksums"),
                (b"\n", True, "## Build checksums"),
                (b"\r\n", True, "## Build checksums"),
            )
        ):
            fixture = _release_fixture(root / f"format-{index}", newline=newline, bom=bom, heading=heading)
            original_notes = fixture["notes"].read_bytes()
            old_checksum = verifier.sha256_file(fixture["portable"])
            _create(**fixture)
            updated = fixture["notes"].read_bytes()
            new_checksum = verifier.sha256_file(fixture["portable"])
            _require(updated.startswith(b"\xef\xbb\xbf") is bom, "release notes BOM state changed")
            content = updated[3:] if bom else updated
            if newline == b"\r\n":
                _require(b"\r\n" in content and b"\n" not in content.replace(b"\r\n", b""), "CRLF was not preserved")
            else:
                _require(b"\r" not in content, "LF was not preserved")
            _require(
                verifier.replace_release_notes_checksum(updated, PORTABLE_NAME, new_checksum, old_checksum)
                == original_notes,
                "formatted release note bytes changed outside the checksum",
            )


def _test_release_notes_rejections() -> None:
    mutations: tuple[tuple[str, Callable[[bytes, Path], bytes]], ...] = (
        ("stale pre-injection hash", lambda raw, portable: _replace_hash(raw, "0" * 64)),
        ("missing hash", lambda raw, portable: _remove_portable_line(raw)),
        ("duplicate hash", lambda raw, portable: raw + _portable_checksum_line(verifier.sha256_file(portable), b"\n")),
        ("malformed hash", lambda raw, portable: _replace_hash(raw, "f" * 63)),
        ("wrong filename", lambda raw, portable: raw.replace(PORTABLE_NAME.encode("ascii"), b"wrong-portable.zip")),
        ("local absolute path", lambda raw, portable: raw + b"- Path: C:\\private\\release.zip\n"),
        ("secret-like addition", lambda raw, portable: raw + b"- Cookie: SID=synthetic-secret-value\n"),
    )
    with tempfile.TemporaryDirectory(prefix="legal-notes-negative-") as temp:
        root = Path(temp)
        for index, (label, mutation) in enumerate(mutations):
            fixture = _release_fixture(root / f"case-{index}")
            fixture["notes"].write_bytes(mutation(fixture["notes"].read_bytes(), fixture["portable"]))
            _expect_creation_error(label, fixture)

        missing = _release_fixture(root / "missing")
        missing["notes"].unlink()
        _expect_creation_error("missing release notes", missing)

        outside = _release_fixture(root / "outside")
        outside_notes = root / "outside-notes.md"
        shutil.copyfile(outside["notes"], outside_notes)
        outside["notes"] = outside_notes
        _expect_creation_error("release notes outside allowed root", outside)

        reparse = _release_fixture(root / "reparse")
        real_is_reparse = verifier._is_reparse

        def simulated_reparse(path: Path) -> bool:
            return path.resolve(strict=False) == reparse["notes"].resolve(strict=False) or real_is_reparse(path)

        with mock.patch.object(verifier, "_is_reparse", simulated_reparse):
            _expect_creation_error("reparse release notes", reparse)

        final_stale = _release_fixture(root / "final-stale")
        original_checksum = verifier.sha256_file(final_stale["portable"])
        _create(**final_stale)
        final_checksum = verifier.sha256_file(final_stale["portable"])
        final_stale["notes"].write_bytes(
            verifier.replace_release_notes_checksum(
                final_stale["notes"].read_bytes(),
                PORTABLE_NAME,
                final_checksum,
                original_checksum,
            )
        )
        _expect_verification_error("final stale release notes checksum", final_stale)


def _test_transactional_rollback() -> None:
    with tempfile.TemporaryDirectory(prefix="legal-payload-rollback-") as temp:
        fixture = _release_fixture(Path(temp) / "release-fixture")
        original_portable = fixture["portable"].read_bytes()
        original_notes = fixture["notes"].read_bytes()
        with mock.patch.object(
            verifier,
            "verify_release_legal_payload",
            side_effect=verifier.LegalPayloadError("forced post-write verification failure"),
        ):
            _expect_creation_error("forced post-write verification failure", fixture)
        _require(fixture["portable"].read_bytes() == original_portable, "portable rollback was not byte-exact")
        _require(fixture["notes"].read_bytes() == original_notes, "release notes rollback was not byte-exact")
        _require(not fixture["legal"].exists(), "failed legal ZIP was not deleted")
        _require(not list(fixture["release"].rglob(".s9h-*")), "transaction left a temporary file")


def _run_negative_mutations() -> None:
    mutations: tuple[tuple[str, Callable[[Path, Path, Path], None]], ...] = (
        ("missing legal file", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.pop("legal/materials/README.md"))),
        ("extra legal file", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.update({"legal/EXTRA.txt": b"extra\n"}))),
        ("changed license byte", lambda control, portable, legal: _mutate_entry(legal, "legal/licenses/Apache-2.0.txt", _append)),
        ("changed notice byte", lambda control, portable, legal: _mutate_entry(legal, "legal/THIRD_PARTY_NOTICES.md", _append)),
        ("changed components JSON", lambda control, portable, legal: _mutate_entry(legal, "legal/materials/components.json", _append)),
        ("manifest wrong source commit", lambda control, portable, legal: _manifest_field(legal, "source_commit", "3" * 40)),
        ("manifest wrong control commit", lambda control, portable, legal: _manifest_field(legal, "control_commit", "4" * 40)),
        ("project license selected", lambda control, portable, legal: _manifest_field(legal, "project_license_status", "MIT")),
        ("compliance true", lambda control, portable, legal: _manifest_field(legal, "legal_compliance_certified", True)),
        ("source availability true", lambda control, portable, legal: _manifest_field(legal, "source_availability_certified", True)),
        ("source kits ready true", lambda control, portable, legal: _manifest_field(legal, "source_kits_ready", True)),
        ("source archive inserted", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.update({"legal/source.zip": b"not a source kit\n"}))),
        ("EXE inserted under legal", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.update({"legal/tool.exe": b"MZ synthetic\n"}))),
        ("duplicate ZIP member", lambda control, portable, legal: _duplicate_member(legal)),
        ("case-insensitive collision", lambda control, portable, legal: _case_collision(legal)),
        ("traversal", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.update({"../escape.txt": b"escape\n"}))),
        ("absolute path", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.update({"/absolute.txt": b"absolute\n"}))),
        ("backslash path", lambda control, portable, legal: _mutate_entries(legal, lambda e: e.update({"legal\\escape.txt": b"escape\n"}))),
        ("encrypted member", lambda control, portable, legal: _set_encrypted_flag(legal)),
        ("non-deterministic timestamp", lambda control, portable, legal: _rewrite_timestamp(legal)),
        ("BOM JSON", lambda control, portable, legal: _mutate_entry(legal, verifier.MANIFEST_PATH, lambda data: b"\xef\xbb\xbf" + data)),
        ("CRLF JSON", lambda control, portable, legal: _mutate_entry(legal, verifier.MANIFEST_PATH, lambda data: data.replace(b"\n", b"\r\n"))),
        ("local absolute path", lambda control, portable, legal: _manifest_field(legal, "project_license_status", "C:\\private\\license")),
        ("secret-like value", lambda control, portable, legal: _manifest_field(legal, "project_license_status", "SID=synthetic-secret")),
        ("missing final newline", lambda control, portable, legal: _mutate_entry(legal, verifier.MANIFEST_PATH, lambda data: data.rstrip(b"\n"))),
    )
    for label, mutation in mutations:
        with tempfile.TemporaryDirectory(prefix="legal-payload-negative-") as temp:
            root = Path(temp)
            fixture = _release_fixture(root / "fixture")
            _create(**fixture)
            mutation(fixture["control"], fixture["portable"], fixture["legal"])
            try:
                verifier.verify_release_legal_payload(
                    control_root=fixture["control"],
                    portable_zip=fixture["portable"],
                    legal_zip=fixture["legal"],
                    release_notes=fixture["notes"],
                    tag=TAG,
                    source_commit=SOURCE_COMMIT,
                    control_commit=CONTROL_COMMIT,
                )
            except (verifier.LegalPayloadError, OSError, RuntimeError, zipfile.BadZipFile):
                continue
            raise AssertionError(f"legal payload mutation was not rejected: {label}")


def _release_fixture(
    root: Path,
    *,
    newline: bytes = b"\n",
    bom: bool = False,
    heading: str = "## Build checksums",
) -> dict[str, Path]:
    control = root / "control"
    release = root / "release"
    assets = release / "assets"
    assets.mkdir(parents=True)
    _copy_control_fixture(control)
    portable = assets / PORTABLE_NAME
    legal = assets / LEGAL_NAME
    notes = release / verifier.RELEASE_NOTES_NAME
    _write_initial_portable(portable)
    _write_release_notes(notes, portable, newline=newline, bom=bom, heading=heading)
    return {
        "control": control,
        "release": release,
        "portable": portable,
        "legal": legal,
        "notes": notes,
    }


def _copy_control_fixture(control: Path) -> None:
    contract = verifier.load_asset_contract(REPO_ROOT / verifier.CONTRACT_PATH)
    del contract
    for relative in (verifier.CONTRACT_PATH, *verifier.LEGAL_PAYLOAD_FILES):
        source = REPO_ROOT / relative
        destination = control / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _write_initial_portable(path: Path) -> None:
    builder._write_deterministic_zip(
        path,
        {"app/SYNTHETIC.txt": b"Synthetic portable fixture; not for distribution.\n"},
        exclusive=True,
    )


def _create(
    *,
    control: Path,
    release: Path,
    portable: Path,
    legal: Path,
    notes: Path,
) -> None:
    del release
    builder.create_release_legal_payload(
        control_root=control,
        portable_zip=portable,
        output_zip=legal,
        release_notes=notes,
        tag=TAG,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
    )


def _write_release_notes(
    path: Path,
    portable: Path,
    *,
    newline: bytes,
    bom: bool,
    heading: str,
) -> None:
    lines = (
        "# Synthetic release fixture",
        "",
        heading,
        "",
        f"- `Youtube.Downloaderbs.exe`: `{EXE_CHECKSUM}`",
        f"- `{portable.name}`: `{verifier.sha256_file(portable)}`",
    )
    body = newline.join(line.encode("utf-8") for line in lines) + newline
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + body)


def _portable_checksum_line(checksum: str, newline: bytes) -> bytes:
    return f"- `{PORTABLE_NAME}`: `{checksum}`".encode("ascii") + newline


def _replace_hash(raw: bytes, replacement: str) -> bytes:
    _, start, end = verifier.parse_release_notes_checksum(raw, PORTABLE_NAME)
    return raw[:start] + replacement.encode("ascii") + raw[end:]


def _remove_portable_line(raw: bytes) -> bytes:
    return b"".join(line for line in raw.splitlines(keepends=True) if PORTABLE_NAME.encode("ascii") not in line)


def _expect_creation_error(label: str, fixture: dict[str, Path]) -> None:
    original_portable = fixture["portable"].read_bytes()
    original_notes = fixture["notes"].read_bytes() if fixture["notes"].is_file() else None
    try:
        _create(**fixture)
    except (builder.LegalPayloadBuildError, verifier.LegalPayloadError, OSError, RuntimeError, zipfile.BadZipFile):
        _require(fixture["portable"].read_bytes() == original_portable, f"{label}: portable changed")
        if original_notes is not None:
            _require(fixture["notes"].read_bytes() == original_notes, f"{label}: notes changed")
        _require(not fixture["legal"].exists(), f"{label}: legal ZIP was retained")
        return
    raise AssertionError(f"legal payload creation mutation was not rejected: {label}")


def _expect_verification_error(label: str, fixture: dict[str, Path]) -> None:
    try:
        verifier.verify_release_legal_payload(
            control_root=fixture["control"],
            portable_zip=fixture["portable"],
            legal_zip=fixture["legal"],
            release_notes=fixture["notes"],
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
        )
    except (verifier.LegalPayloadError, OSError, RuntimeError, zipfile.BadZipFile):
        return
    raise AssertionError(f"legal payload verification mutation was not rejected: {label}")


def _mutate_entries(path: Path, mutation: Callable[[dict[str, bytes]], object]) -> None:
    entries = verifier.read_zip_entries(path, "mutation ZIP", deterministic=True)
    mutation(entries)
    path.unlink()
    builder._write_deterministic_zip(path, entries, exclusive=True)


def _mutate_entry(path: Path, name: str, mutation: Callable[[bytes], bytes]) -> None:
    _mutate_entries(path, lambda entries: entries.__setitem__(name, mutation(entries[name])))


def _manifest_field(path: Path, key: str, value: object) -> None:
    def mutate(entries: dict[str, bytes]) -> None:
        manifest = json.loads(entries[verifier.MANIFEST_PATH].decode("utf-8"))
        manifest[key] = value
        entries[verifier.MANIFEST_PATH] = (
            json.dumps(manifest, indent=2, ensure_ascii=True) + "\n"
        ).encode("utf-8")

    _mutate_entries(path, mutate)


def _duplicate_member(path: Path) -> None:
    entries = verifier.read_zip_entries(path, "mutation ZIP", deterministic=True)
    path.unlink()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(entries.items()):
                _write_member(archive, name, data)
            name = sorted(entries)[0]
            _write_member(archive, name, entries[name])


def _case_collision(path: Path) -> None:
    entries = verifier.read_zip_entries(path, "mutation ZIP", deterministic=True)
    entries["LEGAL/THIRD_PARTY_NOTICES.md"] = b"collision\n"
    path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            _write_member(archive, name, data)


def _rewrite_timestamp(path: Path) -> None:
    entries = verifier.read_zip_entries(path, "mutation ZIP", deterministic=True)
    path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for index, (name, data) in enumerate(sorted(entries.items())):
            timestamp = (2024, 1, 1, 0, 0, 0) if index == 0 else verifier.FIXED_ZIP_TIMESTAMP
            _write_member(archive, name, data, timestamp=timestamp)


def _write_member(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    *,
    timestamp: tuple[int, int, int, int, int, int] = verifier.FIXED_ZIP_TIMESTAMP,
) -> None:
    info = zipfile.ZipInfo(name, date_time=timestamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = verifier.FIXED_FILE_MODE
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _set_encrypted_flag(path: Path) -> None:
    data = bytearray(path.read_bytes())
    local = data.find(b"PK\x03\x04")
    central = data.find(b"PK\x01\x02")
    _require(local >= 0 and central >= 0, "ZIP headers are missing")
    for offset in (local + 6, central + 8):
        flags = struct.unpack_from("<H", data, offset)[0]
        struct.pack_into("<H", data, offset, flags | 0x1)
    path.write_bytes(data)


def _append(data: bytes) -> bytes:
    return data + b"changed\n"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
