# SBOM Generator Feasibility

## Scope And Safety Boundary

This document retains the Phase 7A2 feasibility record and records the Phase 7B-R1 transition to an implemented repository-owned generator. Phase 7B-R1 adds deterministic SPDX 2.3 generation, genuine schema validation, semantic reconciliation, and controlled synthetic release-bundle integration. It does not download or execute a scanner, build the application, generate a production SBOM from final application bytes, create an attestation, assemble a source kit, release, or publish.

The assessment baseline is `887219aaf37ac5470a6b1f3f4393f1fd85350c4e`. The target is canonical SPDX 2.3 JSON named `Youtube-Downloaderbs-v{version}.spdx.json`, with the document predicate `https://spdx.dev/Document/v2.3`. The data license is `CC0-1.0`.

The repository-owned generator is selected at version `1.0.0`, and its implementation plus synthetic integration are validated. Production generation, production validation, attestation, release readiness, and publishing claims remain false. The existing release-assurance policy remains authoritative and fail-closed.

## Current SBOM Input Boundary

The generator now requires a canonical explicit evidence document covering the complete input boundary. The current repository still does not have a final-build evidence document:

- The package script uses PyInstaller one-file output and assembles a portable release ZIP.
- Distributed external runtimes include yt-dlp, FFmpeg, ffprobe, Deno, and aria2.
- `legal/components.json` and the legal license corpus provide authoritative legal-component evidence for their controlled scope.
- Release assets v2 require an SBOM evidence input and include a generated SBOM in the controlled synthetic bundle, but no production SBOM is currently integrated or reconciled.
- The build dependency inputs are `requirements-build.txt` and its controlled bootstrap process; no `requirements-build.lock` is present at this baseline.
- The historical executable inventory is not current final-release evidence.

The historical executable inventory records source commit `988c07f9d3e099b3ff157e33d880c0bad73ad112`. It can inform prototype design, but it cannot stand in for a new final EXE, final portable-package extraction, final Python package inventory, or final native-member inventory.

The implemented input contract receives authoritative release identity, source and control commits, final artifact inventory, portable-package extracted file inventory, PyInstaller executable/CArchive inventory, Python package inventory, native member inventory, external runtime inventory, legal-component evidence, release manifest, checksum file, final executable identity, and unresolved-component records. Every item retains its canonical relative path, size, SHA-256, component association, authoritative field provenance, and explicit unresolved-state reason.

## Official Source Method

Research was read-only and restricted to official project repositories, immutable commits, tagged releases, pinned license blobs, and their published release metadata. No candidate binary or release artifact was downloaded or executed. Evidence was retrieved at `2026-07-18T01:14:55Z`.

The SPDX 2.3 target was checked against `spdx/spdx-spec` tag `v2.3`, commit `aadf3b0b8dbbabdb4d880b0fc714255fea436ff7`, including schema blob `ee61e6686e885f8139c132647fd0b4f483b8fb81`. The specification supports SHA-256 checksums, `NOASSERTION`, packages, files, and `DESCRIBES` or containment relationships. The SBOM document data license is `CC0-1.0`.

The external candidates are pinned as follows:

| Candidate | Release | Immutable commit | License evidence | Execution state |
| --- | --- | --- | --- | --- |
| Anchore Syft | `v1.48.0` | `3e2bc6ed095f7ec1a415fb38cfe1c319e95dfed6` | `LICENSE`, blob `261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64`, Apache-2.0 | not downloaded, not executed |
| Microsoft sbom-tool | `v4.1.5` | `c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3` | `LICENSE`, blob `9e841e7a26e4eb057b24511e7b92d42b257a80e5`, MIT | not downloaded, not executed |

Immutable official references:

- SPDX schema: <https://github.com/spdx/spdx-spec/blob/aadf3b0b8dbbabdb4d880b0fc714255fea436ff7/schemas/spdx-schema.json>
- Syft source and documentation: <https://github.com/anchore/syft/tree/3e2bc6ed095f7ec1a415fb38cfe1c319e95dfed6>
- Syft pinned release: <https://github.com/anchore/syft/releases/tag/v1.48.0>
- sbom-tool source and documentation: <https://github.com/microsoft/sbom-tool/tree/c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3>
- sbom-tool pinned release: <https://github.com/microsoft/sbom-tool/releases/tag/v4.1.5>

Syft's pinned sources establish SPDX 2.3 JSON output, Windows release archives, filesystem and archive scanning, Python ecosystem cataloging, PE/native cataloging, and release checksums/provenance. Its optional remote license lookup would have to be disabled for a strictly offline experiment.

sbom-tool's pinned sources establish Windows command-line generation, file hashing, Component Detection integration, relationships, and SPDX 2.2 or SPDX 3.0 output. They do not establish the required SPDX 2.3 output. Optional ClearlyDefined enrichment would also need to be disabled for an offline experiment.

## Candidate Comparison

The machine-readable record evaluates exactly 30 criteria. This summary highlights the decision-driving differences; the JSON contains the status, result, limitation, and decision impact for every candidate and criterion.

| Area | Project-owned deterministic generator | Anchore Syft | Microsoft sbom-tool |
| --- | --- | --- | --- |
| SPDX 2.3 JSON | Contract explicitly targets it | Directly supported by pinned source | Pinned release documents 2.2 and 3.0, not 2.3 |
| Deterministic bytes and ordering | Fully controllable by contract | Not established by reviewed sources | Not established by reviewed sources |
| Final package reconciliation | Can be made mandatory and fail-closed | Requires a repository wrapper | Requires a repository wrapper |
| PyInstaller one-file coverage | Contract can require CArchive evidence | Complete interpretation not established | Complete interpretation not established |
| Python and native discovery | Must consume authoritative inventories | Strong discovery candidate, completeness still unproved | Discovery available, exact coverage still unproved |
| External runtime identity | Must reconcile all five runtimes | Discovery alone is not authoritative identity | Discovery alone is not authoritative identity |
| Offline operation | Fully controllable | Local scan feasible with remote lookup disabled | Local scan feasible with enrichment disabled |
| Independent comparison | Not independent | Best target-format comparator | Weaker due to target-format mismatch |
| New privileged dependency | None in primary path | Isolated comparator binary only | Separate binary and behavior required |

Neither external candidate alone proves complete coverage of a PyInstaller one-file executable plus extracted portable ZIP, embedded Python runtime, distributed Python packages, native DLL/PYD members, and five external runtimes. Discovery output must never be treated as authoritative final-package reconciliation without exact path, hash, component, and release-manifest cross-checks.

## Architecture Decision

`project-owned-deterministic-spdx-generator` is the selected implementation at version `1.0.0`. This selection establishes the generator foundation and does not claim that a production SBOM has been generated from final release bytes.

Repository ownership is preferred because the generator must enforce exact canonical JSON, deterministic document namespaces and ordering, explicit final-file SHA-256 coverage, no fabricated supplier/version/license/download location, and fail-closed reconciliation against the final manifest and checksum file. A small standard-library implementation can consume controlled evidence rather than infer release identity from a scanner alone.

Select Anchore Syft as the one external comparator for a later separately authorized experiment. It is independent, directly supports SPDX 2.3 JSON, has relevant Windows/filesystem/archive/Python/native discovery, and publishes pinned release checksums and provenance. It has not been downloaded or executed here.

Keep Microsoft sbom-tool as the secondary, unselected candidate. It remains technically relevant, but its pinned release's documented SPDX 2.2 and SPDX 3.0 formats do not match the exact SPDX 2.3 target. It was not rejected as unusable; it was not selected for the first comparator experiment because the format mismatch weakens direct parity.

## Prototype Contract

The project-owned implementation consumes only explicit evidence classes and emits canonical UTF-8 SPDX 2.3 JSON with LF-only line endings, no BOM, and exactly one final newline. Package, file, and relationship order is stable. Document namespace construction is deterministic. Every represented final file has a SHA-256 checksum and explicit association with the final release artifact.

The model must include explicit `DESCRIBES` relationships and package/file containment relationships. Supplier, version, declared license, concluded license, download location, and package URL may be emitted only from authoritative evidence. Where SPDX permits an unknown value and the input is unresolved, the generator must use `NOASSERTION` and preserve the reason and field provenance. It must never invent a license conclusion, supplier, version, URL, or origin.

The PyInstaller one-file boundary requires both executable-level identity and inventory evidence for embedded CArchive members. The portable ZIP boundary requires a separately extracted final-file inventory. Python runtime, distributed Python packages, native DLL/PYD/EXE members, yt-dlp, FFmpeg, ffprobe, Deno, aria2, legal-component records, manifest records, and checksum records must reconcile without duplicate or unsafe paths.

The prototype must fail closed when the final inventory or checksum is absent, hashes differ, paths are duplicate or unsafe, the release manifest differs, distributed files are unaccounted for, an external runtime or Python runtime is omitted, the PyInstaller source inventory mismatches, commit or tag identity is malformed, relationships are incomplete, output is nondeterministic, or offline schema/semantic validation is unavailable.

Generator implementation, SPDX schema validation, semantic reconciliation, and synthetic release-bundle integration are complete for this checkpoint. Comparator execution, final-build evidence collection, production SBOM generation and reconciliation, attestation, release, and publishing remain separately unauthorized.

## Risks And Later Work

Key unresolved risks are final-build PyInstaller extraction, Python package ownership, native-member attribution, external runtime version/origin evidence, absent final package inventory, and maintenance drift in external candidate pins. Schema and semantic validation are implemented, but they have not yet processed a real final application build.

Phase 7B-R2 should collect controlled inventories from one final build, create a production evidence document, generate and verify the production SPDX document, and reconcile it against final immutable manifest/checksum bytes without publishing. A separate comparator experiment could later obtain exactly the pinned Syft artifact through an approved provenance process. Comparator execution is not authorized by this checkpoint.

## Explicit Non-Claims

- No complete SBOM exists.
- No production SBOM was generated or validated.
- The repository-owned generator is selected and pinned at version `1.0.0`.
- No scanner was approved for download.
- No external comparator was downloaded or executed.
- A controlled synthetic release bundle and checksum file were reconciled with a synthetic SBOM; no production release bundle was reconciled.
- No legal compliance or source availability was certified.
- No release, attestation, source kit, or publishing action was authorized.
- Existing Authenticode, SBOM, provenance, legal, source-kit, release, and publishing gates remain fail-closed.
