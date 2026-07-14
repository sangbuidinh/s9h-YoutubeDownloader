from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

import verify_release_legal_payload as verifier


class LegalPayloadBuildError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify a release legal payload")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    _add_common_arguments(create, legal_name="--output-zip")
    verify = subparsers.add_parser("verify")
    _add_common_arguments(verify, legal_name="--legal-zip")
    args = parser.parse_args()
    try:
        if args.command == "create":
            create_release_legal_payload(
                control_root=args.control_root,
                portable_zip=args.portable_zip,
                output_zip=args.output_zip,
                release_notes=args.release_notes,
                tag=args.tag,
                source_commit=args.source_commit,
                control_commit=args.control_commit,
            )
            print("Release legal payload created and verified")
        else:
            verifier.verify_release_legal_payload(
                control_root=args.control_root,
                portable_zip=args.portable_zip,
                legal_zip=args.legal_zip,
                release_notes=args.release_notes,
                tag=args.tag,
                source_commit=args.source_commit,
                control_commit=args.control_commit,
            )
            print("Release legal payload verified")
    except (LegalPayloadBuildError, verifier.LegalPayloadError, OSError, zipfile.BadZipFile) as exc:
        print(f"Release legal payload error: {exc}", file=sys.stderr)
        return 1
    return 0


def _add_common_arguments(parser: argparse.ArgumentParser, *, legal_name: str) -> None:
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--portable-zip", required=True, type=Path)
    parser.add_argument(legal_name, required=True, type=Path)
    parser.add_argument("--release-notes", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--control-commit", required=True)


def create_release_legal_payload(
    *,
    control_root: Path,
    portable_zip: Path,
    output_zip: Path,
    release_notes: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
) -> None:
    verifier.validate_identity(tag, source_commit, control_commit)
    control_root = verifier.require_regular_directory(control_root, "control root")
    verifier.load_asset_contract(control_root / verifier.CONTRACT_PATH)
    portable_zip = portable_zip.expanduser().resolve(strict=False)
    output_zip = output_zip.expanduser().resolve(strict=False)
    verifier.require_regular_file(portable_zip, portable_zip.parent, "portable ZIP")
    release_notes = verifier.require_release_notes_path(release_notes, portable_zip)
    _require_output_path(output_zip, "legal payload ZIP")
    if output_zip == portable_zip:
        raise LegalPayloadBuildError("legal payload ZIP must differ from portable ZIP")

    original_portable_bytes = portable_zip.read_bytes()
    original_notes_bytes = release_notes.read_bytes()
    original_checksum = verifier.verify_release_notes_checksum(release_notes, portable_zip)
    original = verifier.read_zip_entries(portable_zip, "portable ZIP", deterministic=False)
    if any(name.casefold().startswith("legal/") for name in original):
        raise LegalPayloadBuildError("portable ZIP already contains legal material")
    payload = verifier.expected_payload_bytes(control_root)
    payload[verifier.MANIFEST_PATH] = verifier.build_manifest_bytes(
        payload=payload,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
    )
    if set(original).intersection(payload):
        raise LegalPayloadBuildError("portable and legal payload paths collide")

    try:
        _write_deterministic_zip(output_zip, payload, exclusive=True)
        _replace_portable_zip(portable_zip, {**original, **payload})
        final_checksum = verifier.sha256_file(portable_zip)
        updated_notes = verifier.replace_release_notes_checksum(
            original_notes_bytes,
            portable_zip.name,
            original_checksum,
            final_checksum,
        )
        _replace_file_bytes(release_notes, updated_notes, prefix=".s9h-release-notes-")
        verifier.verify_release_legal_payload(
            control_root=control_root,
            portable_zip=portable_zip,
            legal_zip=output_zip,
            release_notes=release_notes,
            tag=tag,
            source_commit=source_commit,
            control_commit=control_commit,
        )
    except Exception:
        restore_errors: list[Exception] = []
        for path, data, prefix in (
            (portable_zip, original_portable_bytes, ".s9h-portable-rollback-"),
            (release_notes, original_notes_bytes, ".s9h-release-notes-rollback-"),
        ):
            try:
                _replace_file_bytes(path, data, prefix=prefix)
            except Exception as restore_exc:
                restore_errors.append(restore_exc)
        try:
            output_zip.unlink(missing_ok=True)
        except Exception as restore_exc:
            restore_errors.append(restore_exc)
        if restore_errors:
            raise LegalPayloadBuildError("release legal payload rollback failed") from restore_errors[0]
        raise


def _replace_portable_zip(portable_zip: Path, entries: dict[str, bytes]) -> None:
    handle = tempfile.NamedTemporaryFile(
        prefix=".s9h-legal-payload-",
        suffix=".zip",
        dir=portable_zip.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    temporary.unlink()
    try:
        _write_deterministic_zip(temporary, entries, exclusive=True)
        os.replace(temporary, portable_zip)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_file_bytes(path: Path, data: bytes, *, prefix: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        prefix=prefix,
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_deterministic_zip(path: Path, entries: dict[str, bytes], *, exclusive: bool) -> None:
    mode = "x" if exclusive else "w"
    names = sorted(entries)
    if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}):
        raise LegalPayloadBuildError("ZIP entry names are not unique")
    with zipfile.ZipFile(path, mode, compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=verifier.FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = verifier.FIXED_FILE_MODE
            archive.writestr(info, entries[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _require_output_path(path: Path, label: str) -> None:
    parent = path.parent.resolve(strict=False)
    if not parent.is_dir() or _is_reparse(parent):
        raise LegalPayloadBuildError(f"{label} parent is unavailable")
    if path.exists():
        raise LegalPayloadBuildError(f"{label} already exists")


def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


if __name__ == "__main__":
    raise SystemExit(main())
