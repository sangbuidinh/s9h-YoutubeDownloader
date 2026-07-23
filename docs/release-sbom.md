# Release SBOM

## Phase 7B-R1 Boundary

The repository-owned generator produces deterministic SPDX 2.3 JSON from explicit evidence. It does not scan, infer release identity, or treat historical build inventory as current final-release evidence.

The generator identity is `s9h-project-owned-deterministic-spdx-generator` version `1.0.0`. Genuine JSON Schema validation requires installed distribution metadata for exactly `fastjsonschema` `2.21.2` and uses that locked validator against the vendored official SPDX 2.3 schema pinned to:

- repository `spdx/spdx-spec`;
- tag `v2.3`;
- commit `aadf3b0b8dbbabdb4d880b0fc714255fea436ff7`;
- schema Git blob `ee61e6686e885f8139c132647fd0b4f483b8fb81`.

The corresponding SPDX specification license is preserved byte-for-byte at `schemas/spdx-2.3/LICENSE` from Git blob `44a22d370bba8d13c7dd7449d71b40ea8842788e` under CC-BY-3.0. The `fastjsonschema` BSD license from the exact `2.21.2` wheel is preserved at `scripts/vendor-notices/fastjsonschema-2.21.2-LICENSE.txt`.

## Commands

Run generation and verification only from the repository's locked build/tooling environment created from `requirements-build.txt`. A different or metadata-less `fastjsonschema` installation fails closed.

Generate:

```powershell
python scripts/generate_release_sbom.py --input <evidence.json> --output <Youtube-Downloaderbs-vVERSION.spdx.json>
```

Verify the document:

```powershell
python scripts/verify_release_sbom.py --input <evidence.json> --sbom <Youtube-Downloaderbs-vVERSION.spdx.json>
```

The historical `legal/release-assets-v2.json` contract remains a five-asset compatibility contract with no SBOM input or SBOM claim. The explicit `legal/release-assets-v3.json` contract declares all six required assets, including the `release-sbom` role and filename template. Bundle creation and verification require `--sbom-input` only for v3. The v3 bundle creates the SBOM and reconciles its exact bytes with `RELEASE_MANIFEST.json` and `SHA256SUMS.txt`; v2/v3 contract, manifest, evidence, or layout mismatches fail closed.

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

## Staged Byte Boundary

- A **provisional inventory** records controlled pre-signing or dry-run inputs. It is not final release evidence.
- A **synthetic SBOM** is generated only from explicit non-production fixtures and is not distributable.
- A **production SBOM** uses production evidence, but it is not final while any signing, package assembly, manifest, or checksum byte can still change.
- A **final signed production SBOM** can be generated only after the first-party executable is Authenticode-signed and verified, the portable package is assembled from that signed executable, and the final package, manifest, and checksum bytes exist.

The release-assurance policy sequence remains authoritative: signing and signature verification precede final portable-package assembly, checksums, and final SBOM generation. The next Phase 7B checkpoint is limited to a provisional inventory collector and synthetic or provisional dry-run foundation. It must not call unsigned bytes final immutable release bytes or claim a production SBOM. Final signed production SBOM generation remains deferred until signing and downstream final-byte prerequisites are implemented and separately authorized.
