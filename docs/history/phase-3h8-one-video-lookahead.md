> Historical implementation note archived from the repository root
> README. It describes Phase 3H.8 and is not the current product guide.

---

# Phase 3H.8 — One-Video Lookahead Media Pipeline

## Purpose

Phase 3H.7 reduced the fixed 10-second pause, but each video still had to wait for its own authenticated metadata to reach the learned media age.

Phase 3H.8 pipelines one video ahead:

1. The current video uses authenticated metadata and downloads media without cookies.
2. While the current media transfer is running, a second yt-dlp process prepares authenticated metadata for the next video only.
3. When the next video starts, the app reuses that prefetched metadata.
4. If the metadata is already old enough, media download starts immediately.
5. If it is not old enough, the app waits only the remaining time rather than the full learned delay.

Example with a learned 10-second age:

- next metadata was prepared 12 seconds ago -> wait 0 seconds;
- next metadata was prepared 7 seconds ago -> wait 3 seconds;
- no prefetch available -> use the normal authenticated extraction fallback.

## Adaptive retry targets

Cookieless saved-media retry targets are now cumulative metadata ages:

- 2 seconds
- 5 seconds
- 10 seconds
- 30 seconds

The app waits only the difference between the current metadata age and the next target.

## Concurrency safety

The download controller now tracks more than one active subprocess so Stop/Cancel can terminate both:

- the current media transfer;
- the one-video metadata lookahead process.

Only one future video is prefetched. Media files are still downloaded sequentially.

## Preserved behavior

- Cookie Bridge and isolated per-attempt cookie copies;
- cookieless saved-media transfer after authenticated extraction;
- real-time timestamps on every visible log line;
- hidden `.s9h-stage-*` directories;
- MP4 container;
- H.264/AVC video;
- AAC audio;
- maximum 1080p;
- final FFmpeg/ffprobe Premiere-safe validation;
- `-N 1` fragment concurrency;
- SQLite status behavior;
- thumbnail workflow.

## Apply

Apply after Phase 3H.7.

1. Let the current batch finish.
2. Close the application and remaining yt-dlp/FFmpeg/Deno processes.
3. Extract this ZIP over the repository root and replace files.
4. Rebuild the EXE.

```powershell
Remove-Item build, dist -Recurse -Force -ErrorAction SilentlyContinue
python scripts\package_windows.py
```

## Files

- `core/downloader.py`
- `scripts/smoke_ytdlp_failure_classification.py`
- `scripts/smoke_cookie_media_lookahead.py`

## Validation

```powershell
python -m compileall -q core ui scripts
python scripts\smoke_cookie_media_lookahead.py
python scripts\smoke_ytdlp_failure_classification.py
python scripts\smoke_cookie_attempt_isolation.py
python scripts\smoke_progress_cancel.py
python scripts\smoke_hidden_staging_directory.py
python scripts\smoke_log_timestamps.py
python scripts\smoke_atomic_output_promotion.py
```

## Expected logs

```text
[COOKIE LOOKAHEAD] Preparing authenticated metadata for next video: ...
[COOKIE LOOKAHEAD] Authenticated metadata is ready for next video: ...
[COOKIE LOOKAHEAD] Reusing metadata prepared 12.4 seconds earlier.
[COOKIE BATCH MODE] Using one-video lookahead metadata; the media transfer will begin as soon as its learned age is reached.
[COOKIE LOOKAHEAD] Metadata already reached the learned age; starting media transfer immediately.
```

When the metadata is not old enough:

```text
[COOKIE LOOKAHEAD] Reusing metadata prepared 7.2 seconds earlier.
[COOKIE BATCH MODE] Waiting 3 seconds before the media transfer (learned metadata age; metadata age target: 10 seconds).
```
