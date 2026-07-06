$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$ReleaseTag = "v1.2.7-rc.1"
$ZipName = "Youtube-Downloaderbs-$ReleaseTag.zip"
$ReleaseRoot = Join-Path $RepoRoot "release"
$AssetsRoot = Join-Path $ReleaseRoot "assets"
$RuntimeRoot = Join-Path $ReleaseRoot "runtime"
$BinRoot = Join-Path $RuntimeRoot "data\bin"
$TempRoot = Join-Path $ReleaseRoot "temp"
$VerifyRoot = Join-Path $ReleaseRoot "verify"

Remove-Item $ReleaseRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $AssetsRoot, $BinRoot, $TempRoot | Out-Null

Write-Host "== Source validation =="
python -m compileall -q core ui scripts
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }

python scripts\package_windows.py --preflight-only
if ($LASTEXITCODE -ne 0) { throw "packaging preflight failed" }

$TargetedTests = @(
    "scripts\smoke_ytdlp_failure_classification.py",
    "scripts\smoke_cookie_media_lookahead.py",
    "scripts\smoke_log_timestamps.py",
    "scripts\smoke_hidden_staging_directory.py",
    "scripts\smoke_premiere_safe_downloads.py"
)
foreach ($Test in $TargetedTests) {
    if (Test-Path $Test) {
        python $Test
        if ($LASTEXITCODE -ne 0) { throw "Smoke test failed: $Test" }
    }
}

@'
from app import _configure_cookie_media_strategy
from core import downloader

_configure_cookie_media_strategy()
assert downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS == (10, 30)
assert downloader.COOKIE_MEDIA_SHORT_PROBE_SECONDS == 30
assert downloader.COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS > 1_000_000

state = downloader._YtdlpBatchState(
    cookie_bootstrap_media_mode=True,
    media_settle_delay_seconds=30,
    media_videos_since_probe=downloader.COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS,
)
delay, is_probe = downloader._batch_cookie_media_initial_delay(state)
assert delay == 30, (delay, is_probe)
assert is_probe is False, (delay, is_probe)
print("sticky 30-second fallback validation passed")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw "Sticky cookie strategy validation failed" }

Write-Host "== Build Windows executable =="
python scripts\package_windows.py
if ($LASTEXITCODE -ne 0) { throw "Windows EXE build failed" }

$BuiltExe = Join-Path $RepoRoot "dist\Youtube Downloaderbs.exe"
$StandaloneExe = Join-Path $AssetsRoot "Youtube.Downloaderbs.exe"
if (-not (Test-Path $BuiltExe)) { throw "Built EXE not found: $BuiltExe" }
Copy-Item $BuiltExe $StandaloneExe -Force
Copy-Item $BuiltExe (Join-Path $RuntimeRoot "Youtube.Downloaderbs.exe") -Force

Write-Host "== Download runtime tools =="
$YtDlpPath = Join-Path $BinRoot "yt-dlp.exe"
Invoke-WebRequest "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile $YtDlpPath

$FfmpegZip = Join-Path $TempRoot "ffmpeg.zip"
$FfmpegExtract = Join-Path $TempRoot "ffmpeg"
Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $FfmpegZip
Expand-Archive $FfmpegZip $FfmpegExtract -Force
$FfmpegSource = Get-ChildItem $FfmpegExtract -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if (-not $FfmpegSource) { throw "ffmpeg.exe was not found after extraction" }
$FfmpegPath = Join-Path $BinRoot "ffmpeg.exe"
Copy-Item $FfmpegSource.FullName $FfmpegPath -Force

$DenoZip = Join-Path $TempRoot "deno.zip"
$DenoExtract = Join-Path $TempRoot "deno"
Invoke-WebRequest "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" -OutFile $DenoZip
Expand-Archive $DenoZip $DenoExtract -Force
$DenoPath = Join-Path $BinRoot "deno.exe"
Copy-Item (Join-Path $DenoExtract "deno.exe") $DenoPath -Force

Write-Host "== Validate binaries =="
$ExeBytes = [IO.File]::ReadAllBytes($StandaloneExe)
if ($ExeBytes.Length -lt 1MB -or $ExeBytes[0] -ne 0x4D -or $ExeBytes[1] -ne 0x5A) {
    throw "Standalone EXE failed PE validation"
}

& $YtDlpPath --version
if ($LASTEXITCODE -ne 0) { throw "yt-dlp runtime validation failed" }
& $FfmpegPath -version
if ($LASTEXITCODE -ne 0) { throw "FFmpeg runtime validation failed" }
& $DenoPath --version
if ($LASTEXITCODE -ne 0) { throw "Deno runtime validation failed" }

Write-Host "== Create complete runtime ZIP =="
$ZipPath = Join-Path $AssetsRoot $ZipName
Compress-Archive -Path (Join-Path $RuntimeRoot "*") -DestinationPath $ZipPath -Force
Expand-Archive $ZipPath $VerifyRoot -Force

$RequiredFiles = @(
    "Youtube.Downloaderbs.exe",
    "data\bin\yt-dlp.exe",
    "data\bin\ffmpeg.exe",
    "data\bin\deno.exe"
)
foreach ($RelativePath in $RequiredFiles) {
    $FullPath = Join-Path $VerifyRoot $RelativePath
    if (-not (Test-Path $FullPath)) { throw "Runtime ZIP is missing $RelativePath" }
}

$ExeHash = Get-FileHash $StandaloneExe -Algorithm SHA256
$ZipHash = Get-FileHash $ZipPath -Algorithm SHA256
$ExeInfo = Get-Item $StandaloneExe
$ZipInfo = Get-Item $ZipPath

Write-Host "EXE: $($ExeInfo.Name) | $($ExeInfo.Length) bytes | SHA256 $($ExeHash.Hash)"
Write-Host "ZIP: $($ZipInfo.Name) | $($ZipInfo.Length) bytes | SHA256 $($ZipHash.Hash)"

$NotesPath = Join-Path $ReleaseRoot "RELEASE_NOTES.md"
@"
## What changed

- Uses authenticated metadata with a stable 10-second cookieless media age target.
- Removes repeated 2-second and 5-second probes from production downloads.
- Keeps a learned 30-second fallback sticky instead of probing 10 seconds again on the next video.
- Preserves one-video lookahead so the age requirement is normally absorbed while the previous video downloads.
- Keeps Cookie Bridge, isolated cookie copies, real-time timestamped logs, hidden staging folders, `-N 1`, and Premiere-safe MP4 H.264/AAC output up to 1080p.

## Release files

- `Youtube.Downloaderbs.exe` — standalone Windows executable.
- `$ZipName` — executable plus yt-dlp, FFmpeg and Deno runtime files.
- GitHub generates Source code ZIP and TAR.GZ automatically from the release tag.

## Checksums

- `Youtube.Downloaderbs.exe`: `$($ExeHash.Hash)`
- `$ZipName`: `$($ZipHash.Hash)`

This is a prerelease for real download testing before the next stable version.
"@ | Set-Content $NotesPath -Encoding utf8

Write-Host "Prerelease assets are ready in $AssetsRoot"
