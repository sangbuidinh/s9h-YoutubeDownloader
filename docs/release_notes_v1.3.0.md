# Youtube Downloaderbs v1.3.0

This stable release adds the aria2c download engine while preserving the
existing Premiere-safe Stable pipeline.

## Highlights

- Added an optional aria2c accelerated media-transfer engine.
- Stable and aria2c use the same MP4 H.264/AAC format policy up to 1080p.
- Added isolated per-attempt cookie handling.
- Added HTTP 403 authenticated metadata recovery.
- Added aria2 HTTP-response exit-code 22 recovery.
- Added live aria2 percentage and speed in the progress UI.
- Added per-video stage timing diagnostics.
- Added mandatory numbered output filenames.
- Improved responsive window behavior.
- Preserved API-first thumbnail downloading.
- Preserved video-scoped SQLite state, validation and atomic promotion.

## Download

New users should download:

`Youtube-Downloaderbs-v1.3.0.zip`

The standalone:

`Youtube.Downloaderbs.exe`

is intended for updating an existing portable installation that already
contains the required runtime files.

## Engines

### Stable

Uses yt-dlp internal transfer and remains the compatibility-first
default.

### aria2c / Fast

Uses aria2c with:

`-x 16 -s 16 -j 16 -k 1M`

aria2c performance varies by video, CDN, network and IP. It is not
guaranteed to be faster for every download.

## Output compatibility

- MP4 container
- H.264 video
- AAC audio
- maximum 1080p
- merge/remux without full video transcoding

## Runtime components

- yt-dlp 2026.03.17
- FFmpeg 8.1.2 Essentials
- ffprobe 8.1.2 Essentials
- aria2c 1.37.0
- Deno 2.7.14

## Notes

YouTube may return temporary HTTP 403, bot-check or CDN-throttling
responses.

Refresh cookies when authentication expires.

Do not include checksum values manually in the tracked notes. The stable
build script will append actual workflow-generated checksum strings.
