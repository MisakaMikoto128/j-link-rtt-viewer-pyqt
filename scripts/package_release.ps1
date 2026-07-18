#requires -Version 5.1
<#
.SYNOPSIS
  Nuitka build + package, one command. Artifacts land in build/dist/<version>/.

.DESCRIPTION
  Run with NO arguments for an interactive menu (arrow keys, remembers your
  last choice in scripts/.package_release.prefs). Run WITH arguments for
  scripted/agent use - the menu is skipped entirely.

  Output layout (build/dist/):
    build/dist/<basename>/
      <basename>.exe    onefile binary
      <basename>.zip    standalone, max-compressed (7-Zip if available)
      <basename>/       standalone, uncompressed (for testing)

  Basename contains the git-derived version and detail, e.g.
    JLinkRTTViewer-v0.6.0-dev.16.g3c4c56-win64

  Version is auto-detected from git describe:
    on a tag   v0.6.0            -> 0.6.0 + "release"
    after tag  v0.6.0-16-g3c4c56 -> 0.6.0 + "dev.16.g3c4c56"

  Overwrite policy: an artifact is regenerated when missing OR when its build
  source is newer - rerun after a fresh build refreshes dist; rerun without
  rebuilding is a cheap no-op.

.EXAMPLE
  ./scripts/package_release.ps1                 # interactive menu
  ./scripts/package_release.ps1 -SkipBuild      # package existing build output
  ./scripts/package_release.ps1 -Version 0.6.0 -Detail test1
  ./scripts/package_release.ps1 -SkipOnefile    # standalone only
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

$prefsFile = Join-Path $PSScriptRoot ".package_release.prefs"

# ---- Interactive menu (only when no action flags given) ----------------------
$noActionFlags = -not ($SkipBuild -or $SkipStandalone -or $SkipOnefile -or $Version -or $Detail)

function Read-MenuChoice([string[]]$options, [string[]]$descriptions, [int]$initial = 0) {
    $pos = [Math]::Max(0, [Math]::Min($initial, $options.Count - 1))
    $top = [Console]::CursorTop
    while ($true) {
        [Console]::SetCursorPosition(0, $top)
        for ($i = 0; $i -lt $options.Count; $i++) {
            $marker = if ($i -eq $pos) { ">" } else { " " }
            $line = " $marker $($options[$i]) - $($descriptions[$i])"
            Write-Host ($line.PadRight([Console]::WindowWidth - 1))
        }
        $key = [Console]::ReadKey($true)
        switch ($key.Key) {
            "UpArrow"   { $pos = ($pos - 1 + $options.Count) % $options.Count }
            "DownArrow" { $pos = ($pos + 1) % $options.Count }
            "Enter"     { return $pos }
        }
    }
}

if ($noActionFlags) {
    $options = @(
        "Build + package (full)",
        "Package only (skip Nuitka build)",
        "Exit"
    )
    $descriptions = @(
        "run both Nuitka builds, then refresh build/dist artifacts (~15-25 min)",
        "zip/copy whatever is already in build/main.dist and build/onefile (~1 min)",
        "do nothing"
    )
    $saved = 0
    if (Test-Path $prefsFile) {
        $raw = (Get-Content $prefsFile -Raw).Trim()
        if ($raw -match '^\d+$') { $saved = [int]$raw }
    }
    Write-Host "package_release - choose action (up/down + Enter):" -ForegroundColor Cyan
    if (Test-Path $prefsFile) { Write-Host "  (last choice preselected; Enter to repeat)" -ForegroundColor DarkGray }
    $choice = Read-MenuChoice $options $descriptions $saved
    [Console]::SetCursorPosition(0, [Console]::CursorTop)  # move past menu
    Set-Content $prefsFile "$choice" -Encoding ascii -NoNewline
    switch ($choice) {
        0 { }                                   # full: build + package
        1 { $SkipBuild = $true }                # package only
        2 { Write-Host "bye."; exit 0 }
    }
    Write-Host "-> $($options[$choice])" -ForegroundColor Cyan
}

# ---- Version / detail detection from git -----------------------------------
if (-not $Version) {
    $desc = (git describe --tags 2>$null)
    if (-not $desc) {
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
$outDir   = "build/dist/$baseName"

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
