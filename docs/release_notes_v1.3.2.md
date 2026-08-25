# Youtube Downloaderbs v1.3.2

This patch release refreshes the checksum-pinned yt-dlp runtime and simplifies
Fast video media transport to native yt-dlp in the normal Windows portable
build.

## Changes

- Replaces bundled yt-dlp 2026.03.17 with the verified official nightly
  `2026.08.18.122307` Windows x64 executable.
- Pins the exact official asset URL and SHA-256 in the reproducible release
  build.
- Records the nightly binary distribution tag separately from upstream source
  commit `5d5b634d8e6b41dc2891847a5ea7a5a3f569a28c`.
- Synchronizes the active third-party notice and license payload.
- Resolves the existing Premiere-safe selector once for Fast video. Split
  selections download the exact H.264 MP4 video stream and exact AAC/M4A
  companion stream through native yt-dlp with one fragment worker, then
  stream-copy both staged streams. A valid combined fallback downloads its
  exact top-level H.264/AAC MP4 format through native yt-dlp without a companion
  transfer or extra merge. Both routes reuse the same saved metadata snapshot
  and retain validation and final promotion.
- Preserves Stable video behavior, separate MP3 output behavior, speed limits,
  cancellation, numbering, and BUG-01 output ownership. aria2 remains bundled
  and unchanged for the existing separate Fast MP3 path.

## Root cause boundary

Bundled yt-dlp 2026.03.17 was incompatible with the tested current YouTube
media path and produced HTTP 403 failures. Replacing only yt-dlp with the
verified 2026.08.18.122307 nightly restored successful Fast aria2
transfer/progress in the controlled comparison.

The later six-video comparison showed that the x16 external-downloader video
leg did not meet the Fast performance target. Production now uses the simpler
native saved-metadata transport directly; it does not use a throughput watchdog
or switch transport during a download. This is not a claim of a proven aria2,
network, or historical performance regression.

This patch does not change the aria2 binary/profile where it remains in use,
the progress queue, the Tk UI, BUG-01 output ownership, or BUG-02 modal behavior.
Fast split-video progress maps the two native transfer legs into one
non-decreasing logical transfer span; a combined fallback maps its single
native transfer across the full transfer span.
Neither route marks the item complete before its required merge, validation,
and promotion steps finish.

The controlled six-video package comparison is accepted for this release. Stable
recorded 82.18 seconds total, 22.45 seconds preparation, 53.00 seconds transfer,
and approximately 84.5 seconds wall time. Fast recorded 70.97 seconds total,
11.48 seconds preparation, 52.83 seconds transfer, and 74.265 seconds wall time.
That is an 11.21-second (13.64%) saving. Most of the measured improvement was in
preparation time; transfer time was approximately unchanged. This controlled
result is not a universal speed guarantee.

Downloaded state is video-scoped. Changing numbering or the Save folder alone
does not authorize a re-download. Manually setting `Chưa tải` remains an explicit
re-download override, while a missing-part state repairs only the required
missing outputs and preserves completed parts. Performance telemetry now counts
media subprocess attempts without counting metadata extraction, and the Fast
documentation reflects the native yt-dlp video transport used by this release.

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
