#requires -Version 5.1
<#
.SYNOPSIS
  把 Nuitka 构建产物打包成 Release 资产。

.DESCRIPTION
  支持两种模式：
    standalone (默认) — 把 build/main.dist/ 重命名后压缩成 zip
                         产物：build/JLinkRTTViewer-vX.Y.Z-win64.zip
    onefile           — 单 exe，仅重命名（已经是单文件，不再压缩）
                         产物：build/JLinkRTTViewer-vX.Y.Z-win64.exe
    both              — 两个都打

  版本号默认从 pyproject.toml 读取，可用 -Version 覆盖。

.EXAMPLE
  ./scripts/package_release.ps1                       # 默认 standalone
  ./scripts/package_release.ps1 -Mode onefile
  ./scripts/package_release.ps1 -Mode both
  ./scripts/package_release.ps1 -Version 0.2.2 -Mode both
#>
param(
    [string]$Version = "",
    [ValidateSet("standalone", "onefile", "both")]
    [string]$Mode = "standalone"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $Version) {
    $pyproject = Get-Content "pyproject.toml" -Raw
    if ($pyproject -match 'version\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        throw "无法从 pyproject.toml 解析 version"
    }
}

$baseName = "JLinkRTTViewer-v$Version-win64"

function Pack-Standalone {
    $distDir = "build/main.dist"
    if (-not (Test-Path "$distDir/JLinkRTTViewer.exe")) {
        throw "找不到 $distDir/JLinkRTTViewer.exe — 先跑 build_nuitka.bat"
    }
    $stageDir = "build/$baseName"
    $zipPath = "build/$baseName.zip"
    if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

    Write-Host "[standalone 1/3] 复制 $distDir -> $stageDir"
    Copy-Item -Recurse $distDir $stageDir

    Write-Host "[standalone 2/3] 压缩 -> $zipPath"
    Compress-Archive -Path $stageDir -DestinationPath $zipPath -CompressionLevel Optimal

    Write-Host "[standalone 3/3] 清理临时目录"
    Remove-Item -Recurse -Force $stageDir

    $size = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "OK: $zipPath ($size MB)" -ForegroundColor Green
}

function Pack-Onefile {
    $srcExe = "build/onefile/JLinkRTTViewer.exe"
    if (-not (Test-Path $srcExe)) {
        throw "找不到 $srcExe — 先跑 build_nuitka_onefile.bat"
    }
    $dstExe = "build/$baseName.exe"
    if (Test-Path $dstExe) { Remove-Item -Force $dstExe }

    Write-Host "[onefile 1/1] 复制 $srcExe -> $dstExe"
    Copy-Item $srcExe $dstExe

    $size = [math]::Round((Get-Item $dstExe).Length / 1MB, 1)
    Write-Host "OK: $dstExe ($size MB)" -ForegroundColor Green
}

switch ($Mode) {
    "standalone" { Pack-Standalone }
    "onefile"    { Pack-Onefile }
    "both"       { Pack-Standalone; Pack-Onefile }
}
