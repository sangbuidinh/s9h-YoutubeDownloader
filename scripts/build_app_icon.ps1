param(
    [string]$SourcePath = "assets/app_icon_source.png",
    [string]$PngPath = "assets/app_icon.png",
    [string]$IcoPath = "assets/app_icon.ico"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing

function New-ResizedPngBytes {
    param(
        [System.Drawing.Image]$Image,
        [int]$Size
    )

    $bitmap = New-Object System.Drawing.Bitmap $Size, $Size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.DrawImage($Image, 0, 0, $Size, $Size)

        $stream = New-Object System.IO.MemoryStream
        try {
            $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
            return $stream.ToArray()
        }
        finally {
            $stream.Dispose()
        }
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Write-MultiSizeIco {
    param(
        [System.Drawing.Image]$Image,
        [string]$Path,
        [int[]]$Sizes
    )

    $images = @()
    foreach ($size in $Sizes) {
        $images += ,@{
            Size = $size
            Data = New-ResizedPngBytes -Image $Image -Size $size
        }
    }

    $stream = New-Object System.IO.FileStream $Path, ([System.IO.FileMode]::Create), ([System.IO.FileAccess]::Write)
    $writer = New-Object System.IO.BinaryWriter $stream
    try {
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]$images.Count)

        $offset = 6 + (16 * $images.Count)
        foreach ($entry in $images) {
            $sizeByte = if ($entry.Size -eq 256) { 0 } else { $entry.Size }
            $writer.Write([byte]$sizeByte)
            $writer.Write([byte]$sizeByte)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([UInt16]1)
            $writer.Write([UInt16]32)
            $writer.Write([UInt32]$entry.Data.Length)
            $writer.Write([UInt32]$offset)
            $offset += $entry.Data.Length
        }

        foreach ($entry in $images) {
            $writer.Write([byte[]]$entry.Data)
        }
    }
    finally {
        $writer.Dispose()
        $stream.Dispose()
    }
}

function New-TransparentMotifBitmap {
    param(
        [System.Drawing.Image]$Source,
        [System.Drawing.Rectangle]$CropRect,
        [int]$Size
    )

    $bitmap = New-Object System.Drawing.Bitmap $Size, $Size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.DrawImage($Source, (New-Object System.Drawing.Rectangle 0, 0, $Size, $Size), $CropRect, [System.Drawing.GraphicsUnit]::Pixel)
    }
    finally {
        $graphics.Dispose()
    }

    for ($y = 0; $y -lt $Size; $y++) {
        for ($x = 0; $x -lt $Size; $x++) {
            $color = $bitmap.GetPixel($x, $y)
            $r = [int]$color.R
            $g = [int]$color.G
            $b = [int]$color.B
            $maxOther = [Math]::Max($g, $b)
            $redScore = $r - $maxOther
            $whiteSpread = [Math]::Max([Math]::Abs($r - $g), [Math]::Abs($r - $b))

            $isWhiteTriangle = $r -ge 185 -and $g -ge 185 -and $b -ge 175 -and $whiteSpread -le 80
            $isRedMotif = $r -ge 85 -and $redScore -ge 35

            if (-not ($isWhiteTriangle -or $isRedMotif)) {
                $bitmap.SetPixel($x, $y, [System.Drawing.Color]::FromArgb(0, $r, $g, $b))
                continue
            }

            $alpha = 255
            if ($isRedMotif -and -not $isWhiteTriangle) {
                $alphaFromRed = [Math]::Min(255, [Math]::Max(0, [int][Math]::Round(($r - 70) * 3.2)))
                $alphaFromScore = [Math]::Min(255, [Math]::Max(0, ($redScore - 32) * 9))
                $alpha = [Math]::Min($alphaFromRed, $alphaFromScore)
                if ($alpha -lt 24) {
                    $alpha = 0
                }
            }

            $bitmap.SetPixel($x, $y, [System.Drawing.Color]::FromArgb($alpha, $r, $g, $b))
        }
    }

    return $bitmap
}

$sourceFullPath = (Resolve-Path $SourcePath).Path
$pngFullPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PngPath)
$icoFullPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($IcoPath)

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pngFullPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $icoFullPath) | Out-Null

$source = [System.Drawing.Image]::FromFile($sourceFullPath)
try {
    $baseSide = [Math]::Min($source.Width, $source.Height)
    $cropSide = [int][Math]::Round($baseSide * 0.805)
    $cropX = [int][Math]::Round(($source.Width - $cropSide) / 2)
    $cropY = [int][Math]::Round($source.Height * 0.09)

    if (($cropY + $cropSide) -gt $source.Height) {
        $cropY = $source.Height - $cropSide
    }
    if ($cropY -lt 0) {
        $cropY = 0
    }

    $cropRect = New-Object System.Drawing.Rectangle $cropX, $cropY, $cropSide, $cropSide
    $cropped = New-TransparentMotifBitmap -Source $source -CropRect $cropRect -Size 1024
    try {
        $cropped.Save($pngFullPath, [System.Drawing.Imaging.ImageFormat]::Png)
        Write-MultiSizeIco -Image $cropped -Path $icoFullPath -Sizes @(16, 24, 32, 48, 64, 128, 256)
    }
    finally {
        $cropped.Dispose()
    }
}
finally {
    $source.Dispose()
}

Write-Host "source: $sourceFullPath"
Write-Host "png: $pngFullPath"
Write-Host "ico: $icoFullPath"
