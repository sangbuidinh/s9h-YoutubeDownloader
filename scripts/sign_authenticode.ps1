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
    [string]$ProviderConfigPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$TimestampUrl,

    [ValidateSet("synthetic", "production")]
    [string]$SigningPurpose,

    [string]$ReleaseAssurancePolicyPath,
    [string]$ExpectedPublisher,
    [string]$CertificateThumbprint,
    [string]$SignToolPath = "signtool.exe",
    [switch]$PlanOnly
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
        throw "Signing target is missing or is not a file"
    }

    $RootItem = Get-Item -LiteralPath $AuthorizedRoot -Force
    $TargetItem = Get-Item -LiteralPath $TargetPath -Force
    if (($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Authorized release root must not be a reparse point"
    }
    if (($TargetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Signing target must not be a reparse point"
    }

    $RootPath = [IO.Path]::GetFullPath($RootItem.FullName).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $CanonicalTarget = [IO.Path]::GetFullPath($TargetItem.FullName)
    $RootPrefix = $RootPath + [IO.Path]::DirectorySeparatorChar
    if (-not $CanonicalTarget.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Signing target is outside the authorized release root"
    }

    $Cursor = $TargetItem.Directory
    while ($null -ne $Cursor) {
        $CursorPath = [IO.Path]::GetFullPath($Cursor.FullName).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if (($Cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Signing target path traverses a reparse point"
        }
        if ([string]::Equals($CursorPath, $RootPath, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        if (-not $CursorPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Signing target parent escaped the authorized release root"
        }
        $Cursor = $Cursor.Parent
    }
    if ($null -eq $Cursor) {
        throw "Signing target is not rooted in the authorized release directory"
    }

    if ($VendorNames -contains $TargetItem.Name) {
        throw "Vendor-supplied binaries must not be re-signed"
    }
    if ($TargetItem.Name -cne $FirstPartyName) {
        throw "Only the canonical first-party executable may be signed"
    }
    if ($TargetItem.Length -le 0) {
        throw "Signing target is empty"
    }

    return [PSCustomObject]@{
        RootPath = $RootPath
        TargetPath = $CanonicalTarget
    }
}

function Assert-UnsignedPeStructure {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Stream = [IO.File]::OpenRead($Path)
    $Reader = New-Object IO.BinaryReader($Stream)
    try {
        if ($Stream.Length -lt 256 -or $Reader.ReadUInt16() -ne 0x5A4D) {
            throw "Unsigned target is not a Windows PE executable"
        }
        $Stream.Position = 0x3C
        $PeOffset = $Reader.ReadInt32()
        if ($PeOffset -lt 0x40 -or $PeOffset -gt ($Stream.Length - 160)) {
            throw "Unsigned target has an invalid PE header offset"
        }
        $Stream.Position = $PeOffset
        if ($Reader.ReadUInt32() -ne 0x00004550) {
            throw "Unsigned target has an invalid PE signature"
        }

        $OptionalHeaderOffset = $PeOffset + 24
        $Stream.Position = $OptionalHeaderOffset
        $Magic = $Reader.ReadUInt16()
        if ($Magic -eq 0x20B) {
            $NumberOfDirectoriesOffset = $OptionalHeaderOffset + 108
            $CertificateDirectoryOffset = $OptionalHeaderOffset + 144
        }
        elseif ($Magic -eq 0x10B) {
            $NumberOfDirectoriesOffset = $OptionalHeaderOffset + 92
            $CertificateDirectoryOffset = $OptionalHeaderOffset + 128
        }
        else {
            throw "Unsigned target has an unsupported PE optional header"
        }
        if ($CertificateDirectoryOffset + 8 -gt $Stream.Length) {
            throw "Unsigned target has a truncated PE optional header"
        }

        $Stream.Position = $NumberOfDirectoriesOffset
        $DirectoryCount = $Reader.ReadUInt32()
        if ($DirectoryCount -gt 4) {
            $Stream.Position = $CertificateDirectoryOffset
            $CertificateOffset = $Reader.ReadUInt32()
            $CertificateSize = $Reader.ReadUInt32()
            if ($CertificateOffset -ne 0 -or $CertificateSize -ne 0) {
                throw "Signing target already contains an Authenticode certificate table"
            }
        }
    }
    finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Read-ProviderConfig {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Provider configuration is missing"
    }
    $Raw = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Path), [Text.UTF8Encoding]::new($false))
    if ($Raw -match "`r" -or $Raw -match "-----BEGIN .*PRIVATE KEY-----") {
        throw "Provider configuration failed hygiene validation"
    }
    $Config = $Raw | ConvertFrom-Json
    if ($Config.provider.id -ne "ssl-com-esigner" -or
        $Config.provider.service -ne "SSL.com eSigner" -or
        $Config.custody.model -ne "provider-managed cloud HSM" -or
        $Config.custody.private_key_export_allowed -ne $false -or
        $Config.custody.repository_pfx_allowed -ne $false -or
        $Config.custody.repository_private_key_allowed -ne $false -or
        $Config.signing_contract.first_party_targets.Count -ne 1 -or
        $Config.signing_contract.first_party_targets[0] -ne $FirstPartyName -or
        $Config.signing_contract.plan_only_supported -ne $true -or
        ($Config.signing_contract.real_signing_purposes -join ",") -cne "synthetic,production" -or
        $Config.signing_contract.file_digest -ne "SHA256" -or
        $Config.signing_contract.timestamp_protocol -ne "RFC3161" -or
        $Config.signing_contract.timestamp_digest -ne "SHA256" -or
        $Config.signing_contract.verification_switch -ne "/pa") {
        throw "Provider configuration does not satisfy the signing contract"
    }
    return $Config
}

function Read-ReleaseAssurancePolicy {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Production signing requires an explicit release-assurance policy"
    }
    $Raw = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Path), [Text.UTF8Encoding]::new($false))
    if ($Raw -match "`r" -or $Raw -match "-----BEGIN .*PRIVATE KEY-----") {
        throw "Release-assurance policy failed hygiene validation"
    }
    $Policy = $Raw | ConvertFrom-Json
    if ($Policy.policy_id -ne "s9h-release-assurance-v1" -or
        $Policy.product -ne "Youtube Downloaderbs" -or
        $Policy.schema_version -ne 2) {
        throw "Production release-assurance policy identity is invalid"
    }
    return $Policy
}

function Normalize-CertificateThumbprint {
    param(
        [Parameter(Mandatory = $true)][string]$Value
    )

    $Normalized = ($Value -replace "\s", "").ToUpperInvariant()
    if ($Normalized -notmatch "^[0-9A-F]{40}$") {
        throw "The provisioned certificate selector is invalid"
    }
    return $Normalized
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
Assert-UnsignedPeStructure -Path $Authorized.TargetPath
$Config = Read-ProviderConfig -Path $ProviderConfigPath

$TimestampUri = $null
if (-not [Uri]::TryCreate($TimestampUrl, [UriKind]::Absolute, [ref]$TimestampUri) -or
    $TimestampUri.Scheme -notin @("http", "https")) {
    throw "Timestamp URL must be an absolute HTTP or HTTPS RFC 3161 endpoint"
}
if ($TimestampUrl -cne $Config.timestamp.candidate_url) {
    throw "Timestamp URL does not match the reviewed provider configuration"
}

if ($PlanOnly) {
    $Plan = [ordered]@{
        mode = "plan-only"
        provider = "ssl-com-esigner"
        target = $FirstPartyName
        sign_command = "signtool.exe sign /fd SHA256 /tr <approved-rfc3161-url> /td SHA256 /sha1 <redacted-certificate-selector> Youtube.Downloaderbs.exe"
        verify_command = "signtool.exe verify /pa /all /v /tw Youtube.Downloaderbs.exe"
        expected_publisher = "<required-after-provisioning>"
        invokes_signtool = $false
    }
    Write-Output ($Plan | ConvertTo-Json -Compress)
    exit 0
}

if ([string]::IsNullOrWhiteSpace($SigningPurpose)) {
    throw "A real signing purpose must be explicitly selected"
}

if ($Config.state.account_provisioned -ne $true -or
    $Config.state.certificate_provisioned -ne $true -or
    $Config.state.credential_source_configured -ne $true -or
    $Config.state.synthetic_signing_authorized -ne $true -or
    $Config.state.timestamp_authority_approved -ne $true -or
    $Config.timestamp.authority_approved -ne $true) {
    throw "Synthetic signing prerequisites are not satisfied"
}
if ([string]::IsNullOrWhiteSpace($ExpectedPublisher) -or
    $ExpectedPublisher -cne $Config.certificate.expected_publisher) {
    throw "Expected publisher is required and must match the provisioned policy"
}
if ([string]::IsNullOrWhiteSpace($CertificateThumbprint) -or
    [string]::IsNullOrWhiteSpace($Config.certificate.expected_thumbprint)) {
    throw "An explicit provisioned certificate selector is required"
}
$NormalizedThumbprint = Normalize-CertificateThumbprint -Value $CertificateThumbprint
$ExpectedThumbprint = Normalize-CertificateThumbprint -Value $Config.certificate.expected_thumbprint
if ($NormalizedThumbprint -cne $ExpectedThumbprint) {
    throw "The selected certificate does not match the provisioned policy"
}

if ($SigningPurpose -ceq "production") {
    if ($Config.state.production_signing_authorized -ne $true) {
        throw "Production signing authorization is not satisfied"
    }
    if ($Config.state.remote_signing_validated -ne $true) {
        throw "Remote synthetic signing validation is not satisfied"
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseAssurancePolicyPath)) {
        throw "Production signing requires an explicit release-assurance policy"
    }
    $ReleasePolicy = Read-ReleaseAssurancePolicy -Path $ReleaseAssurancePolicyPath
    $RequiredProductionGates = @(
        "assembly_authorized",
        "legal_compliance_certified",
        "release_gate_reconsideration_allowed",
        "source_assets_created",
        "source_availability_certified",
        "source_kits_ready"
    )
    foreach ($Gate in $RequiredProductionGates) {
        if ($ReleasePolicy.release_integration.existing_gate_invariants.$Gate -ne $true) {
            throw "An independent production release gate is not satisfied"
        }
    }
}

$ResolvedSignTool = Resolve-SignTool -Path $SignToolPath
$SignToolPath = $ResolvedSignTool
$SignArguments = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256", "/sha1", $NormalizedThumbprint, $Authorized.TargetPath)
& $SignToolPath @SignArguments | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Authenticode signing failed"
}

$VerifyScript = Join-Path $PSScriptRoot "verify_authenticode_signature.ps1"
if (-not (Test-Path -LiteralPath $VerifyScript -PathType Leaf)) {
    throw "Authenticode verification script is missing"
}
& $VerifyScript `
    -Target $Authorized.TargetPath `
    -ReleaseRoot $Authorized.RootPath `
    -SignToolPath $SignToolPath `
    -ExpectedPublisher $ExpectedPublisher `
    -CertificateThumbprint $NormalizedThumbprint
if ($LASTEXITCODE -ne 0) {
    throw "Authenticode verification failed after signing"
}
