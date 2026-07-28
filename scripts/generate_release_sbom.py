from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_sbom


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic SPDX 2.3 release SBOM")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--schema", default=release_sbom.SCHEMA_PATH, type=Path)
    args = parser.parse_args()
    try:
        evidence = release_sbom.load_input(args.input)
        expected = release_sbom.expected_filename(evidence["release"]["version"])
        if args.output.name != expected:
            raise release_sbom.SbomError("SBOM output filename is invalid")
        if args.output.exists():
            raise release_sbom.SbomError("SBOM output already exists")
        output = release_sbom.generate_bytes(evidence, schema_path=args.schema)
        release_sbom.verify_document(output, evidence, schema_path=args.schema)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(output)
        print(f"Deterministic SPDX 2.3 SBOM generated: {expected}")
    except (OSError, UnicodeError, release_sbom.SbomError) as exc:
        print(f"Release SBOM generation error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
