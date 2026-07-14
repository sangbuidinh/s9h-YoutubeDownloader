# Third-Party Notices

## Scope and limitations

This file covers known direct third-party components that current repository sources identify as build inputs or distributed runtime tools. It is not a project license and does not license original project code or assets. It is not legal advice or a certification of complete binary-level legal compliance. Exact embedded runtime inventory remains pending Phase 6B.

## Distributed runtime tools

### yt-dlp 2026.03.17

- Role: external executable in the portable ZIP.
- Distributed filename: `data/bin/yt-dlp.exe`.
- Upstream: `yt-dlp/yt-dlp` at ref `2026.03.17`.
- License label: The Unlicense / public-domain dedication.
- Local license: `legal/licenses/yt-dlp-2026.03.17-UNLICENSE.txt`.
- Source-distribution status: not assessed in Phase 6A.
- Inclusion does not imply endorsement by or affiliation with yt-dlp.

### FFmpeg and ffprobe 8.1.2

- Role: external executables from the Gyan FFmpeg 8.1.2 essentials static Windows build in the portable ZIP.
- Distributed filenames: `data/bin/ffmpeg.exe` and `data/bin/ffprobe.exe`.
- Binary package: `ffmpeg-8.1.2-essentials_build.zip` from Gyan.
- Upstream: `FFmpeg/FFmpeg` at ref `n8.1.2`.
- License label: GPLv3 static build, as identified by the Gyan build distributor.
- Local license: `legal/licenses/FFmpeg-8.1.2-GPLv3.txt`.
- Source-distribution status: not certified. Complete Corresponding Source for the distributed static binaries and incorporated libraries has not been certified.
- Inclusion does not imply endorsement by or affiliation with FFmpeg or Gyan.

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

The application imports Tkinter, and a packaged Windows executable may contain Tcl/Tk runtime material. Upstream Tcl terms from `tcltk/tcl` ref `core-8-6-13` are preserved at `legal/licenses/Tcl-Tk-license.terms` as a conservative notice. Phase 6A does not assert that this ref is the exact Tcl/Tk version embedded by every build workflow. Exact executable contents require built-artifact inspection.

## GPL source-availability warning

FFmpeg/ffprobe and aria2 are distributed as separate executables. Separation does not eliminate each executable's own redistribution obligations. Attribution or a copied license text alone does not resolve source-distribution requirements. Exact Corresponding Source and source-offer handling have not been certified in Phase 6A, and release integration must not be described as complete until Phase 6B closes these items.

## Trademarks and affiliation

Third-party names and marks belong to their respective owners. Inclusion of a component does not imply endorsement or affiliation with its upstream project or distributor. YouTube and Google names are not project trademarks, and this project does not claim affiliation with YouTube, Google, or the upstream projects listed here.

## License files

- `legal/licenses/Apache-2.0.txt`
- `legal/licenses/aria2-1.37.0-GPLv2.txt`
- `legal/licenses/Deno-2.7.14-MIT.txt`
- `legal/licenses/FFmpeg-8.1.2-GPLv3.txt`
- `legal/licenses/PyInstaller-6.21.0-COPYING.txt`
- `legal/licenses/Python-3.11.9-LICENSE.txt`
- `legal/licenses/Tcl-Tk-license.terms`
- `legal/licenses/yt-dlp-2026.03.17-UNLICENSE.txt`
