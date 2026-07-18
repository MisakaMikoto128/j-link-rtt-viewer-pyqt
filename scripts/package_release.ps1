#requires -Version 5.1
<#
.SYNOPSIS
  One-command packaging: Nuitka build (standalone + onefile) -> organize
  artifacts into a per-version folder.

.DESCRIPTION
  Output layout (dist/):
    dist/<version>-<detail>-win64/
      JLinkRTTViewer-v<ver>-<detail>-win64.exe   onefile binary
      JLinkRTTViewer-v<ver>-<detail>-win64.zip   standalone, max-compressed
      JLinkRTTViewer-v<ver>-<detail>-win64/      standalone, uncompressed (for testing)

  Version is auto-detected from git:
    - On a tag:              v0.6.0          -> version 0.6.0, detail "release"
    - After a tag (dev):     v0.6.0-16-g3c4c568 -> version 0.6.0, detail "dev.16.g3c4c568"
  Override with -Version / -Detail.

  7-Zip (tools/7z.exe or PATH or C:\Program Files\7-Zip) is used for max
  compression when available; otherwise falls back to Compress-Archive.

  Overwrite policy: an artifact is regenerated when it is missing OR when its
  build source (build/main.dist resp. build/onefile exe) is newer - so a rerun
  after a fresh build refreshes dist, while a rerun without rebuilding is a
  cheap no-op. Manually delete dist/ subfolders to force regeneration.

.EXAMPLE
  ./scripts/package_release.ps1                 # build + package both
  ./scripts/package_release.ps1 -SkipBuild      # package existing build output
  ./scripts/package_release.ps1 -SkipStandalone # only onefile
  ./scripts/package_release.ps1 -Version 0.6.0 -Detail test1
#>
param(
    [string]$Version = "",
    [string]$Detail = "",
    [switch]$SkipBuild,
    [switch]$SkipStandalone,
    [switch]$SkipOnefile
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# ---- Version / detail detection from git -----------------------------------
if (-not $Version) {
    $desc = (git describe --tags 2>$null)
    if (-not $desc) {
        # No tags at all: fall back to pyproject.toml
        $pyproject = Get-Content "pyproject.toml" -Raw
        if ($pyproject -match 'version\s*=\s*"([^"]+)"') {
            $Version = $Matches[1]
            if (-not $Detail) { $Detail = "untagged.g$(git rev-parse --short HEAD)" }
        } else {
            throw "cannot determine version: no git tags and no pyproject.toml version"
        }
    } elseif ($desc -match '^v?(\d+\.\d+\.\d+)$') {
        $Version = $Matches[1]
        if (-not $Detail) { $Detail = "release" }
    } elseif ($desc -match '^v?(\d+\.\d+\.\d+)-(\d+)-g([0-9a-f]+)$') {
        $Version = $Matches[1]
        if (-not $Detail) { $Detail = "dev.$($Matches[2]).g$($Matches[3])" }
    } else {
        throw "cannot parse git describe output: $desc"
    }
}
if (-not $Detail) { $Detail = "release" }

$baseName = "JLinkRTTViewer-v$Version-$Detail-win64"
$outDir   = "dist/$baseName"

# ---- 7-Zip detection --------------------------------------------------------
$sevenZip = $null
foreach ($candidate in @("$repoRoot/tools/7z.exe", "C:\Program Files\7-Zip\7z.exe")) {
    if (Test-Path $candidate) { $sevenZip = $candidate; break }
}
if (-not $sevenZip) {
    $cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($cmd) { $sevenZip = $cmd.Source }
}

Write-Host "== package_release: $baseName" -ForegroundColor Cyan
if ($sevenZip) { Write-Host "   zip: 7-Zip ultra ($sevenZip)" -ForegroundColor DarkGray }
else           { Write-Host "   zip: Compress-Archive fallback (install 7-Zip for max compression)" -ForegroundColor DarkGray }

# ---- Build ------------------------------------------------------------------
if ($SkipBuild) {
    Write-Host "[1/4] skip build (-SkipBuild)" -ForegroundColor Yellow
} else {
    Write-Host "[1/4] Nuitka build (standalone + onefile)" -ForegroundColor Cyan
    if (-not $SkipStandalone) { cmd /c .\build_nuitka.bat;         if ($LASTEXITCODE -ne 0) { throw "build_nuitka.bat failed" } }
    if (-not $SkipOnefile)    { cmd /c .\build_nuitka_onefile.bat; if ($LASTEXITCODE -ne 0) { throw "build_nuitka_onefile.bat failed" } }
}

# ---- Prepare output dir ------------------------------------------------------
# Overwrite policy: an artifact is (re)generated when missing OR when its build
# source is newer (fresh build => refresh dist). Rerun without rebuild = no-op.
Write-Host "[2/4] prepare $outDir" -ForegroundColor Cyan
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }

function Test-Stale([string]$artifact, [string]$source) {
    if (-not (Test-Path $artifact)) { return $true }
    if (-not (Test-Path $source)) { return $false }
    return (Get-Item $source).LastWriteTime -gt (Get-Item $artifact).LastWriteTime
}

function Write-Artifact([string]$path, [string]$source, [scriptblock]$produce) {
    if (Test-Stale $path $source) {
        if (Test-Path $path) { Write-Host "   rebuild (source newer): $path" -ForegroundColor Yellow }
        & $produce
        $size = [math]::Round((Get-Item $path).Length / 1MB, 1)
        Write-Host "   OK: $path ($size MB)" -ForegroundColor Green
    } else {
        Write-Host "   keep existing: $path" -ForegroundColor Yellow
    }
}

# ---- Onefile exe --------------------------------------------------------------
if (-not $SkipOnefile) {
    Write-Host "[3/4] onefile exe" -ForegroundColor Cyan
    $srcExe = "build/onefile/JLinkRTTViewer.exe"
    if (-not (Test-Path $srcExe)) { throw "missing $srcExe - run without -SkipBuild first" }
    Write-Artifact "$outDir/$baseName.exe" $srcExe { Copy-Item $srcExe "$outDir/$baseName.exe" }
}

# ---- Standalone: uncompressed dir + zip --------------------------------------
if (-not $SkipStandalone) {
    Write-Host "[4/4] standalone dir + zip" -ForegroundColor Cyan
    $distDir = "build/main.dist"
    if (-not (Test-Path "$distDir/JLinkRTTViewer.exe")) { throw "missing $distDir/JLinkRTTViewer.exe - run without -SkipBuild first" }
    $stageDir = "$outDir/$baseName"
    $zipPath  = "$outDir/$baseName.zip"
    $srcMarker = "$distDir/JLinkRTTViewer.exe"

    if (Test-Stale "$stageDir/JLinkRTTViewer.exe" $srcMarker) {
        # dir judged by its inner exe timestamp vs build source
        if (Test-Path $stageDir) {
            Write-Host "   rebuild dir (source newer): $stageDir" -ForegroundColor Yellow
            Remove-Item -Recurse -Force $stageDir
        }
        Copy-Item -Recurse $distDir $stageDir
        Write-Host "   OK: $stageDir/" -ForegroundColor Green
    } else {
        Write-Host "   keep existing dir: $stageDir" -ForegroundColor Yellow
    }

    Write-Artifact $zipPath "$stageDir/JLinkRTTViewer.exe" {
        if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
        if ($sevenZip) {
            Push-Location $outDir
            try {
                & $sevenZip a -tzip -mx=9 -mfb=258 -mpass=15 "$baseName.zip" "$baseName" | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "7z failed (exit $LASTEXITCODE)" }
            } finally { Pop-Location }
        } else {
            Compress-Archive -Path $stageDir -DestinationPath $zipPath -CompressionLevel Optimal
        }
    }
}

Write-Host "`nDone: $outDir" -ForegroundColor Green
Get-ChildItem $outDir | ForEach-Object {
    $s = if ($_.PSIsContainer) { "<dir>" } else { "{0,8:N1} MB" -f ($_.Length / 1MB) }
    Write-Host ("  {0}  {1}" -f $s, $_.Name)
}
