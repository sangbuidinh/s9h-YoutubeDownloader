# YouTube Downloaderbs

Windows desktop app for fetching a YouTube channel video list with the YouTube Data API and downloading selected videos, thumbnails, and MP3 audio through external runtime tools.

## Download Latest Release

Download the latest packaged build from:

https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest

## Portable Folder Structure

The portable package should be extracted with this structure:

```text
Youtube Downloader/
|-- Youtube Downloaderbs.exe
\-- data/
    |-- api key.txt.example
    |-- cookies.txt.example
    |-- app_settings.example.json
    \-- bin/
        |-- yt-dlp.exe
        |-- ffmpeg.exe
        \-- deno.exe
```

Runtime files stay outside the executable so they can be updated without rebuilding the app.

## Run From Source

```powershell
cd "D:\Youtube Downloader Source"
python app.py
```

When running from source, app data is stored in this repository's `data` folder. Runtime tools are read from `data\bin` first, then from the source root, then from `D:\Youtube Downloader` during local development.

## Required Runtime Tools

- `data\bin\yt-dlp.exe`: required for video and thumbnail downloads.
- `data\bin\ffmpeg.exe`: required for merging video/audio and MP3 extraction.
- `data\bin\deno.exe`: optional helper for YouTube JavaScript challenge handling.

## YouTube API Key

You can enter an API key directly in the app. The last entered key is stored in `data\app_settings.json`.

You can also create `data\api key.txt` and put one API key per line. The packaged app includes only `data\api key.txt.example`; rename or copy it locally before adding real keys.

## Cookies Format

Cookies are optional. If YouTube asks for sign-in or bot verification, export cookies in Netscape `cookies.txt` format and select that file in the app.

Do not upload real cookies to GitHub or include them in release packages.

## Download Modes

The download mode selector controls which files are created for selected videos:

1. `Video + Thumb`
2. `Audio MP3 + Thumb`
3. `Video + Audio MP3 + Thumb`

The default mode is `Video + Thumb`. MP3 extraction requires `ffmpeg.exe`.

## Output Folder Structure

For the save folder selected in the UI, downloads are organized by channel:

```text
<Save folder>/
\-- <Channel name>/
    |-- video/
    |   \-- Example Title.mp4
    |-- thumb/
    |   \-- Example Title.jpg
    \-- audio/
        \-- Example Title.mp3
```

The `audio` folder is created only when an audio download mode is used.

## SQLite State Storage

The app stores download status in:

```text
data/download_state.sqlite3
```

This file is the only source of truth for downloaded, skipped, and manual statuses. The app does not depend on real output filenames when deciding old download status because users may rename downloaded files after download.

SQLite sidecar files may exist next to it:

```text
data/download_state.sqlite3-wal
data/download_state.sqlite3-shm
```

Do not delete these files if you want to keep download history and manual statuses.

## Packaging .exe

Build from the repository root:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Youtube Downloaderbs" app.py
```

Expected output:

```text
dist/Youtube Downloaderbs.exe
```

Build and release packages must keep user data and runtime tools outside the executable.

## Security Notes

Do not commit or upload:

- `data/download_state.sqlite3`
- `data/download_state.sqlite3-wal`
- `data/download_state.sqlite3-shm`
- `data/app_settings.json`
- cookies files
- API key files
- generated `.exe` files
- release archives

Only example files such as `data/app_settings.example.json`, `data/api key.txt.example`, and `data/cookies.txt.example` are safe to include.

## Current Limitations

- The app requires a YouTube Data API key to fetch channel videos.
- Some YouTube downloads may require valid cookies.
- Runtime tools must be updated manually in `data\bin`.
- Download status is stored by channel/video identity and selected save folder, not by scanning old output folders.
