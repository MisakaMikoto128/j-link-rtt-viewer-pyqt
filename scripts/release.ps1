#requires -Version 5.1
<#
.SYNOPSIS
  一键发版：版本号 bump -> 提交 -> tag -> Nuitka 双版本编译 -> 打包 -> push -> gh release。

.DESCRIPTION
  用法：
    ./scripts/release.ps1 -Version 0.6.0                       # 全流程（编译+发布）
    ./scripts/release.ps1 -Version 0.6.0 -SkipBuild            # 跳过编译（已构建好产物时）
    ./scripts/release.ps1 -Version 0.6.0 -NotesFile notes.md   # 自定义 release 说明文件
    ./scripts/release.ps1 -Version 0.6.0 -DryRun               # 只打印将执行的步骤

  版本号会同步更新三处：pyproject.toml / src/ui/about_page.py / build_nuitka_onefile.bat。
  未提供 -NotesFile 时用默认模板（提示手工完善，或从 CHANGELOG 对应小节拷贝）。
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$NotesFile = "",
    [switch]$SkipBuild,
    [switch]$SkipPush,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$tag = "v$Version"

function Run([string]$cmd) {
    Write-Host "  > $cmd" -ForegroundColor DarkGray
    if (-not $DryRun) { Invoke-Expression $cmd; if ($LASTEXITCODE -ne 0) { throw "命令失败：$cmd (exit $LASTEXITCODE)" } }
}

# ---- 0. 前置检查 ----
Write-Host "[0/6] 前置检查" -ForegroundColor Cyan
if (git status --porcelain) { throw "工作区有未提交修改，先提交或 stash" }
git fetch origin --quiet 2>$null
if (git tag -l $tag) { throw "tag $tag 已存在（本地）" }
$remoteTags = git ls-remote --tags origin $tag 2>$null
if ($remoteTags) { throw "tag $tag 已存在（远端）" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "未安装 gh CLI" }

# ---- 1. 版本号 bump（三处）----
Write-Host "[1/6] 版本号 -> $Version（pyproject.toml / about_page.py / build_nuitka_onefile.bat）" -ForegroundColor Cyan
if (-not $DryRun) {
    (Get-Content pyproject.toml -Raw) -replace 'version\s*=\s*"[^"]+"', "version = `"$Version`"" |
        Set-Content pyproject.toml -Encoding utf8 -NoNewline
    (Get-Content src/ui/about_page.py -Raw) -replace 'APP_VERSION\s*=\s*"[^"]+"', "APP_VERSION = `"$Version`"" |
        Set-Content src/ui/about_page.py -Encoding utf8 -NoNewline
    (Get-Content build_nuitka_onefile.bat -Raw) -replace 'set PRODUCT_VERSION=.*', "set PRODUCT_VERSION=$Version" |
        Set-Content build_nuitka_onefile.bat -Encoding ascii -NoNewline
}

# ---- 2. 提交 + tag ----
Write-Host "[2/6] 提交 + 打 tag $tag" -ForegroundColor Cyan
Run "git add pyproject.toml src/ui/about_page.py build_nuitka_onefile.bat"
Run "git commit -m `"chore: bump version to $Version`""
Run "git tag $tag"

# ---- 3. 编译双版本 ----
if ($SkipBuild) {
    Write-Host "[3/6] 跳过编译（-SkipBuild）" -ForegroundColor Yellow
} else {
    Write-Host "[3/6] Nuitka 编译 standalone + onefile（耗时较长）" -ForegroundColor Cyan
    Run "cmd /c build_nuitka.bat"
    Run "cmd /c build_nuitka_onefile.bat"
}

# ---- 4. 打包 ----
Write-Host "[4/6] 打包 Release 资产" -ForegroundColor Cyan
Run "powershell -ExecutionPolicy Bypass -File scripts/package_release.ps1 -Mode both -Version $Version"
$zip = "build/JLinkRTTViewer-$tag-win64.zip"
$exe = "build/JLinkRTTViewer-$tag-win64.exe"

# ---- 5. 推送 ----
if ($SkipPush) {
    Write-Host "[5/6] 跳过推送（-SkipPush）" -ForegroundColor Yellow
} else {
    Write-Host "[5/6] push main + tag 到 origin" -ForegroundColor Cyan
    Run "git push origin main --follow-tags"
}

# ---- 6. gh release ----
if ($SkipPush) {
    Write-Host "[6/6] 跳过 gh release（-SkipPush）" -ForegroundColor Yellow
} else {
    Write-Host "[6/6] 创建 GitHub Release $tag" -ForegroundColor Cyan
    $notesArg = ""
    if ($NotesFile) { $notesArg = "--notes-file `"$NotesFile`"" }
    else { $notesArg = "--notes `"$tag`n`nTODO: 从 CHANGELOG.md 的 [$Version] 小节拷贝 release 说明。`"" }
    Run "gh release create $tag `"$zip`" `"$exe`" --title `"$tag`" $notesArg"
}

Write-Host "`nDone: $tag" -ForegroundColor Green
