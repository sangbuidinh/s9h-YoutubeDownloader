# Legal Materials

## Project licensing status

No project license is selected in Phase 6A. This directory does not grant rights to original project source or assets. Third-party materials retain their own terms, preserved in `legal/licenses/` and summarized in `THIRD_PARTY_NOTICES.md`.

## Inventory scope

The machine-readable inventory covers known direct runtime components, known build-time components, and a conservative Tcl/Tk notice. Phase 6A does not build the standalone executable, so it does not claim an exhaustive binary-level dependency inventory.

Embedded or collected components that still require built-artifact inventory include OpenSSL, SQLite, zlib, libffi, Tcl/Tk subcomponents, and Microsoft runtime components where redistributable terms apply. Their status is `requires-built-artifact-inventory`; no versions or copyright years are inferred here.

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

## Phase 6B requirements

Phase 6B must complete all of the following before release notices are declared complete:

- produce a controlled real build;
- inspect an executable-level component inventory;
- identify exact embedded Tcl/Tk, OpenSSL, SQLite, zlib, libffi, and other collected components;
- inject notices into the portable ZIP;
- define companion notice assets for the standalone EXE;
- decide and implement source availability or source-offer handling;
- update the release bundle schema;
- add final release workflow gates.
