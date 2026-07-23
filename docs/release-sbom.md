# Release SBOM

## Phase 7B-R1 Boundary

The repository-owned generator produces deterministic SPDX 2.3 JSON from explicit evidence. It does not scan, infer release identity, or treat historical build inventory as current final-release evidence.

The generator identity is `s9h-project-owned-deterministic-spdx-generator` version `1.0.0`. Genuine JSON Schema validation uses `fastjsonschema` `2.21.2` against the vendored official SPDX 2.3 schema pinned to:

- repository `spdx/spdx-spec`;
- tag `v2.3`;
- commit `aadf3b0b8dbbabdb4d880b0fc714255fea436ff7`;
- schema Git blob `ee61e6686e885f8139c132647fd0b4f483b8fb81`.

The corresponding SPDX specification license is preserved byte-for-byte at `schemas/spdx-2.3/LICENSE` from Git blob `44a22d370bba8d13c7dd7449d71b40ea8842788e` under CC-BY-3.0. The `fastjsonschema` BSD license from the exact `2.21.2` wheel is preserved at `scripts/vendor-notices/fastjsonschema-2.21.2-LICENSE.txt`.

## Commands

Generate:

```powershell
python scripts/generate_release_sbom.py --input <evidence.json> --output <Youtube-Downloaderbs-vVERSION.spdx.json>
```

Verify the document:

```powershell
python scripts/verify_release_sbom.py --input <evidence.json> --sbom <Youtube-Downloaderbs-vVERSION.spdx.json>
```

Bundle creation and verification require `--sbom-input`. The bundle creates the SBOM as the `release-sbom` asset and reconciles its exact bytes with `RELEASE_MANIFEST.json` and `SHA256SUMS.txt`.

## Evidence Contract

The canonical JSON input covers release/source/control identity, the standalone executable, all base release assets, extracted portable files, PyInstaller CArchive/source records, Python runtime and packages, native members, external runtimes, legal components, manifest/checksum evidence, and explicit unresolved records.

Authoritative fields are emitted only from supplied evidence. Unknown SPDX fields use `NOASSERTION`, and the output package comment preserves the reason, source, and provenance.

## Non-Claims

The CI fixture is explicitly synthetic and not for distribution. Synthetic success does not mean:

- a production SBOM was generated or validated;
- final application bytes were inventoried or reconciled;
- legal compliance or source availability was certified;
- provenance or SBOM attestations exist;
- Authenticode signing exists;
- release readiness or publishing is allowed.

Phase 7B-R2 is limited to controlled final-build inventory collection, production evidence creation, production SPDX generation/verification, and reconciliation against final immutable bytes without publishing.
