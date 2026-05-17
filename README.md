# YouTube Downloader Source

This is the source-code version of the Windows desktop app.

## How to run

```powershell
cd "D:\Youtube Downloader Source"
python app.py
```

## Required runtime files

The app expects these files in the existing runtime folder:

```text
D:\Youtube Downloader\yt-dlp.exe
D:\Youtube Downloader\ffmpeg.exe
D:\Youtube Downloader\api key.txt
```

`api key.txt` is read only. The app never modifies it.

Optional: place `deno.exe` next to `yt-dlp.exe` to let yt-dlp use Deno for YouTube JavaScript challenge solving. It remains external so it can be updated independently.

When packaged, the app resolves its runtime folder from the location of `Youtube Downloaderbs.exe`. Keep these files next to the final executable:

```text
Youtube Downloader/
|-- Youtube Downloaderbs.exe
|-- yt-dlp.exe
|-- ffmpeg.exe
|-- api key.txt
\-- data
    \-- download_state.json
```

When running from source, app state is stored under `D:\Youtube Downloader Source\data`, and runtime tools are read from the source folder if present or from `D:\Youtube Downloader` during development.

## Download modes

The `Kiểu tải` dropdown controls which files are downloaded for selected videos:

1. `Video + Thumb`
2. `Audio MP3 + Thumb`
3. `Video + Audio MP3 + Thumb`

The default mode is `Video + Thumb`. MP3 extraction requires `ffmpeg.exe`.

## Output structure

For a selected save folder, downloads are written as:

```text
<selected folder>
\-- <Channel Name>
    |-- video
    |   |-- Example Title.mp4
    |   |-- Example Title (2).mp4
    |   \-- Another Video Title.mp4
    |-- thumb
    |   |-- Example Title.jpg
    |   |-- Example Title (2).jpg
    |   \-- Another Video Title.jpg
    \-- audio
        |-- Example Title.mp3
        |-- Example Title (2).mp3
        \-- Another Video Title.mp3
```

`audio/` is created only when an MP3 download mode is used. The output channel folder should only contain folders created by this app: `video/`, `thumb/`, and `audio/` when audio is used. Temporary files are created outside the output folder and cleaned when possible.

Persistent app state is stored outside the output channel folder. In source mode it is stored at `D:\Youtube Downloader Source\data\download_state.json`; in packaged mode it is stored at `data\download_state.json` next to `Youtube Downloaderbs.exe`.

`download_state.json` is the source of truth for video status. Saved `video_path`, `thumb_path`, and `audio_path` values are references only, because users may rename files after downloading.

The last manually entered API key is stored outside the output channel folder at `data\app_settings.json` next to `download_state.json`. The app ignores missing or corrupted settings and starts with an empty API key field.

Manual status edits are saved immediately to `data/download_state.json` by `channel_id` and `video_id`. Manual overrides are used first on later loads until the user clears the manual status or successfully downloads the requested files again.

Downloaded files use the original YouTube video title as the filename, sanitized only for Windows filename compatibility. Video, thumbnail, and audio outputs share the same sanitized base filename.

Video downloads use yt-dlp best available format by default. The final output is merged to `.mp4` when possible.

## Download limit

The speed limit field uses MB/s numbers only:

- Empty or `0` means unlimited.
- `5` is passed to yt-dlp as `--limit-rate 5M`.
- `1.5` is passed to yt-dlp as `--limit-rate 1.5M`.
- Text, negative values, and command-like values such as `--anything` are rejected.

## Loading more videos

Short videos are hidden by default. The app uses `videos.list(contentDetails.duration)` and the UI threshold `Ẩn video dưới: [3] phút` to decide visibility. Use the `Hiển thị video ngắn` checkbox to show all loaded videos without refetching.

The first fetch scans uploads until it has up to 100 visible videos after duration filtering, no more videos are available, or the 500-upload safety scan limit is reached. Use `Xem thêm video` to append the next older videos from the same uploads playlist.

## Current limitations

- No SQLite database.
- No JSON, TXT, CSV, metadata, or sidecar export.
- No one-folder-per-video output.
- No cloud sync or login system.
