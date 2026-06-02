# YouTube Downloaderbs

A portable Windows desktop app for fetching YouTube channel videos through the YouTube Data API and downloading selected videos, thumbnails, and MP3 audio with yt-dlp and ffmpeg.

## Key Features

- Fetch channel videos with YouTube Data API
- Download selected videos instead of entire channels
- Premiere-friendly MP4 download mode: H.264/AAC, max 1080p
- Thumbnail download
- MP3 audio extraction/download
- SQLite download history/manual status
- Optional cookies support for sign-in/bot-check cases
- Lightweight two-line progress display
- Portable runtime tools in `data/bin`

## Download

Download the latest packaged build from [here](https://github.com/sangbuidinh/s9h-YoutubeDownloader/releases/latest).

## Quick Start

1. Download the latest release zip.
2. Extract the whole folder.
3. Run `Youtube Downloaderbs.exe`.
4. Enter a YouTube Data API key.
5. Paste a channel URL, channel ID, or handle.
6. Click `Lấy danh sách Video`.
7. Select videos.
8. Choose a save folder and download mode.
9. Click download.

## Portable Folder Structure

Extract the whole release package and keep the executable with its `data` folder:

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

Do not move only the `.exe` away from the folder. Runtime tools are external and must remain available in `data/bin`.

## Requirements

- Windows
- YouTube Data API key
- `yt-dlp.exe` in `data/bin`
- `ffmpeg.exe` in `data/bin`
- `deno.exe` in `data/bin` for optional YouTube JavaScript challenge handling
- Optional `cookies*.txt` file for sign-in, bot-check, age-restricted, private, or session-gated downloads

## YouTube API Key

A YouTube Data API key is required to fetch channel video lists. Enter the key in the app before loading a channel.

The last entered key is stored locally in `data/app_settings.json`. The app can also read additional keys from `data/api key.txt`, one key per line. The packaged `data/api key.txt.example` file is only a template.

Do not commit or publish real API keys.

## Cookies

Cookies are optional. Use cookies when YouTube requires sign-in, bot verification, age/private access, or session-specific access.

The app supports selecting `cookies*.txt` files. Cookie exports should use Netscape cookies format.

Do not upload real cookies to GitHub or include them in release packages.

## Download Modes

| Mode | Output |
|---|---|
| Video + Thumb | `.mp4` + `.jpg` |
| Audio MP3 + Thumb | `.mp3` + `.jpg` |
| Video + Audio MP3 + Thumb | `.mp4` + `.mp3` + `.jpg` |

## Output Folder Structure

For the save folder selected in the app, downloads are organized by channel and output type:

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

The `audio` folder is used only when an audio download mode is selected.

## Download History / SQLite State

Download and manual status are stored in:

```text
data/download_state.sqlite3
```

SQLite is the source of truth for app status. Download status is stored by channel/video identity in SQLite and is not determined only by scanning output folders. This matters because users may rename or move downloaded files after download.

SQLite sidecar files may exist next to the database:

```text
data/download_state.sqlite3-wal
data/download_state.sqlite3-shm
```

Do not delete `.sqlite3`, `.wal`, or `.shm` files if you want to keep download history and manual statuses.

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Invalid API key | Wrong or disabled API key | Create/enter a valid YouTube Data API key |
| API quota exceeded | Daily quota used | Wait for quota reset or use another valid key |
| `yt-dlp.exe` missing | Runtime file missing | Keep `yt-dlp.exe` in `data/bin` |
| `ffmpeg.exe` missing | Runtime file missing | Keep `ffmpeg.exe` in `data/bin` |
| YouTube asks for sign-in/bot verification | YouTube anti-bot/session challenge | Enable cookies and select a valid `cookies*.txt` file |
| Download is slow or interrupted | Network/CDN/YouTube throttling | Retry later, update yt-dlp, or use valid cookies |
| MP3 extraction fails | ffmpeg missing or source MP4 invalid | Check `ffmpeg.exe` and retry |

## Run From Source

```powershell
python app.py
```

When running from source, app data is stored in this repository's `data` folder. Runtime tools are read from `data/bin`.

## Packaging

Build the Windows executable from the repository root:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Youtube Downloaderbs" app.py
```

Expected output:

```text
dist/Youtube Downloaderbs.exe
```

Release packages must keep user data and runtime tools outside the executable.

## Security Notes

Do not commit or upload:

- `data/download_state.sqlite3`
- `data/download_state.sqlite3-wal`
- `data/download_state.sqlite3-shm`
- `data/app_settings.json`
- cookies files
- API key files
- generated `.exe`
- release archives

Only example files such as `data/app_settings.example.json`, `data/api key.txt.example`, and `data/cookies.txt.example` are safe to include.

## Limitations

- Requires a YouTube Data API key to fetch channel videos.
- Some downloads may require valid cookies.
- Runtime tools must be updated manually in `data/bin`.
- YouTube behavior can change and may require updating yt-dlp.
- Download status is maintained in SQLite, not by full filesystem scanning.
