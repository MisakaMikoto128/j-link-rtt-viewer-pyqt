#requires -Version 5.1
<#
.SYNOPSIS
  把 build/main.dist/ 打包成 JLinkRTTViewer-vX.Y.Z-win64.zip 供 Release 上传。

.DESCRIPTION
  - 版本号默认从 pyproject.toml 读取，可用 -Version 覆盖
  - 内层文件夹名 = JLinkRTTViewer-vX.Y.Z-win64（解压后用户看到的根目录）
  - 输出到 build/JLinkRTTViewer-vX.Y.Z-win64.zip
  - 重新运行会覆盖旧 zip 和临时文件夹

.EXAMPLE
  ./scripts/package_release.ps1
  ./scripts/package_release.ps1 -Version 0.2.1
#>
param(
    [string]$Version = ""
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

$distDir = "build/main.dist"
if (-not (Test-Path "$distDir/JLinkRTTViewer.exe")) {
    throw "找不到 $distDir/JLinkRTTViewer.exe — 先跑 build_nuitka.bat"
}

$folderName = "JLinkRTTViewer-v$Version-win64"
$stageDir = "build/$folderName"
$zipPath = "build/$folderName.zip"

if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

Write-Host "[1/3] 复制 $distDir -> $stageDir"
Copy-Item -Recurse $distDir $stageDir

Write-Host "[2/3] 压缩 -> $zipPath"
Compress-Archive -Path $stageDir -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "[3/3] 清理临时目录"
Remove-Item -Recurse -Force $stageDir

$size = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "OK: $zipPath ($size MB)"
