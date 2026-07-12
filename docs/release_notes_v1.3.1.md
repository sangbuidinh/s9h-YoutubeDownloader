# Youtube Downloaderbs v1.3.1

This patch release improves numbered download continuation after a batch
is stopped or interrupted.

## Changes

- The visible File start number now advances after each newly completed
  logical video.
- The advanced value remains available after Stop or a controlled
  download error.
- Failed, cancelled, skipped, incomplete and previously completed items
  do not advance the number.
- Duplicate completion callbacks are ignored.
- Delayed events from an older download run cannot modify a newer run.
- Manual changes to the visible number remain the source of truth for
  the next run.
- Number synchronization remains session-only and is not saved to
  SQLite or application settings.
- Active-batch filename allocation remains unchanged.

## Existing functionality retained

- Stable yt-dlp internal transfer engine.
- Optional aria2c accelerated transfer engine.
- Premiere-safe MP4 H.264/AAC output up to 1080p.
- Isolated cookie handling and authenticated metadata recovery.
- API-first thumbnail downloading.
- Video-scoped SQLite state.
- Validation and atomic output promotion.
- Mandatory numbered output filenames.

## Download

New users should download:

`Youtube-Downloaderbs-v1.3.1.zip`

The standalone executable:

`Youtube.Downloaderbs.exe`

is intended for updating an existing portable installation that already
contains the required runtime files.

## Runtime components

- yt-dlp 2026.03.17
- FFmpeg 8.1.2 Essentials
- ffprobe 8.1.2 Essentials
- aria2c 1.37.0
- Deno 2.7.14

## Notes

The updated File start number is retained only while the application
remains open. Closing and reopening the application resets the field to
blank.

YouTube may return temporary HTTP 403, bot-check or CDN-throttling
responses. Refresh cookies when authentication expires.

Do not manually insert asset checksum values into this tracked document.
The release build must append the actual workflow-generated SHA-256
values to the generated release body.
