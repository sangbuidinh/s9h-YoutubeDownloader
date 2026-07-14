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
