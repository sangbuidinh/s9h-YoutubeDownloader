# Youtube Downloaderbs v1.3.0-rc.1

This is a prerelease for testing the new aria2c download engine and the
updated download pipeline.

## Highlights

- Added an optional aria2c accelerated media-transfer engine.
- Stable and aria2c now use the same Premiere-safe MP4 H.264/AAC
  pipeline up to 1080p.
- Added isolated per-attempt cookie handling.
- Added HTTP 403 authenticated metadata fallback.
- Added recovery for aria2 HTTP-response exit code 22.
- Added mandatory numbered output filenames.
- Added live aria2 percentage and speed in the two-line progress UI.
- Added per-video preparation, transfer, merge, validation, promotion
  and retry timing diagnostics.
- Improved responsive window behavior.
- Preserved API-first thumbnail downloads.
- Preserved validation, atomic promotion and video-scoped SQLite state.
- Bundled a checksum-pinned coherent FFmpeg/ffprobe Essentials pair.

## Download

For a new installation, download:

`Youtube-Downloaderbs-v1.3.0-rc.1.zip`

The portable ZIP contains the application and required runtime tools.

`Youtube.Downloaderbs.exe` is intended for updating an existing portable
installation that already has the required `data/bin` runtime files.

## Engine behavior

- Stable uses yt-dlp's internal downloader.
- aria2c uses multiple HTTP connections:
  `-x 16 -s 16 -j 16 -k 1M`.
- Both engines select MP4 H.264/AAC up to 1080p.
- Both engines use merge/remux only; no full video transcode is
  performed.
- aria2c performance depends on the selected video, CDN, IP and network.
- aria2c is not guaranteed to outperform Stable for every download.
- Stable remains the recommended compatibility-first option.

## Cookies and YouTube access

YouTube may return transient HTTP 403, bot-check or CDN throttling.

Refresh the configured cookies when authentication becomes invalid.

The application does not include or upload user cookies.

## Known limitations

- This is a release candidate.
- Live aria2 progress depends on the terminal format emitted by the
  bundled aria2c build.
- Network/CDN behavior may vary between downloads.
- Local Cookie Bridge requires a current exported cookie file.
- No automatic engine switch occurs between aria2c and Stable.

## Feedback

Please report reproducible issues with:

- selected engine;
- application log;
- yt-dlp version;
- aria2c version;
- whether cookies were enabled;
- sensitive URLs and cookie values removed.

Do not include private logs or user-specific paths.
