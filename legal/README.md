# Legal Materials

## Project licensing status

No project license is selected in Phase 6A. This directory does not grant rights to original project source or assets. Third-party materials retain their own terms, preserved in `legal/licenses/` and summarized in `THIRD_PARTY_NOTICES.md`.

## Inventory scope

`legal/components.json` covers known direct runtime components, known build-time components, and a conservative Tcl/Tk notice. Records that still require further resolution retain `requires-built-artifact-inventory`; versions and copyright years are not inferred without evidence.

## Phase 6B1 controlled build inventory

`legal/built-artifact-inventory.json` records a controlled Windows x64 PyInstaller build from one exact source commit. This snapshot does not prove future binaries are identical. OpenSSL, SQLite, zlib, libffi, Tcl/Tk subcomponents, Microsoft runtime components, and every observed native archive member are represented conservatively; unresolved members remain explicit. No executable or extracted binary is committed.

## Source and binary correspondence

The current stable release sources identify these versions and builds:

- yt-dlp 2026.03.17;
- Gyan FFmpeg 8.1.2 essentials build;
- aria2 1.37.0;
- Deno 2.7.14;
- Python 3.11.9;
- PyInstaller 6.21.0.

Version-specific build scripts remain the source of truth for the binary versions they package. The legacy v1.2.7 workflow may have different runtime-acquisition semantics. Phase 6A does not certify every historical workflow's binary and license correspondence.

## FFmpeg-specific warning

Gyan identifies its static builds as GPLv3. FFmpeg license configuration varies with enabled components. Source-distribution obligations are not resolved merely by copying the GPL text. A conclusion about redistribution requires review of the exact Gyan archive, configuration, incorporated libraries, and Corresponding Source.

## Release gate

`legal/release-policy.json` intentionally blocks all four current version workflows. The gate executes before dependency installation, release runtime acquisition, and application build. Existing releases are not retroactively certified.

## Source availability status

FFmpeg/ffprobe Corresponding Source remains unverified. aria2 source-distribution integration remains incomplete. Neither a copied license file nor an upstream link is represented as sufficient. Phase 6B2 must provide verified source kits and equivalent access alongside the applicable releases.

## Phase 6B2 requirements

Phase 6B2 must complete all of the following before the release gate can be reconsidered:

- resolve or preserve explicit uncertainty for embedded Tcl/Tk, OpenSSL, SQLite, zlib, libffi, and other collected components;
- inject notices into the portable ZIP;
- define companion notice assets for the standalone EXE;
- provide verified source kits and implement source availability or source-offer handling;
- update the release bundle schema;
- update the release gate only after its prerequisites are verified.

## Phase 6B2A source correspondence audit

The exact pinned FFmpeg and aria2 binary packages were audited without executing them. Exact core source refs were resolved where the package evidence permitted, but core source is not represented as the full source required for the distributed static packages. External static-component sources and build-recipe gaps remain explicit. The result is **source correspondence partially identified**, **source kit not ready**, and **no compliance certification**.

`legal/source-correspondence.json` records the audit findings. `legal/source-kit-requirements.json` defines the prerequisites for Phase 6B2B. The audit confirms that all four workflows remain fail-closed; Phase 6B2A does not enable publishing, source kits and legal payloads remain absent, and existing releases are not retroactively certified.

## Phase 6B2B1 release legal payload

The contract in `legal/release-assets-v2.json` defines `s9h-release-legal-payload-v1` and `s9h-release-bundle-v2`. It states that portable packages will include verified legal materials, and standalone EXE releases require a companion legal ZIP. It also states that legal materials do not replace source-distribution obligations or select a project license.

The aria2 and FFmpeg source asset names are defined but assets are not ready. Release bundle v2 requires both source assets, the current release policy remains fail-closed, publishing remains disabled, and existing releases are not retroactively certified. Phase 6B2B2 remains required.

## Phase 6B2B2A source-kit feasibility inventory

The immutable-input feasibility inventory in `legal/source-input-inventory.json` and its generated assessment in `legal/source-kit-feasibility.json` preserve both verified evidence and unresolved evidence. The source input inventory is evidence-only: unresolved does not mean absent, and identified does not mean complete.

No source archive or source kit was created, and no source kit was assembled. Source kits remain not ready, the release remains blocked, there is no release gate reconsideration, and there is no legal certification. Existing releases are not retroactively certified. Phase 6B2B2B is not authorized until blockers are resolved.

## Phase 6B2B2A1a aria2 primary-source resolution

`legal/primary-source-evidence-aria2.json` records official primary-source research for the six provider-identified aria2 static components. The exact result is **5 verified, 1 partial, 0 unresolved**: c-ares, Expat, libssh2, SQLite, and zlib have verified immutable inputs; GNU MP remains partial because its exact official Mercurial changeset was not established and the protected inventory accepts only a 40-character Git commit.

The official source archives for all six components were downloaded temporarily outside the repository and independently hashed. They were not committed and were not assembled into a source kit. Provider identification is not the same as immutable upstream resolution, and upstream resolution does not prove exact provider build reproduction. Archive hashing does not prove that the binary incorporated unmodified source.

No source kit was assembled. Toolchain and build orchestration remain incomplete, assembly remains unauthorized, publishing remains blocked, and existing releases are not retroactively certified. This is not legal advice.

## Phase 6B2B2A1b FFmpeg codec primary-source research

`legal/primary-source-evidence-ffmpeg-codecs.json` records contextual official-upstream research for 16 FFmpeg codec-library names identified with static linkage by the exact Gyan package metadata. Exact provider dependency versions remain unresolved, official upstream project identification does not prove provider use, and no component was promoted.

The exact result is **0 provider versions verified, 0 verified immutable inputs, 16 identified-name-only inputs, and 0 provider source archive hashes verified**. Feasibility counts remain unchanged. No source archive hash was accepted as a provider input, no source archive was committed, and no source kit was assembled.

The aria2 verifier owns aria2 semantics and shared gates; the FFmpeg batch verifier owns this FFmpeg evidence batch. Neither weakens the general source-kit feasibility gate. The exact historical recipe, toolchain, configure command, and patch set remain unresolved. Assembly remains unauthorized, publishing remains blocked, existing releases are not retroactively certified, and this is not legal advice.

## Phase 6B2B2A1c FFmpeg support primary-source research

`legal/primary-source-evidence-ffmpeg-support.json` records contextual official-upstream research for 14 FFmpeg graphics, subtitle, text and audio-support libraries identified with static linkage by the exact Gyan package metadata. Exact provider dependency versions remain unresolved, upstream project identification does not prove provider use, and no component was promoted.

The exact result is **0 provider versions verified, 0 verified immutable inputs, 14 identified-name-only inputs, and 0 provider source archive hashes verified**. Feasibility counts remain unchanged. No provider source archive hash was accepted, no source archive was committed, and no source kit was assembled.

The aria2 verifier owns aria2 data and shared gates, the codec verifier owns the prior 16 codec records, the support verifier owns the new 14 support records, and the general feasibility verifier owns overall schema and readiness. No verifier permanently freezes unrelated future evidence. The exact historical recipe, toolchain, configure command, and patch set remain unresolved. Assembly remains unauthorized, publishing remains blocked, existing releases are not retroactively certified, and this is not legal advice.
