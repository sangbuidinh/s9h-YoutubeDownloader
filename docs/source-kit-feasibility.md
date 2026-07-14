# Phase 6B2B2A Source-Kit Feasibility

## Purpose and fixed outcome

Phase 6B2B2A records an evidence-only inventory of source inputs for the pinned aria2 and FFmpeg binary packages. It evaluates what is known, what is only partially identified, and what evidence is still required. It does not assemble archives, approve source kits, change release policy, or make a legal conclusion.

The fixed outcome is fail-closed: legal compliance certification is false, source assets were not created, source kits remain not ready, assembly is not authorized, and release gate reconsideration is not allowed. The release remains blocked.

This document is not legal advice. Existing releases are not retroactively certified.

## Evidence hierarchy

The inventory applies this evidence hierarchy:

1. **Verified immutable upstream evidence** identifies an official repository and exact immutable content identity, backed by primary-source evidence. An independently established source-archive SHA-256 is retained where it already exists.
2. **Provider evidence** may identify a component name, linkage, build characteristic, or version. It remains partial when it does not identify the official upstream repository and exact immutable ref.
3. **Unresolved evidence** records a missing repository, ref, archive hash, version, toolchain detail, or build instruction explicitly. Unresolved does not mean absent.

Identified does not mean complete. A provider component list establishes what the provider says was enabled or linked, but it does not by itself prove an official upstream repository, exact source ref, complete archive, or build correspondence.

## Core source and full package inputs

The exact core source for aria2 and FFmpeg is backed by a repository, 40-character commit, and previously verified archive SHA-256 in `legal/source-correspondence.json`. Core source is only one input to each static binary package. Full package source inputs also include external static components, applicable system-facing components, exact toolchain details, configuration, build orchestration, and patch or explicit no-modification evidence.

PE imports only enumerate dynamic imports. They cannot enumerate code from static libraries already incorporated into a PE executable, so an empty non-system dynamic-import list cannot remove provider-evidenced static components from the inventory.

## aria2 matrix

| Input class | Count | Evidence state | Remaining gap |
| --- | ---: | --- | --- |
| Core source | 1 | Verified immutable evidence | None for the recorded core identity |
| External static components | 6 | Provider-identified versions; partial | Official repositories, immutable refs, and independent archive hashes |
| External system-facing components | 0 | Not applicable | None |
| Toolchain | 1 record | Partial | Exact compiler and supporting-tool versions |
| Build orchestration | 1 record | Partial | Immutable provider script, exact configuration, patch status, and reproducible entrypoint |

The six external static components are `c-ares`, `expat`, `gmp`, `libssh2`, `sqlite`, and `zlib`. In the generated external-component resolution buckets, aria2 has `0` verified immutable inputs, `6` partially identified inputs, and `0` wholly unresolved records. Those six partial records still contain `18` unresolved upstream identity fields: repository, immutable ref, and archive SHA-256 for each component.

## FFmpeg matrix

| Input class | Count | Evidence state | Remaining gap |
| --- | ---: | --- | --- |
| Core source | 1 | Verified immutable evidence | None for the recorded core identity |
| External static components | 50 | Provider-identified names; partial | Versions, official repositories, immutable refs, and independent archive hashes |
| External system-facing components | 5 | Provider-identified candidates; partial | Exact role and source-input applicability remain unresolved |
| Toolchain | 1 record | Unresolved | Build host, compiler, compiler version, and supporting-tool versions |
| Build orchestration | 1 record | Partial | Immutable provider script, exact configuration, patch status, and reproducible entrypoint |

The five system-facing candidates are `d3d11va`, `d3d12va`, `dxva2`, `mediafoundation`, and `vaapi`. The generated external-component resolution buckets contain `0` verified immutable inputs, `55` partially identified inputs, and `0` wholly unresolved records. Every partial record still has unresolved version or upstream-identity fields; this classification preserves provider evidence without treating a name as complete source correspondence.

## Toolchain gaps

The aria2 provider metadata identifies an Ubuntu Linux mingw-w64 static cross-build, but it does not establish exact compiler and supporting-tool versions. The FFmpeg provider metadata identifies a 64-bit static GPLv3 package, but the build host, compiler, compiler version, and supporting tools remain unresolved.

## Build-orchestration gaps

Neither package has complete build orchestration tied to an immutable provider ref. Exact configuration, patch or explicit no-modification evidence, and a reproducible entrypoint remain unresolved. Provider configuration characteristics are useful partial evidence, not a substitute for those inputs.

## Phase boundary

The source input inventory is evidence-only. No source archive or source kit was created, and no source kit was assembled. Phase 6B2B2A does not authorize assembly or publishing.

Phase 6B2B2B is not authorized until every recorded blocker is resolved with primary-source evidence, including exact external-component identities, exact toolchain versions, complete immutable build orchestration, configuration, patch status, and source-archive manifests. A separate review and explicit authorization are required before any assembly work or release-gate reconsideration.
