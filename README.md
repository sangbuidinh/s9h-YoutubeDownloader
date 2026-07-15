# Youtube Downloaderbs

Youtube Downloaderbs is a Windows desktop application for selecting and batch-downloading videos from YouTube channels. It is distributed as a portable application and produces Premiere-oriented MP4 H.264/AAC media up to 1080p, with optional MP3 audio and thumbnail output.

Current stable version: `v1.3.1`

The application uses the YouTube Data API to list channel uploads and yt-dlp plus external runtime tools to download, process, and validate selected media.

## Features

- Fetches channel information and recent uploads through the YouTube Data API.
- Loads up to 100 visible videos per page and supports loading additional pages.
- Filters videos by title, downloaded state, and configurable minimum or maximum duration.
- Supports individual selection, visible-row selection, and date-based selection.
- Requires a positive File start number and creates numbered output filenames.
- Provides the default Stable yt-dlp transfer engine and an optional Fast aria2c engine.
- Downloads video, MP3 audio, and thumbnails through three explicit modes.
- Supports a user-selected cookies file and a Local Cookie Bridge file source.
- Stores video-scoped download state in a local SQLite database.
- Reconciles partial output state so missing video, audio, or thumbnail parts can be completed.
- Validates media output before marking a part complete.
- Promotes completed files atomically from hidden staging directories.
- Shows download progress and supports bounded Stop/Cancel process cleanup.
- Uses a Premiere-safe MP4 policy: H.264/AVC video, AAC audio, and a maximum height of 1080p.

## Download Options

Download current assets from the [latest GitHub release](https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest).

### Portable ZIP

New installations should use:

`Youtube-Downloaderbs-v1.3.1.zip`

The portable package contains this exact runtime layout:

```text
Youtube.Downloaderbs.exe
data/bin/yt-dlp.exe
data/bin/ffmpeg.exe
data/bin/ffprobe.exe
data/bin/aria2c.exe
data/bin/deno.exe
```

Extract the complete ZIP before running the application. Keep the `data/bin` directory beside the application executable.

### Standalone EXE

The release also provides:

`Youtube.Downloaderbs.exe`

This standalone asset is intended to replace the application EXE in an existing portable installation. It does not contain yt-dlp, FFmpeg, ffprobe, aria2c, or Deno by itself. New users should download the portable ZIP instead.

## Requirements

- Windows x64.
- A network connection.
- A YouTube Data API key for channel listing.
- Sufficient disk space for media files and temporary staging data.
- Optional user-owned cookies for restricted videos, expired sessions, or YouTube bot-check scenarios.

Administrator privileges are not required by the application design. No minimum Windows version is currently documented.

## First-Run Guide

1. Download and extract `Youtube-Downloaderbs-v1.3.1.zip`.
2. Run `Youtube.Downloaderbs.exe`.
3. Enter a YouTube Data API key.
4. Enter a channel URL, handle, username, or channel ID.
5. Select **Lấy danh sách Video** to fetch channel uploads.
6. Choose a save folder.
7. Choose a download mode and download engine.
8. Enter a **File start number**.
9. Select the videos to process.
10. Select **Tải** to start the batch.

The File start number must be a positive whole number. Output numbers use at least three digits, such as `001`, `051`, or `1000`.

The visible number advances after each newly completed logical video. Failed, cancelled, skipped, incomplete, and previously completed videos do not advance that visible suggestion. The active batch keeps its original number allocation.

File start number state is session-only. Closing and reopening the application resets the field to blank. You can manually override the value before starting a new run; the application does not scan filenames or maintain persistent numbering continuation.

## Download Modes

The application exposes these exact modes:

### Video + Thumb

- MP4 video in the channel `video` directory.
- JPG thumbnail in the channel `thumb` directory.

### Audio MP3 + Thumb

- MP3 audio in the channel `audio` directory.
- JPG thumbnail in the channel `thumb` directory.
- A compatible MP4 may be downloaded temporarily when required for MP3 extraction.

### Video + Audio MP3 + Thumb

- MP4 video in the channel `video` directory.
- MP3 audio in the channel `audio` directory.
- JPG thumbnail in the channel `thumb` directory.

Thumbnail downloads try the URL returned by the YouTube Data API first and use yt-dlp as a fallback.

## Download Engines

### Stable - yt-dlp internal

Stable is the default, compatibility-first engine. yt-dlp handles media transfer internally with one fragment connection. Format selection, cookie isolation, retries, merge/remux, validation, and atomic promotion use the same policy as Fast.

### Fast - aria2c experimental

Fast supplies aria2c to yt-dlp for media transfer with this current profile:

```text
-x 16 -s 16 -j 16 -k 1M
```

Actual speed depends on the video, YouTube CDN route, network, and public IP behavior. Fast is not guaranteed to outperform Stable.

Fast changes the media-transfer process only. It retains the same Premiere-safe format selector, cookies workflow, fallback handling, FFmpeg merge/remux, ffprobe validation, atomic promotion, and sequential per-video order as Stable. It does not perform unrestricted video transcoding.

The percentage reported from aria2c represents transfer progress, not completion of the full logical pipeline. Merge, validation, promotion, MP3 extraction, thumbnail work, and state updates may continue after transfer reaches 100%.

If Fast is selected but `aria2c.exe` is missing or cannot start, the batch is blocked with a tool error. Select Stable for a later batch or repair the portable runtime installation.

## Output Compatibility

Video output follows a strict Premiere-safe policy:

- MP4 container.
- H.264/AVC video (`avc1`).
- AAC audio in an M4A-compatible source (`mp4a`).
- Maximum video height of 1080p.
- FFmpeg merge/remux and MP3 extraction.
- ffprobe stream, codec, dimensions, and duration inspection.

`ffprobe.exe` inspects and validates local media; it does not download media.

Not every YouTube video exposes a compatible MP4 H.264/AAC source at 1080p or below. When no compatible source exists, the application reports that no Premiere-safe format is available instead of silently selecting an unrestricted codec or resolution.

## Output Folders and State

For a selected save folder, output is organized by sanitized channel name:

```text
<save folder>/
  <channel name>/
    video/NNN Title.mp4
    audio/NNN Title.mp3
    thumb/NNN Title.jpg
```

Only directories required by the selected mode are populated. Temporary media work occurs in hidden `.s9h-stage-*` directories under the channel directory and is cleaned after the attempt.

Video-scoped state is stored locally in:

`data/download_state.sqlite3`

The state tracks individual video, audio, and thumbnail parts. It supports partial-state reconciliation when files are missing, invalid, manually reclassified, or completed in a later mode.

## API Key Security

The API key entered in the application is used for YouTube Data API channel and video listing requests.

After an accepted fetch, the application persists the entered key in local settings using Windows DPAPI protection for the current Windows user. Legacy `data/api key.txt` and an application-root `api key.txt` remain supported for compatibility, with the manually entered key taking priority.

Protect plaintext API key files with appropriate filesystem permissions. Missing legacy files are treated as normal. Unreadable files or files that are not valid UTF-8 produce a friendly error rather than being reported as an invalid API key or network failure.

Never commit, upload, or include API keys in logs, screenshots, issue reports, or release packages.

## Cookies and Local Cookie Bridge

Cookies are optional. Use only cookies from an account and browser profile you control. Cookie files are sensitive authentication data and may provide access to the associated YouTube session.

The application supports two cookie sources:

- **File cookies.txt**: select a Netscape-format cookies text file.
- **Local Cookie Bridge**: select the `youtube_cookies.txt` file produced by a separately installed bridge tool.

The bridge extension is not bundled in this repository. Its selected file path is stored in local application settings. A legacy bridge location may be detected only for compatibility when no explicit bridge path has been saved.

For each yt-dlp attempt, the application copies the selected canonical cookie file into an isolated temporary attempt directory, applies private permissions when possible, and passes only that temporary copy to yt-dlp. The temporary directory is removed when the attempt exits.

If YouTube rejects a session, refresh YouTube in the source browser, complete any required sign-in or bot verification, export fresh cookies, and retry. Do not upload or commit cookie files.

## Data and Privacy

- SQLite download state is stored locally in `data/download_state.sqlite3`.
- Application settings are stored locally in `data/app_settings.json`.
- Downloaded media is written to the save folder you choose.
- Temporary media and isolated cookie copies are created locally for active attempts.
- Network requests are made to YouTube/Google APIs, YouTube pages, thumbnail URLs, and media endpoints required for product functions.

This repository does not implement application telemetry or analytics. This statement describes the application source in this repository and is not a guarantee about external services contacted for YouTube functionality.

## Troubleshooting

### Invalid API key

Confirm the key is active, YouTube Data API v3 is enabled for its Google Cloud project, and key restrictions permit that API. Replace an invalid legacy key file if one is being used.

### API quota exceeded

Wait for quota reset or provide another valid key. The application can try additional non-empty keys from the legacy key file after the manually entered key.

### Cannot resolve channel

Check the channel URL, `@handle`, username, or `UC...` channel ID. Private, deleted, renamed, or unsupported channel forms may not resolve.

### HTTP 403

YouTube media URLs and CDN access can expire or be rejected. Retry later, refresh cookies when authentication is involved, and confirm the system clock and network are stable.

### Bot verification or sign-in required

Open YouTube in the source browser, complete the verification or sign-in request, export fresh cookies, enable cookies in the application, and retry after the session is accepted.

### Expired cookies

Export a new cookie file from the active signed-in browser session. Select the new file or refresh the Local Cookie Bridge export before retrying.

### Missing Deno or JavaScript runtime

Restore `data/bin/deno.exe` from the portable ZIP. Some yt-dlp YouTube challenge flows require a supported JavaScript runtime.

### No Premiere-safe format

The video may not provide MP4 H.264/AAC streams at 1080p or below. Both engines enforce the same strict format policy.

### aria2 failure

Confirm `data/bin/aria2c.exe` exists and starts normally. CDN or HTTP response failures can still occur. Fast does not guarantee better performance; select Stable for a later batch when appropriate.

### File permission denied

Choose a writable save folder, close applications holding the output file, and check filesystem permissions. Administrator access is normally unnecessary.

### Path too long

Choose a shorter save-folder path. The application also limits generated filename length, but the complete Windows output path can still exceed its safety limit.

### Stop leaves a process running

Current Windows cancellation first requests `taskkill` for the process tree. If that command fails, times out, or returns a nonzero exit code, bounded `terminate()` and `kill()` fallbacks are attempted. Wait for the UI to finish its controlled shutdown before starting another run.

### Missing ffprobe.exe

Restore `data/bin/ffprobe.exe` from the portable ZIP. Validation may fall back through the FFmpeg tool path in some cases, but the supported portable layout includes ffprobe.

### Standalone EXE has missing runtimes

The standalone EXE is not a complete fresh installation. Extract the portable ZIP or place all required runtime files in `data/bin` beside the EXE.

## Release Verification

GitHub release notes list the SHA-256 values generated for that release. On Windows PowerShell, verify the portable ZIP with:

```powershell
Get-FileHash `
  -Algorithm SHA256 `
  -LiteralPath ".\Youtube-Downloaderbs-v1.3.1.zip"
```

Compare the output with the checksum in the corresponding GitHub release body. This repository does not document an independent signature for the release assets.

## Run and Build from Source

The current release workflow builds on Windows with Python `3.11`. Runtime media tools remain external to the Python application.

1. Clone or open the repository on Windows.
2. Install Python 3.11 and PyInstaller for packaging.
3. Place these runtime files under `data/bin`: `yt-dlp.exe`, `ffmpeg.exe`, `ffprobe.exe`, `aria2c.exe`, and `deno.exe`.
4. Run the source application with `python app.py`.

The Python source currently declares no required third-party runtime packages in `requirements.txt`; packaging requires PyInstaller.

Run source validation:

```powershell
python -m compileall app.py core ui scripts
python scripts/package_windows.py --preflight-only
```

Run every tracked smoke test:

```powershell
$SmokeTests = @(git ls-files "scripts/smoke_*.py" | Sort-Object)
foreach ($Test in $SmokeTests) {
    python $Test
    if ($LASTEXITCODE -ne 0) {
        throw "Smoke test failed: $Test"
    }
}
```

Build the one-file windowed executable when PyInstaller and runtime prerequisites are ready:

```powershell
python scripts/package_windows.py
```

The canonical stable release script performs additional pinned-runtime, checksum, PE x64, portable-layout, and release-asset validation. A local package build alone should not be described as a reproducible or signed release.

## Project Structure

```text
app.py       Application startup and SQLite initialization
core/        Download, API, state, filename, and runtime logic
ui/          Tkinter window and dialogs
scripts/     Packaging, release, diagnostic, and smoke scripts
docs/        UI contract, release notes, and historical documents
data/bin/    External runtime executables for portable/source use
VERSION      Current application version
```

## Known Limitations

- The application and release process are Windows-focused.
- Video output is limited to compatible H.264/AAC MP4 sources at 1080p or below.
- Channel listing depends on YouTube Data API availability and quota.
- YouTube extraction, CDN, bot-check, and format behavior may change.
- Cookie sessions can expire or be rejected.
- aria2 performance varies and may be slower than Stable.
- File start number continuation is session-only and resets when the application closes.
- No Authenticode signing guarantee is documented for current assets.
- No enterprise support or service-level guarantee is provided.

## Documentation and History

- [UI logic contract](docs/ui_logic_contract.md)
- [Release notes for v1.3.1](docs/release_notes_v1.3.1.md)
- [Historical Phase 3H.8 one-video lookahead note](docs/history/phase-3h8-one-video-lookahead.md)

## License and third-party software

No project license has been selected. Public availability of this repository is not a grant of permission to reproduce, redistribute, or create derivative works from the original project code or assets.

Third-party software retains its own license terms. See [Third-Party Notices](THIRD_PARTY_NOTICES.md) and [Legal Materials](legal/README.md) for the current known-direct-component inventory and preserved license texts. Release-package notice integration and source-distribution verification remain blocked pending Phase 6B2.

## Phase 6B1 controlled build inventory

A controlled Windows x64 PyInstaller build was inspected. [`legal/built-artifact-inventory.json`](legal/built-artifact-inventory.json) is a snapshot of that one exact controlled build; it does not prove that future binaries are identical. Unresolved native members remain explicit, and no binary is committed with the inventory.

## Release gate

[`legal/release-policy.json`](legal/release-policy.json) intentionally blocks all current version workflows until Phase 6B2. The gate runs before dependency installation, runtime acquisition, and application build. Existing releases are not retroactively certified by this technical control.

## Source availability status

FFmpeg/ffprobe Corresponding Source remains unverified, and aria2 source-distribution integration remains incomplete. Copying a license file or linking an upstream repository is not represented as sufficient. Phase 6B2 must provide verified source kits and equivalent release access before publishing is enabled.

## Phase 6B2A source correspondence audit

The exact pinned FFmpeg and aria2 binary packages were audited without executing them. Exact core source refs were resolved where the package evidence permitted, but core source is not represented as the full source required for the distributed static packages. External static-component sources and build-recipe gaps remain explicit. The result is **source correspondence partially identified**, **source kit not ready**, and **no compliance certification**.

[`legal/source-correspondence.json`](legal/source-correspondence.json) records the audit findings. [`legal/source-kit-requirements.json`](legal/source-kit-requirements.json) defines the prerequisites for Phase 6B2B. The audit confirms that all four workflows remain fail-closed; Phase 6B2A does not enable publishing, source kits and legal payloads remain absent, and existing releases are not retroactively certified.

## Phase 6B2B1 release legal payload

The contract in `legal/release-assets-v2.json` defines `s9h-release-legal-payload-v1` and `s9h-release-bundle-v2` without changing the project license status. It states that portable packages will include verified legal materials, and standalone EXE releases require a companion legal ZIP. The contract also states that legal materials do not replace source-distribution obligations.

The aria2 and FFmpeg source asset names are defined but assets are not ready. Release bundle v2 requires both source assets, the current release policy remains fail-closed, publishing remains disabled, and existing releases are not retroactively certified. Phase 6B2B2 remains required.

## Phase 6B2B2A source-kit feasibility

[`legal/source-input-inventory.json`](legal/source-input-inventory.json) is an evidence-only source input inventory; unresolved does not mean absent, and identified does not mean complete. No source kit was assembled, the release remains blocked, and existing releases are not retroactively certified. See the [feasibility report](docs/source-kit-feasibility.md) for the recorded gaps.

Phase 6B2B2A1a records official primary-source evidence for the six aria2 static inputs with **5 verified, 1 partial, 0 unresolved**. No source kit was assembled, assembly remains unauthorized, and publishing remains blocked. The result is evidence resolution only, not legal certification or proof of exact provider build reproduction; see [`legal/primary-source-evidence-aria2.json`](legal/primary-source-evidence-aria2.json).

Phase 6B2B2A1b reviews 16 FFmpeg codec-library names identified with static linkage by the exact Gyan package. Exact provider dependency versions remain unresolved, upstream project identification does not prove provider use, and no component was promoted. The result is **0 provider versions verified, 0 verified immutable inputs, 16 identified-name-only inputs, and 0 provider source archive hashes verified**; feasibility counts remain unchanged.

No source archive hash was accepted as a provider input, no source archive was committed, and no source kit was assembled. The aria2 verifier now owns aria2 semantics and shared gates, while the FFmpeg batch verifier owns this evidence batch; neither weakens the general feasibility gate. The exact historical recipe, toolchain, configure command, and patch set remain unresolved. Assembly remains unauthorized, publishing remains blocked, existing releases are not retroactively certified, and this is not legal advice; see [`legal/primary-source-evidence-ffmpeg-codecs.json`](legal/primary-source-evidence-ffmpeg-codecs.json).

Phase 6B2B2A1c records official-upstream context for 14 FFmpeg support-library names covering graphics, subtitle, text and audio-support evidence. Gyan identifies component names and static linkage, but exact provider dependency versions remain unresolved and upstream project identification does not prove provider use. No component was promoted: **provider versions verified: 0, verified immutable inputs: 0, identified-name-only inputs: 14, provider source archive hashes verified: 0**. Feasibility counts remain unchanged; no provider source archive hash was accepted, no source archive was committed, and no source kit was assembled.

The aria2 verifier owns aria2 data and shared gates, the codec verifier owns the prior 16 codec records, the support verifier owns the new 14 support records, and the general feasibility verifier owns overall schema and readiness. No verifier permanently freezes unrelated future evidence. The exact historical recipe, toolchain, configure command, and patch set remain unresolved. Assembly remains unauthorized, publishing remains blocked, existing releases are not retroactively certified, and this is not legal advice; see [`legal/primary-source-evidence-ffmpeg-support.json`](legal/primary-source-evidence-ffmpeg-support.json).

Phase 6B2B2A1d records FFmpeg hardware acceleration and system-interface evidence for 9 static SDK, toolkit, header, library or hardware-API candidates and 5 system candidates. Provider metadata identifies names and linkage, but exact toolkit, SDK and system-interface versions remain unresolved. No static candidate was promoted, no system candidate was declared fully resolved, and official vendor documentation does not prove historical provider version. System APIs are not automatically conventional source archives.

The result is **Total components: 14, Static candidates: 9, System candidates: 5, Provider versions verified: 0, Verified immutable inputs: 0, Identified-name-only inputs: 9, System-component candidates: 5, Provider archive hashes verified: 0**. No provider source archive hash was accepted, no source archive or SDK installer was committed, feasibility counts remain unchanged, and no source kit was assembled. The exact historical recipe remains unresolved. The exact toolchain remains unresolved. The exact configure command remains unresolved. The patch set remains unresolved. The aria2 verifier owns aria2 data and shared gates, the codec verifier owns the prior 16 codec records, the support verifier owns the prior 14 support records, the hardware/system verifier owns this 14-component batch, and the general feasibility verifier owns overall schema and readiness. No component verifier permanently freezes unrelated future evidence. Assembly remains unauthorized, publishing remains blocked, existing releases are not retroactively certified, and this is not legal advice; see [`legal/primary-source-evidence-ffmpeg-hardware-system.json`](legal/primary-source-evidence-ffmpeg-hardware-system.json).

Phase 6B2B2A1e records FFmpeg compression, security, networking and remaining library evidence for the final 11 static external-component names. Provider metadata identifies component names and static linkage, but exact provider dependency versions remain unresolved, upstream project identification does not prove provider use, and no component was promoted. The batch has **Current batch components: 11, Static components: 11, Provider versions verified: 0, Verified immutable inputs: 0, Identified-name-only inputs: 11, Provider source archive hashes verified: 0**.

No provider archive hash was accepted, no source archive was committed, feasibility counts remain unchanged, and no source kit was assembled. All 55 FFmpeg external components now have dedicated evidence-batch coverage, while source-kit completion remains false. Assembly remains unauthorized, publishing remains blocked, existing releases are not retroactively certified, and this is not legal advice; see [`legal/primary-source-evidence-ffmpeg-remaining-libraries.json`](legal/primary-source-evidence-ffmpeg-remaining-libraries.json).

### Phase 6B2B2A2 - FFmpeg provider toolchain and build-orchestration feasibility

Phase 6B2B2A2 does not modify the conservative source-input inventory; prior component verifiers retain ownership of it. Detailed provider toolchain and orchestration research is stored in [`legal/ffmpeg-provider-build-feasibility.json`](legal/ffmpeg-provider-build-feasibility.json). No readiness status changed: exact dependency versions, compiler and supporting-tool versions, configure command, patch evidence, and reproducibility remain unresolved. No build was performed, no binary was reproduced, and no source kit was assembled. Assembly remains unauthorized and publishing remains blocked. See the [detailed feasibility matrix](docs/source-kit-feasibility.md).
