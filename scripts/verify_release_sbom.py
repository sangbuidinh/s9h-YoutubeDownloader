from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_sbom


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify deterministic SPDX 2.3 release SBOM")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--schema", default=release_sbom.SCHEMA_PATH, type=Path)
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--checksum-file", type=Path)
    args = parser.parse_args()
    try:
        evidence = release_sbom.load_input(args.input)
        if args.sbom.name != release_sbom.expected_filename(evidence["release"]["version"]):
            raise release_sbom.SbomError("SBOM filename or identity mismatch")
        if (args.release_manifest is None) != (args.checksum_file is None):
            raise release_sbom.SbomError(
                "release manifest and checksum reconciliation must be available together"
            )
        manifest = None
        checksums = None
        if args.release_manifest is not None:
            manifest = release_sbom._load_strict_json(args.release_manifest.read_bytes())
            checksums = args.checksum_file.read_bytes()
        release_sbom.verify_document(
            args.sbom.read_bytes(),
            evidence,
            schema_path=args.schema,
            final_manifest=manifest,
            final_checksum_bytes=checksums,
        )
        print("Deterministic SPDX 2.3 SBOM verified")
    except (OSError, UnicodeError, release_sbom.SbomError) as exc:
        print(f"Release SBOM verification error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
