[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$InstallerPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$AllowedRoot,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ProviderPolicyPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-InstallerPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "Installer allowed root is missing or is not a directory"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "CKA installer is missing or is not a file"
    }

    $RootItem = Get-Item -LiteralPath $Root -Force
    $InstallerItem = Get-Item -LiteralPath $Path -Force
    if (($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Installer allowed root must not be a reparse point"
    }
    if (($InstallerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "CKA installer must not be a reparse point"
    }

    $RootPath = [IO.Path]::GetFullPath($RootItem.FullName).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $CanonicalInstaller = [IO.Path]::GetFullPath($InstallerItem.FullName)
    $RootPrefix = $RootPath + [IO.Path]::DirectorySeparatorChar
    if (-not $CanonicalInstaller.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "CKA installer is outside the allowed root"
    }

    $Cursor = $InstallerItem.Directory
    while ($null -ne $Cursor) {
        $CursorPath = [IO.Path]::GetFullPath($Cursor.FullName).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if (($Cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "CKA installer path traverses a reparse point"
        }
        if ([string]::Equals($CursorPath, $RootPath, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        if (-not $CursorPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "CKA installer parent escaped the allowed root"
        }
        $Cursor = $Cursor.Parent
    }
    if ($null -eq $Cursor) {
        throw "CKA installer is not rooted in the allowed directory"
    }

    return [PSCustomObject]@{
        FullName = $CanonicalInstaller
        Length = $InstallerItem.Length
        VersionInfo = $InstallerItem.VersionInfo
    }
}

function Read-PackageIdentity {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Authenticode provider policy is missing"
    }
    $Raw = [IO.File]::ReadAllText(
        [IO.Path]::GetFullPath($Path),
        [Text.UTF8Encoding]::new($false)
    )
    if ($Raw.StartsWith([char]0xFEFF) -or $Raw -match "`r" -or
        $Raw -match "-----BEGIN .*PRIVATE KEY-----") {
        throw "Authenticode provider policy failed hygiene validation"
    }
    $Policy = $Raw | ConvertFrom-Json
    if ($Policy.policy_id -cne "s9h-authenticode-provider-v1" -or
        $Policy.official_evidence.cka.cka_package_integrated -ne $true -or
        $Policy.official_evidence.cka.immutable_package_identity_established -ne $true) {
        throw "Authenticode provider policy does not contain an accepted CKA identity"
    }
    return $Policy.official_evidence.cka.package_identity
}

function Get-PeArchitecture {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = [IO.File]::OpenRead($Path)
    $Reader = [IO.BinaryReader]::new($Stream)
    try {
        if ($Stream.Length -lt 256 -or $Reader.ReadUInt16() -ne 0x5A4D) {
            throw "CKA installer is not a Windows PE executable"
        }
        $Stream.Position = 0x3C
        $PeOffset = $Reader.ReadInt32()
        if ($PeOffset -lt 0x40 -or $PeOffset -gt ($Stream.Length - 24)) {
            throw "CKA installer has an invalid PE header offset"
        }
        $Stream.Position = $PeOffset
        if ($Reader.ReadUInt32() -ne 0x00004550) {
            throw "CKA installer has an invalid PE signature"
        }
        $Machine = $Reader.ReadUInt16()
        switch ($Machine) {
            0x014C { return "x86" }
            0x8664 { return "x64" }
            default { throw "CKA installer has an unsupported PE architecture" }
        }
    }
    finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Normalize-HexIdentity {
    param([Parameter(Mandatory = $true)][string]$Value)
    return ($Value -replace "\s", "").ToUpperInvariant()
}

$Authorized = Assert-InstallerPath -Path $InstallerPath -Root $AllowedRoot
$Expected = Read-PackageIdentity -Path $ProviderPolicyPath

if ([IO.Path]::GetFileName($Authorized.FullName) -cne [string]$Expected.resource_display_filename) {
    throw "CKA installer filename does not match the pinned official resource identity"
}
if ($Authorized.Length -ne [long]$Expected.byte_size) {
    throw "CKA installer byte size does not match the pinned identity"
}
$ActualSha256 = (Get-FileHash -LiteralPath $Authorized.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -cne [string]$Expected.sha256) {
    throw "CKA installer SHA-256 does not match the pinned identity"
}
$Architecture = Get-PeArchitecture -Path $Authorized.FullName
if ($Architecture -cne [string]$Expected.architecture) {
    throw "CKA installer architecture does not match the pinned identity"
}

$Signature = Get-AuthenticodeSignature -LiteralPath $Authorized.FullName
if ($Signature.Status.ToString() -cne [string]$Expected.authenticode_status_required) {
    throw "CKA installer Authenticode status is not valid"
}
if ($null -eq $Signature.SignerCertificate) {
    throw "CKA installer signer certificate is missing"
}
$Signer = $Signature.SignerCertificate
$SignerSimpleName = $Signer.GetNameInfo(
    [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
    $false
)
if (-not [string]::Equals($SignerSimpleName, [string]$Expected.signer_simple_name, [StringComparison]::Ordinal)) {
    throw "CKA installer signer SimpleName does not match the pinned identity"
}
if ((Normalize-HexIdentity -Value $Signer.SerialNumber) -cne
    (Normalize-HexIdentity -Value ([string]$Expected.signer_serial))) {
    throw "CKA installer signer serial does not match the pinned identity"
}
if ((Normalize-HexIdentity -Value $Signer.Thumbprint) -cne
    (Normalize-HexIdentity -Value ([string]$Expected.signer_thumbprint))) {
    throw "CKA installer signer thumbprint does not match the pinned identity"
}
if (-not [string]::Equals($Signer.Issuer, [string]$Expected.signer_issuer, [StringComparison]::Ordinal)) {
    throw "CKA installer signer issuer does not match the pinned identity"
}

$FileVersion = ([string]$Authorized.VersionInfo.FileVersion).Trim()
$ProductVersion = ([string]$Authorized.VersionInfo.ProductVersion).Trim()
$ProductName = ([string]$Authorized.VersionInfo.ProductName).Trim()
if ($FileVersion -cne [string]$Expected.file_version) {
    throw "CKA installer FileVersion does not match the pinned identity"
}
if ($ProductVersion -cne [string]$Expected.product_version) {
    throw "CKA installer ProductVersion does not match the pinned identity"
}
if ($ProductName -cne [string]$Expected.product_name) {
    throw "CKA installer ProductName does not match the pinned identity"
}
if ($Expected.timestamp_certificate_required -ne $true -or
    $null -eq $Signature.TimeStamperCertificate) {
    throw "CKA installer timestamp certificate is missing"
}

$Result = [ordered]@{
    architecture = $Architecture
    authenticode_status = "Valid"
    filename = [IO.Path]::GetFileName($Authorized.FullName)
    file_version = $FileVersion
    product_name = $ProductName
    product_version = $ProductVersion
    sha256 = $ActualSha256
    signer = $SignerSimpleName
    size = $Authorized.Length
    timestamp_certificate_present = $true
}
Write-Output ($Result | ConvertTo-Json -Compress)
