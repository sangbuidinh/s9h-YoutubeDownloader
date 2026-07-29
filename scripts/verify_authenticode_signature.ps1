[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ReleaseRoot,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExpectedPublisher,

    [string]$SignToolPath = "signtool.exe"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$FirstPartyName = "Youtube.Downloaderbs.exe"
$VendorNames = @(
    "yt-dlp.exe",
    "ffmpeg.exe",
    "ffprobe.exe",
    "aria2c.exe",
    "deno.exe"
)

function Assert-AuthorizedTarget {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$AuthorizedRoot
    )

    if (-not (Test-Path -LiteralPath $AuthorizedRoot -PathType Container)) {
        throw "Authorized release root is missing"
    }
    if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
        throw "Verification target is missing or is not a file"
    }

    $RootItem = Get-Item -LiteralPath $AuthorizedRoot -Force
    $TargetItem = Get-Item -LiteralPath $TargetPath -Force
    if (($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($TargetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Verification path must not use a reparse point"
    }

    $RootPath = [IO.Path]::GetFullPath($RootItem.FullName).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $CanonicalTarget = [IO.Path]::GetFullPath($TargetItem.FullName)
    $RootPrefix = $RootPath + [IO.Path]::DirectorySeparatorChar
    if (-not $CanonicalTarget.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Verification target is outside the authorized release root"
    }

    $Cursor = $TargetItem.Directory
    while ($null -ne $Cursor) {
        $CursorPath = [IO.Path]::GetFullPath($Cursor.FullName).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if (($Cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Verification target path traverses a reparse point"
        }
        if ([string]::Equals($CursorPath, $RootPath, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        if (-not $CursorPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Verification target parent escaped the authorized release root"
        }
        $Cursor = $Cursor.Parent
    }
    if ($null -eq $Cursor) {
        throw "Verification target is not rooted in the authorized release directory"
    }

    if ($VendorNames -contains $TargetItem.Name) {
        throw "Vendor-supplied binaries must not be verified as first-party signatures"
    }
    if ($TargetItem.Name -cne $FirstPartyName) {
        throw "Only the canonical first-party executable may be verified"
    }
    if ($TargetItem.Length -le 0) {
        throw "Verification target is empty"
    }

    return [PSCustomObject]@{
        RootPath = $RootPath
        TargetPath = $CanonicalTarget
    }
}

function Resolve-SignTool {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return [IO.Path]::GetFullPath((Get-Item -LiteralPath $Path).FullName)
    }
    $Command = Get-Command $Path -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
        throw "SignTool is unavailable"
    }
    return $Command.Source
}

$Authorized = Assert-AuthorizedTarget -TargetPath $Target -AuthorizedRoot $ReleaseRoot
$SignToolPath = Resolve-SignTool -Path $SignToolPath
$VerifyArguments = @("verify", "/pa", "/all", "/v", "/tw", $Authorized.TargetPath)
$VerifyOutput = @(& $SignToolPath @VerifyArguments 2>&1 | ForEach-Object { [string]$_ })
$VerifyExitCode = $LASTEXITCODE
if ($VerifyExitCode -ne 0) {
    throw "Default Authenticode verification failed"
}

$VerifyText = $VerifyOutput -join "`n"
if ($VerifyText -notmatch "(?i)Successfully verified") {
    throw "SignTool did not confirm successful Authenticode verification"
}
if ($VerifyText -notmatch "(?i)Timestamp Verified by") {
    throw "Authenticode timestamp is missing or invalid"
}
if ($VerifyText -notmatch "(?i)RFC3161") {
    throw "Authenticode timestamp is not confirmed as RFC 3161"
}
if ($VerifyText -notmatch "(?i)SHA256") {
    throw "Authenticode signature or timestamp digest is not confirmed as SHA-256"
}
if ($VerifyText.IndexOf($ExpectedPublisher, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw "SignTool output does not match the expected publisher"
}

$Signature = Get-AuthenticodeSignature -LiteralPath $Authorized.TargetPath
if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "PowerShell Authenticode verification did not return Valid"
}
if ($null -eq $Signature.SignerCertificate -or
    $Signature.SignerCertificate.Subject.IndexOf(
        $ExpectedPublisher,
        [StringComparison]::OrdinalIgnoreCase
    ) -lt 0) {
    throw "Authenticode signer identity does not match the expected publisher"
}
if ($null -eq $Signature.TimeStamperCertificate) {
    throw "Authenticode timestamp certificate is missing"
}

$FileSha256 = (Get-FileHash -LiteralPath $Authorized.TargetPath -Algorithm SHA256).Hash.ToLowerInvariant()
$Result = [ordered]@{
    target = $FirstPartyName
    sha256 = $FileSha256
    publisher = $ExpectedPublisher
    verification_policy = "Default Authenticode /pa"
    timestamp_protocol = "RFC3161"
    timestamp_digest = "SHA256"
    timestamp_verified = $true
    downstream_packaging_allowed_for_recorded_hash = $true
}
Write-Output ($Result | ConvertTo-Json -Compress)
