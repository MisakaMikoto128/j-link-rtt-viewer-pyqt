# scripts/ 说明

## package_release.ps1 / package_release.sh — 一键打包（日常用）

Nuitka 构建（standalone + onefile）→ 按 git 版本号组织产物到 `build/dist/`。

**无参数运行 = 交互菜单**（上下键选择，记住上次选择，回车直接重复）：
1. Build + package — 全流程，~15-25 分钟
2. Package only — 跳过构建，只打包 build/ 现有产物，~1 分钟
3. Build only — 只构建不打包
4. Exit

**带参数运行 = 无交互**（agent / CI 用）：

```powershell
# Windows
./scripts/package_release.ps1 -SkipBuild        # 只打包
./scripts/package_release.ps1 -BuildOnly        # 只构建
./scripts/package_release.ps1 -Version 0.6.0 -Detail test1   # 覆盖版本号
./scripts/package_release.ps1 -SkipOnefile      # 只出 standalone
```

```bash
# Linux
./scripts/package_release.sh --skip-build
./scripts/package_release.sh --build-only
./scripts/package_release.sh --version 0.6.0 --detail test1
./scripts/package_release.sh --skip-onefile
```

产物（`build/dist/JLinkRTTViewer-v<ver>-<detail>-<平台>/`）：
- `.exe` / 无后缀 — onefile 单文件
- `.zip`(Win) / `.tar.gz`(Linux) — standalone 极限压缩（7-Zip / xz，缺失时回退 PowerShell / gzip）
- 同名目录 — standalone 未压缩，直接运行测试

版本号自动取自 `git describe --tags`：tag 上 = `0.6.0-release`；tag 后 = `0.6.0-dev.N.g<hash>`。
重跑不覆盖：产物缺失或 build 源更新才重新生成；其余情况秒级 keep。

## release.ps1 — 发版（bump + tag + push + gh release）

```powershell
./scripts/release.ps1 -Version 0.7.0                 # 全流程
./scripts/release.ps1 -Version 0.7.0 -SkipBuild      # 已构建好
./scripts/release.ps1 -Version 0.7.0 -DryRun         # 只打印步骤
```

版本号同步三处：pyproject.toml / src/ui/about_page.py / build_nuitka_onefile.bat。
第 4 步内部调用 package_release.ps1（产物在 build/dist/），最后 gh release 上传 zip + exe。

## measure_launch.py — 启动耗时测量

```powershell
python scripts/measure_launch.py build/main.dist/JLinkRTTViewer.exe --runs 5 --tag standalone
```

进程创建 → 窗口 ready（`--startup-bench` 写标记文件）的墙钟时间，结果追加到 scratch/measure/results.jsonl。

## build_icons.py — 重新生成 assets/icons/ 下的 ico/png（改图标源文件后用）
