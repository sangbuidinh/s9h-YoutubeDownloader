# Third-Party Notices

## Scope and limitations

This file covers known direct third-party components that current repository sources identify as build inputs or distributed runtime tools. It is not a project license and does not license original project code or assets. It is not legal advice or a certification of complete binary-level legal compliance. A controlled build snapshot exists, but unresolved embedded components remain pending Phase 6B2.

## Distributed runtime tools

### yt-dlp nightly 2026.08.18.122307

- Role: external executable in the portable ZIP.
- Distributed filename: `data/bin/yt-dlp.exe`.
- Binary distribution: `yt-dlp/yt-dlp-nightly-builds` release tag `2026.08.18.122307`, asset `yt-dlp.exe`, SHA-256 `652e154bce7170070d0f26415c9a3c35c121f5a7903cb8cde6d31c4577517fb9`.
- Upstream source and license: `yt-dlp/yt-dlp` at commit `5d5b634d8e6b41dc2891847a5ea7a5a3f569a28c`.
- License label: The Unlicense / public-domain dedication.
- Local license: `legal/licenses/yt-dlp-2026.08.18.122307-UNLICENSE.txt`.
- Source-distribution status: not assessed in Phase 6A.
- Inclusion does not imply endorsement by or affiliation with yt-dlp.

### FFmpeg and ffprobe 8.1.2

- Role: external executables from the project-controlled FFmpeg 8.1.2 Windows x64 build in the portable ZIP.
- Distributed filenames: `data/bin/ffmpeg.exe` and `data/bin/ffprobe.exe`.
- Upstream: `FFmpeg/FFmpeg` at commit `38b88335f99e76ed89ff3c93f877fdefce736c13` (8.1.2).
- License label: LGPL-2.1-or-later for this configuration; GPL, version3, and nonfree are disabled. Native defaults are retained; external autodetection is disabled. libmp3lame supplies MP3 encoding; Windows SChannel supplies TLS.
- Local licenses: `legal/licenses/FFmpeg-8.1.2-LGPLv2.1.txt` and `legal/licenses/FFmpeg-8.1.2-LICENSE.md`.
- Active source and binary mapping: `legal/source-compliance-v1.3.2.json`. Source ZIP: `Youtube-Downloaderbs-v1.3.2-ffmpeg-source.zip`, supplied alongside binaries subject to separate release authorization. A technical mapping is not legal certification.
- This software is based in part on the work of the Independent JPEG Group. Incorporated IJG-derived source files are unmodified.
- Gyan is retired from the active v1.3.2 target. Its GPLv3 text and INCOMPLETE correspondence record remain historical evidence, not evidence for this build.
- Inclusion does not imply endorsement by or affiliation with FFmpeg.

### LAME 3.100

libmp3lame is linked statically into the project runtime for the existing MP3 quality-zero contract. LAME is acknowledged at https://lame.sourceforge.io/ . Exact source release `lame-3.100.tar.gz` is pinned and included in the FFmpeg source ZIP. LAME code is LGPL-2.0-or-later; the unchanged upstream texts are `legal/licenses/LAME-3.100-COPYING.txt` and `legal/licenses/LAME-3.100-LICENSE.txt`.

### MinGW-w64 runtime 14.0.0

The checksum-pinned w64devkit 2.9.1 compiler supplies MinGW-w64 14.0.0 support. Its exact aggregate runtime notice, including permissive and Wine-header notices, is preserved at `legal/licenses/MinGW-w64-14.0.0-COPYING.txt`. Windows system DLLs are not bundled. Compiler tools are not represented as FFmpeg media dependencies or as a legal certification.

### aria2 1.37.0

- Role: external executable in the portable ZIP.
- Distributed filename: `data/bin/aria2c.exe`.
- Upstream: `aria2/aria2` at ref `release-1.37.0`.
- License label: GNU General Public License, Version 2, as present in the upstream COPYING file.
- Local license: `legal/licenses/aria2-1.37.0-GPLv2.txt`.
- Source-distribution status: not certified. Distributor source-offer handling has not been certified.
- Inclusion does not imply endorsement by or affiliation with aria2.

### Deno 2.7.14

- Role: external executable in the portable ZIP.
- Distributed filename: `data/bin/deno.exe`.
- Upstream: `denoland/deno` at ref `v2.7.14`.
- License label: MIT.
- Local license: `legal/licenses/Deno-2.7.14-MIT.txt`.
- Source-distribution status: not assessed in Phase 6A.
- Inclusion does not imply endorsement by or affiliation with Deno.

## Packaged application runtime

### Python 3.11.9

Python 3.11.9 is the runtime identified for embedding in the PyInstaller-built standalone executable. Its complete upstream license file, including the Python Software Foundation License Version 2 and incorporated notices, is stored at `legal/licenses/Python-3.11.9-LICENSE.txt`. The upstream source is `python/cpython` at ref `v3.11.9`. Executable-level dependency inventory remains required.

### PyInstaller 6.21.0

PyInstaller 6.21.0 is the build tool whose bootloader and related files are embedded in the standalone executable. Its upstream `COPYING.txt` is stored at `legal/licenses/PyInstaller-6.21.0-COPYING.txt`; the Apache License 2.0 text referenced for runtime hooks is stored at `legal/licenses/Apache-2.0.txt`.

PyInstaller is GPL-2.0-or-later. Its bootloader exception permits distribution of the compiled bootloader embedded with another program without imposing GPL restrictions solely from that embedding. Runtime hooks are described by PyInstaller as Apache-2.0 licensed. The bootloader exception does not license this project's original source.

### Tcl/Tk conservative notice

The application imports Tkinter, and the controlled Phase 6B1 build contains Tcl/Tk runtime material. Upstream Tcl terms from `tcltk/tcl` ref `core-8-6-13` are preserved at `legal/licenses/Tcl-Tk-license.terms` as a conservative notice. The exact Tcl/Tk patch version was not verified, and the snapshot does not assert what every future build contains.

## GPL source-availability warning

FFmpeg/ffprobe and aria2 are distributed as separate executables. Separation does not eliminate each executable's own redistribution obligations. Attribution or a copied license text alone does not resolve Corresponding Source requirements. Current technical readiness requires verified exact source assets and packaged notices; it does not certify legal compliance or authorize distribution. Equivalent source access and the final release decision remain subject to external review and explicit legal authorization.

## Phase 6B1 controlled build inventory

`legal/built-artifact-inventory.json` records one controlled Windows x64 PyInstaller build. It is not a prediction of future binaries, retains unresolved native members, and does not include the executable itself.

## Release gate

`legal/release-policy.json` keeps historical workflows fail-closed and validates the current technical inputs before runtime acquisition or application build. Final publication additionally requires explicit legal authorization, which remains absent. Existing releases are not retroactively certified.

## Source availability status

Current technical state is owned by `legal/source-compliance-v1.3.2.json` and `legal/release-policy.json`. The project-controlled FFmpeg source kit and accepted aria2 source kit require byte-level validation and equivalent release access. A copied license or upstream link is not represented as sufficient. Production authorization remains `LEGAL_REVIEW_REQUIRED`; publishing remains disabled without that separate decision.

The phase-specific sections below preserve historical observations. Their Gyan and earlier source-kit blockers are not claims about the replacement runtime.

## Phase 6B2A source correspondence audit

The exact pinned FFmpeg and aria2 binary packages were audited without executing them. Exact core source refs were resolved where the package evidence permitted, but core source is not represented as the full source required for the distributed static packages. External static-component sources and build-recipe gaps remain explicit. The result is **source correspondence partially identified**, **source kit not ready**, and **no compliance certification**.

`legal/source-correspondence.json` records the audit findings. `legal/source-kit-requirements.json` defines the prerequisites for Phase 6B2B. The audit confirms that all five workflows remain fail-closed; Phase 6B2A does not enable publishing, source kits and legal payloads remain absent, and existing releases are not retroactively certified.

## Phase 6B2B1 release legal payload

The release contract states that portable packages will include verified legal materials, and standalone EXE releases require a companion legal ZIP. It also states that legal materials do not replace source-distribution obligations. The aria2 and FFmpeg source asset names are defined but assets are not ready, and release bundle v2 requires both source assets.

The current release policy remains fail-closed, publishing remains disabled, existing releases are not retroactively certified, and Phase 6B2B2 remains required. This integration does not select a project license or certify source availability.

## Trademarks and affiliation

Third-party names and marks belong to their respective owners. Inclusion of a component does not imply endorsement or affiliation with its upstream project or distributor. YouTube and Google names are not project trademarks, and this project does not claim affiliation with YouTube, Google, or the upstream projects listed here.

## License files

- `legal/licenses/Apache-2.0.txt`
- `legal/licenses/aria2-1.37.0-GPLv2.txt`
- `legal/licenses/Deno-2.7.14-MIT.txt`
- `legal/licenses/FFmpeg-8.1.2-GPLv3.txt`
- `legal/licenses/FFmpeg-8.1.2-LGPLv2.1.txt`
- `legal/licenses/FFmpeg-8.1.2-LICENSE.md`
- `legal/licenses/LAME-3.100-COPYING.txt`
- `legal/licenses/LAME-3.100-LICENSE.txt`
- `legal/licenses/MinGW-w64-14.0.0-COPYING.txt`
- `legal/licenses/PyInstaller-6.21.0-COPYING.txt`
- `legal/licenses/Python-3.11.9-LICENSE.txt`
- `legal/licenses/Tcl-Tk-license.terms`
- `legal/licenses/yt-dlp-2026.08.18.122307-UNLICENSE.txt`
