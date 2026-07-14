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

import prepare_release_legal_payload as builder
import verify_release_legal_payload as verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
TAG = "v0.0.0-test"
SOURCE_COMMIT = "1" * 40
CONTROL_COMMIT = "2" * 40


def main() -> int:
    _test_positive_contract()
    _run_negative_mutations()
    print("release legal payload smoke tests passed")
    return 0


def _test_positive_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="legal-payload-positive-") as temp:
        root = Path(temp)
        control = root / "control"
        _copy_control_fixture(control)
        first_portable = root / "portable-one.zip"
        second_portable = root / "portable-two.zip"
        _write_initial_portable(first_portable)
        shutil.copyfile(first_portable, second_portable)
        first_legal = root / "legal-one.zip"
        second_legal = root / "legal-two.zip"

        _create(control, first_portable, first_legal)
        _create(control, second_portable, second_legal)
        _require(first_legal.read_bytes() == second_legal.read_bytes(), "legal ZIP is not deterministic")
        _require(
            first_portable.read_bytes() == second_portable.read_bytes(),
            "portable ZIP injection is not deterministic",
        )
        manifest = verifier.verify_release_legal_payload(
            control_root=control,
            portable_zip=first_portable,
            legal_zip=first_legal,
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
        )
        legal_entries = verifier.read_zip_entries(first_legal, "legal ZIP", deterministic=True)
        portable_entries = verifier.read_zip_entries(first_portable, "portable ZIP", deterministic=True)
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
        source_kits = json.loads((control / "legal/source-kit-requirements.json").read_text("utf-8"))
        _require(all(kit["status"] == "blocked" for kit in source_kits["kits"]), "source kit is not blocked")


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
            control = root / "control"
            _copy_control_fixture(control)
            portable = root / "portable.zip"
            legal = root / "legal.zip"
            _write_initial_portable(portable)
            _create(control, portable, legal)
            mutation(control, portable, legal)
            try:
                verifier.verify_release_legal_payload(
                    control_root=control,
                    portable_zip=portable,
                    legal_zip=legal,
                    tag=TAG,
                    source_commit=SOURCE_COMMIT,
                    control_commit=CONTROL_COMMIT,
                )
            except (verifier.LegalPayloadError, OSError, RuntimeError, zipfile.BadZipFile):
                continue
            raise AssertionError(f"legal payload mutation was not rejected: {label}")


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


def _create(control: Path, portable: Path, legal: Path) -> None:
    builder.create_release_legal_payload(
        control_root=control,
        portable_zip=portable,
        output_zip=legal,
        tag=TAG,
        source_commit=SOURCE_COMMIT,
        control_commit=CONTROL_COMMIT,
    )


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
