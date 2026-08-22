# Youtube Downloaderbs v1.3.2

This patch release refreshes the checksum-pinned yt-dlp runtime and removes
the confirmed external-downloader overhead from Fast video's companion-audio
leg in the normal Windows portable build.

## Changes

- Replaces bundled yt-dlp 2026.03.17 with the verified official nightly
  `2026.08.18.122307` Windows x64 executable.
- Pins the exact official asset URL and SHA-256 in the reproducible release
  build.
- Records the nightly binary distribution tag separately from upstream source
  commit `5d5b634d8e6b41dc2891847a5ea7a5a3f569a28c`.
- Synchronizes the active third-party notice and license payload.
- Resolves the existing Premiere-safe selector once for Fast video, downloads
  the exact H.264 MP4 video stream through aria2c at the unchanged x16 profile,
  downloads the exact AAC/M4A companion stream through native yt-dlp, and
  stream-copies both staged streams before validation and final promotion.
- Preserves Stable video behavior, separate MP3 output behavior, speed limits,
  cancellation, numbering, and BUG-01 output ownership.

## Root cause boundary

Bundled yt-dlp 2026.03.17 was incompatible with the tested current YouTube
media path and produced HTTP 403 failures. Replacing only yt-dlp with the
verified 2026.08.18.122307 nightly restored successful Fast aria2
transfer/progress in the controlled comparison.

This patch does not change aria2, the progress parser or queue, the Tk UI,
BUG-01 output ownership, or BUG-02 modal behavior. Fast video progress now
maps the two transfer legs into one non-decreasing logical transfer span and
does not mark the item complete until merge, validation, and promotion finish.

The accepted current performance bottleneck is
`CURRENT_FAST_AUDIO_EXTERNAL_DOWNLOADER_OVERHEAD`. This is not a claim of a
proven historical aria2 regression. Production performance acceptance remains
pending operator testing of the packaged application.

## Download

New users should download:

`Youtube-Downloaderbs-v1.3.2.zip`

The standalone executable:

`Youtube.Downloaderbs.exe`

is intended for updating an existing portable installation that already
contains the required runtime files.

## Runtime components

- yt-dlp nightly 2026.08.18.122307
- FFmpeg 8.1.2 Essentials
- ffprobe 8.1.2 Essentials
- aria2c 1.37.0
- Deno 2.7.14

Do not manually insert asset checksum values into this tracked document. The
release build must append the actual workflow-generated SHA-256 values to the
generated release body.
