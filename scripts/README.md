# scripts/ 说明

## package_release.ps1 / package_release.sh — 一键打包（日常用）

Nuitka 构建 + 按 git 版本号打包，产物放在 `build/dist/`。

**无参数运行 = 交互菜单**（上下键选择，记住上次选择，回车直接重复）：
1. Build + package (full) — 构建并打包（默认）
2. Package only — 已有 build/ 产物时只打包，不跑 Nuitka
3. Exit

**带参数运行 = 无交互**（agent / CI 用）：

```powershell
# Windows
./scripts/package_release.ps1 -SkipBuild        # 只打包
./scripts/package_release.ps1 -Version 0.6.0 -Detail test1
./scripts/package_release.ps1 -SkipOnefile      # 只出 standalone
```

```bash
# Linux
./scripts/package_release.sh --skip-build
./scripts/package_release.sh --version 0.6.0 --detail test1
./scripts/package_release.sh --skip-onefile
```

产物目录名包含版本和 git 哈希，例如：
`build/dist/JLinkRTTViewer-v0.6.0-dev.16.g3c4c56-win64/`

目录内含：
- `.exe` / 无后缀 — onefile 单文件
- `.zip`(Win) / `.tar.gz`(Linux) — standalone 极限压缩（7-Zip / xz，缺失时回退）
- 同名目录 — standalone 未压缩，直接运行测试

版本号自动取自 `git describe --tags`：tag 上为 `0.6.0-release`，tag 后为 `0.6.0-dev.N.g<hash>`。
重跑不覆盖：产物缺失或 build 源更新才重新生成；其余情况秒级 keep。

## release.ps1 — 发布到 GitHub（发版用）

独立脚本，不做交互菜单。只做 GitHub 发布相关：

```powershell
./scripts/release.ps1 -Version 0.7.0                 # 全流程
./scripts/release.ps1 -Version 0.7.0 -SkipBuild      # 已构建好
./scripts/release.ps1 -Version 0.7.0 -DryRun         # 只打印步骤
```

流程：版本号 bump（pyproject.toml / about_page.py / build_nuitka_onefile.bat）→ commit + tag → Nuitka 构建 → 调用 package_release.ps1 组织产物 → push → gh release。内部第 4 步带 `-SkipBuild`，所以不会弹菜单。

## measure_launch.py — 启动耗时测量

```powershell
python scripts/measure_launch.py build/main.dist/JLinkRTTViewer.exe --runs 5 --tag standalone
```

进程创建 → 窗口 ready（`--startup-bench` 写标记文件）的墙钟时间，结果追加到 scratch/measure/results.jsonl。

## build_icons.py — 重新生成 assets/icons/ 下的 ico/png（改图标源文件后用）
