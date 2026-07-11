[CmdletBinding()]
param(
    [switch]$PreparePinnedRuntime
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$ReleaseVersion = "1.3.0"
$ReleaseTag = "v$ReleaseVersion"
$ZipName = "Youtube-Downloaderbs-$ReleaseTag.zip"
$ReleaseRoot = Join-Path $RepoRoot "release"
$AssetsRoot = Join-Path $ReleaseRoot "assets"
$RuntimeRoot = Join-Path $ReleaseRoot "runtime"
$RuntimeSourceBin = Join-Path $RepoRoot "data\bin"
$BinRoot = Join-Path $RuntimeRoot "data\bin"
$TempRoot = Join-Path $ReleaseRoot "temp"
$VerifyRoot = Join-Path $ReleaseRoot "verify"

$YtDlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/download/2026.03.17/yt-dlp.exe"
$YtDlpSha256 = "3DB811B366B2DA47337D2FCFDFE5BBD9A258DAD3F350C54974F005DF115A1545"

$FfmpegArchiveUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip"
$FfmpegArchiveSha256 = "DB580001CAA24AC104C8CB856CD113A87B0A443F7BDF47D8C12B1D740584A2EC"
$FfmpegBundleIdentity = "8.1.2-essentials_build-www.gyan.dev"
$FfmpegSha256 = "1326DDE4C84FF1F96FE6B8916C5BED29E163E9B5DCCF995F6F3DB069D143EC5E"
$FfprobeSha256 = "B49CCC7C6547B141AD5A2F6EC69CC04323D7133D7704D70B331B904C63EECB07"

$Aria2ArchiveUrl = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
$Aria2ArchiveSha256 = "67D015301EEF0B612191212D564C5BB0A14B5B9C4796B76454276A4D28D9B288"
$Aria2Sha256 = "BE2099C214F63A3CB4954B09A0BECD6E2E34660B886D4C898D260FEBFE9D70C2"

$DenoArchiveUrl = "https://github.com/denoland/deno/releases/download/v2.7.14/deno-x86_64-pc-windows-msvc.zip"
$DenoArchiveSha256 = "25F9871F5C1D9E999D60071F8069767134495FD601D2E2C7CE1E8C641487BDA0"
$DenoSha256 = "B6E83993F1F1AB97075A77043DE61118966D719B5450BC631251D47C3A34230B"

function Assert-File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description is missing"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "$Description is empty"
    }
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Assert-File -Path $Path -Description $Description
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($Actual -ne $Expected.ToUpperInvariant()) {
        throw "$Description SHA-256 mismatch"
    }
    return $Actual
}

function Assert-Pe64 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Assert-File -Path $Path -Description $Description
    $Stream = [IO.File]::OpenRead($Path)
    $Reader = [IO.BinaryReader]::new($Stream)
    try {
        if ($Reader.ReadUInt16() -ne 0x5A4D) {
            throw "$Description is not a Windows PE executable"
        }
        $Stream.Position = 0x3C
        $PeOffset = $Reader.ReadInt32()
        if ($PeOffset -lt 0 -or $PeOffset -gt ($Stream.Length - 6)) {
            throw "$Description has an invalid PE header offset"
        }
        $Stream.Position = $PeOffset
        if ($Reader.ReadUInt32() -ne 0x00004550) {
            throw "$Description has an invalid PE signature"
        }
        if ($Reader.ReadUInt16() -ne 0x8664) {
            throw "$Description is not a 64-bit x86 Windows executable"
        }
    }
    finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Get-VersionLine {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $Output = @(& $Path @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "$Description version command failed"
    }
    $Line = $Output |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($Line)) {
        throw "$Description returned no version text"
    }
    return $Line.Trim()
}

function Invoke-CheckedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "Downloading $Description"
    Invoke-WebRequest -Uri $Uri -OutFile $Destination
    [void](Assert-Sha256 -Path $Destination -Expected $ExpectedSha256 -Description $Description)
}

function Get-SingleExtractedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $Matches = @(Get-ChildItem -LiteralPath $Root -Recurse -Filter $Name -File)
    if ($Matches.Count -ne 1) {
        throw "$Description archive contained $($Matches.Count) matching files"
    }
    return $Matches[0].FullName
}

function Prepare-PinnedRuntimeFiles {
    $DownloadRoot = Join-Path $TempRoot "runtime-downloads"
    $ExtractRoot = Join-Path $TempRoot "runtime-extract"
    New-Item -ItemType Directory -Force -Path $DownloadRoot, $ExtractRoot | Out-Null

    $DownloadedYtDlp = Join-Path $DownloadRoot "yt-dlp.exe"
    Invoke-CheckedDownload -Uri $YtDlpUrl -Destination $DownloadedYtDlp `
        -ExpectedSha256 $YtDlpSha256 -Description "yt-dlp 2026.03.17"

    $FfmpegArchive = Join-Path $DownloadRoot "ffmpeg-8.1.2-essentials_build.zip"
    Invoke-CheckedDownload -Uri $FfmpegArchiveUrl -Destination $FfmpegArchive `
        -ExpectedSha256 $FfmpegArchiveSha256 -Description "Gyan FFmpeg 8.1.2 Essentials archive"
    $FfmpegExtract = Join-Path $ExtractRoot "ffmpeg"
    Expand-Archive -LiteralPath $FfmpegArchive -DestinationPath $FfmpegExtract
    $DownloadedFfmpeg = Get-SingleExtractedFile -Root $FfmpegExtract -Name "ffmpeg.exe" -Description "FFmpeg"
    $DownloadedFfprobe = Get-SingleExtractedFile -Root $FfmpegExtract -Name "ffprobe.exe" -Description "ffprobe"
    [void](Assert-Sha256 -Path $DownloadedFfmpeg -Expected $FfmpegSha256 -Description "ffmpeg.exe")
    [void](Assert-Sha256 -Path $DownloadedFfprobe -Expected $FfprobeSha256 -Description "ffprobe.exe")

    $Aria2Archive = Join-Path $DownloadRoot "aria2-1.37.0-win-64bit-build1.zip"
    Invoke-CheckedDownload -Uri $Aria2ArchiveUrl -Destination $Aria2Archive `
        -ExpectedSha256 $Aria2ArchiveSha256 -Description "aria2 1.37.0 archive"
    $Aria2Extract = Join-Path $ExtractRoot "aria2"
    Expand-Archive -LiteralPath $Aria2Archive -DestinationPath $Aria2Extract
    $DownloadedAria2 = Get-SingleExtractedFile -Root $Aria2Extract -Name "aria2c.exe" -Description "aria2"
    [void](Assert-Sha256 -Path $DownloadedAria2 -Expected $Aria2Sha256 -Description "aria2c.exe")

    $DenoArchive = Join-Path $DownloadRoot "deno-x86_64-pc-windows-msvc.zip"
    Invoke-CheckedDownload -Uri $DenoArchiveUrl -Destination $DenoArchive `
        -ExpectedSha256 $DenoArchiveSha256 -Description "Deno 2.7.14 archive"
    $DenoExtract = Join-Path $ExtractRoot "deno"
    Expand-Archive -LiteralPath $DenoArchive -DestinationPath $DenoExtract
    $DownloadedDeno = Get-SingleExtractedFile -Root $DenoExtract -Name "deno.exe" -Description "Deno"
    [void](Assert-Sha256 -Path $DownloadedDeno -Expected $DenoSha256 -Description "deno.exe")

    New-Item -ItemType Directory -Force -Path $RuntimeSourceBin | Out-Null
    Copy-Item -LiteralPath $DownloadedYtDlp -Destination (Join-Path $RuntimeSourceBin "yt-dlp.exe") -Force
    Copy-Item -LiteralPath $DownloadedFfmpeg -Destination (Join-Path $RuntimeSourceBin "ffmpeg.exe") -Force
    Copy-Item -LiteralPath $DownloadedFfprobe -Destination (Join-Path $RuntimeSourceBin "ffprobe.exe") -Force
    Copy-Item -LiteralPath $DownloadedAria2 -Destination (Join-Path $RuntimeSourceBin "aria2c.exe") -Force
    Copy-Item -LiteralPath $DownloadedDeno -Destination (Join-Path $RuntimeSourceBin "deno.exe") -Force
}

$TrackedStatus = @(git status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the tracked working tree" }
if ($TrackedStatus.Count -gt 0) { throw "Tracked working tree must be clean before building" }

$VersionFile = Join-Path $RepoRoot "VERSION"
Assert-File -Path $VersionFile -Description "VERSION"
$ActiveVersion = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
if ($ActiveVersion -ne $ReleaseVersion) {
    throw "VERSION must be $ReleaseVersion"
}

Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $AssetsRoot, $BinRoot, $TempRoot | Out-Null

if ($PreparePinnedRuntime) {
    Write-Host "== Prepare checksum-pinned runtime tools =="
    Prepare-PinnedRuntimeFiles
}

Write-Host "== Validate runtime source =="
$YtDlpPath = Join-Path $RuntimeSourceBin "yt-dlp.exe"
$FfmpegPath = Join-Path $RuntimeSourceBin "ffmpeg.exe"
$FfprobePath = Join-Path $RuntimeSourceBin "ffprobe.exe"
$Aria2Path = Join-Path $RuntimeSourceBin "aria2c.exe"
$DenoPath = Join-Path $RuntimeSourceBin "deno.exe"

$RuntimeExpectations = @(
    @{ Path = $YtDlpPath; Name = "yt-dlp.exe"; Hash = $YtDlpSha256 },
    @{ Path = $FfmpegPath; Name = "ffmpeg.exe"; Hash = $FfmpegSha256 },
    @{ Path = $FfprobePath; Name = "ffprobe.exe"; Hash = $FfprobeSha256 },
    @{ Path = $Aria2Path; Name = "aria2c.exe"; Hash = $Aria2Sha256 },
    @{ Path = $DenoPath; Name = "deno.exe"; Hash = $DenoSha256 }
)
foreach ($Runtime in $RuntimeExpectations) {
    [void](Assert-Sha256 -Path $Runtime.Path -Expected $Runtime.Hash -Description $Runtime.Name)
    Assert-Pe64 -Path $Runtime.Path -Description $Runtime.Name
}

$YtDlpVersion = Get-VersionLine -Path $YtDlpPath -Arguments @("--version") -Description "yt-dlp"
$FfmpegVersion = Get-VersionLine -Path $FfmpegPath -Arguments @("-version") -Description "ffmpeg"
$FfprobeVersion = Get-VersionLine -Path $FfprobePath -Arguments @("-version") -Description "ffprobe"
$Aria2Version = Get-VersionLine -Path $Aria2Path -Arguments @("--version") -Description "aria2c"
$DenoVersion = Get-VersionLine -Path $DenoPath -Arguments @("--version") -Description "Deno"

if ($YtDlpVersion -ne "2026.03.17") { throw "Unexpected yt-dlp version" }
if ($FfmpegVersion -notmatch [regex]::Escape($FfmpegBundleIdentity)) { throw "Unexpected FFmpeg bundle" }
if ($FfprobeVersion -notmatch [regex]::Escape($FfmpegBundleIdentity)) { throw "Unexpected ffprobe bundle" }
if ($Aria2Version -ne "aria2 version 1.37.0") { throw "Unexpected aria2c version" }
if ($DenoVersion -notmatch "^deno 2\.7\.14 ") { throw "Unexpected Deno version" }

Write-Host "yt-dlp: $YtDlpVersion"
Write-Host "ffmpeg: $FfmpegVersion"
Write-Host "ffprobe: $FfprobeVersion"
Write-Host "aria2c: $Aria2Version"
Write-Host "deno: $DenoVersion"

Write-Host "== Source validation =="
python -m compileall -q app.py core ui scripts
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }

python scripts\package_windows.py --preflight-only
if ($LASTEXITCODE -ne 0) { throw "packaging preflight failed" }

$SmokeTests = @(git ls-files "scripts/smoke_*.py" | Sort-Object)
if ($LASTEXITCODE -ne 0 -or $SmokeTests.Count -lt 1) { throw "Could not enumerate smoke tests" }
foreach ($Test in $SmokeTests) {
    python $Test
    if ($LASTEXITCODE -ne 0) { throw "Smoke test failed: $Test" }
}

Write-Host "== Build Windows executable =="
python scripts\package_windows.py
if ($LASTEXITCODE -ne 0) { throw "Windows EXE build failed" }

$BuiltExe = Join-Path $RepoRoot "dist\Youtube Downloaderbs.exe"
$StandaloneExe = Join-Path $AssetsRoot "Youtube.Downloaderbs.exe"
Assert-File -Path $BuiltExe -Description "built application EXE"
Assert-Pe64 -Path $BuiltExe -Description "built application EXE"
Copy-Item -LiteralPath $BuiltExe -Destination $StandaloneExe -Force
Copy-Item -LiteralPath $BuiltExe -Destination (Join-Path $RuntimeRoot "Youtube.Downloaderbs.exe") -Force

foreach ($Runtime in $RuntimeExpectations) {
    Copy-Item -LiteralPath $Runtime.Path -Destination (Join-Path $BinRoot $Runtime.Name) -Force
}

Write-Host "== Create and verify portable ZIP =="
$ZipPath = Join-Path $AssetsRoot $ZipName
Compress-Archive -Path (Join-Path $RuntimeRoot "*") -DestinationPath $ZipPath
Expand-Archive -LiteralPath $ZipPath -DestinationPath $VerifyRoot

$RequiredFiles = @(
    "Youtube.Downloaderbs.exe",
    "data\bin\yt-dlp.exe",
    "data\bin\ffmpeg.exe",
    "data\bin\ffprobe.exe",
    "data\bin\aria2c.exe",
    "data\bin\deno.exe"
) | Sort-Object

$ActualFiles = @(
    Get-ChildItem -LiteralPath $VerifyRoot -Recurse -File |
        ForEach-Object { $_.FullName.Substring($VerifyRoot.Length + 1) } |
        Sort-Object
)
$ManifestDifference = @(Compare-Object -ReferenceObject $RequiredFiles -DifferenceObject $ActualFiles)
if ($ManifestDifference.Count -gt 0) {
    throw "Portable ZIP manifest differs from the required six-file contract"
}

foreach ($RelativePath in $RequiredFiles) {
    $FullPath = Join-Path $VerifyRoot $RelativePath
    Assert-File -Path $FullPath -Description "packaged $RelativePath"
    Assert-Pe64 -Path $FullPath -Description "packaged $RelativePath"
}

$PackagedYtDlpVersion = Get-VersionLine -Path (Join-Path $VerifyRoot "data\bin\yt-dlp.exe") -Arguments @("--version") -Description "packaged yt-dlp"
$PackagedFfmpegVersion = Get-VersionLine -Path (Join-Path $VerifyRoot "data\bin\ffmpeg.exe") -Arguments @("-version") -Description "packaged ffmpeg"
$PackagedFfprobeVersion = Get-VersionLine -Path (Join-Path $VerifyRoot "data\bin\ffprobe.exe") -Arguments @("-version") -Description "packaged ffprobe"
$PackagedAria2Version = Get-VersionLine -Path (Join-Path $VerifyRoot "data\bin\aria2c.exe") -Arguments @("--version") -Description "packaged aria2c"
$PackagedDenoVersion = Get-VersionLine -Path (Join-Path $VerifyRoot "data\bin\deno.exe") -Arguments @("--version") -Description "packaged Deno"

if ($PackagedYtDlpVersion -ne $YtDlpVersion) { throw "Packaged yt-dlp version changed" }
if ($PackagedFfmpegVersion -ne $FfmpegVersion) { throw "Packaged FFmpeg version changed" }
if ($PackagedFfprobeVersion -ne $FfprobeVersion) { throw "Packaged ffprobe version changed" }
if ($PackagedAria2Version -ne $Aria2Version) { throw "Packaged aria2c version changed" }
if ($PackagedDenoVersion -ne $DenoVersion) { throw "Packaged Deno version changed" }

$ExeHashValue = (
    Get-FileHash `
        -LiteralPath $StandaloneExe `
        -Algorithm SHA256
).Hash
$ZipHashValue = (
    Get-FileHash `
        -LiteralPath $ZipPath `
        -Algorithm SHA256
).Hash
$ZipExeHashValue = (
    Get-FileHash `
        -LiteralPath (Join-Path $VerifyRoot "Youtube.Downloaderbs.exe") `
        -Algorithm SHA256
).Hash
if ($ExeHashValue -ne $ZipExeHashValue) { throw "Standalone EXE differs from the EXE inside the ZIP" }

$PackagedFfmpegHash = Get-FileHash -LiteralPath (Join-Path $VerifyRoot "data\bin\ffmpeg.exe") -Algorithm SHA256
$PackagedFfprobeHash = Get-FileHash -LiteralPath (Join-Path $VerifyRoot "data\bin\ffprobe.exe") -Algorithm SHA256
if ($PackagedFfmpegHash.Hash -ne $FfmpegSha256) { throw "Packaged FFmpeg hash changed" }
if ($PackagedFfprobeHash.Hash -ne $FfprobeSha256) { throw "Packaged ffprobe hash changed" }

$ExeInfo = Get-Item -LiteralPath $StandaloneExe
$ZipInfo = Get-Item -LiteralPath $ZipPath
$BuildCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not determine build commit" }

$NotesPath = Join-Path $ReleaseRoot "RELEASE_NOTES.md"
Copy-Item -LiteralPath (Join-Path $RepoRoot "docs\release_notes_v1.3.0.md") -Destination $NotesPath
$ChecksumLines = @(
    "",
    "## Build checksums",
    "",
    ('- `{0}`: `{1}`' -f "Youtube.Downloaderbs.exe", $ExeHashValue),
    ('- `{0}`: `{1}`' -f $ZipName, $ZipHashValue)
)
$ChecksumLines | Add-Content -LiteralPath $NotesPath -Encoding utf8

$ManifestPath = Join-Path $ReleaseRoot "BUILD_MANIFEST.txt"
@"
release_tag: $ReleaseTag
build_commit: $BuildCommit
zip_name: $ZipName
zip_size: $($ZipInfo.Length)
zip_sha256: $ZipHashValue
exe_name: Youtube.Downloaderbs.exe
exe_size: $($ExeInfo.Length)
exe_sha256: $ExeHashValue
yt_dlp_version: $YtDlpVersion
ffmpeg_version: $FfmpegVersion
ffmpeg_sha256: $($PackagedFfmpegHash.Hash)
ffprobe_version: $FfprobeVersion
ffprobe_sha256: $($PackagedFfprobeHash.Hash)
aria2_version: $Aria2Version
deno_version: $DenoVersion
portable_manifest_files: $($RequiredFiles.Count)
"@ | Set-Content -LiteralPath $ManifestPath -Encoding utf8

Write-Host "EXE: $($ExeInfo.Name) | $($ExeInfo.Length) bytes | SHA256 $ExeHashValue"
Write-Host "ZIP: $($ZipInfo.Name) | $($ZipInfo.Length) bytes | SHA256 $ZipHashValue"
Write-Host "Stable release assets are ready in release/assets"
