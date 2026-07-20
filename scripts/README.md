# scripts/ 说明

## package_release.ps1 / package_release.sh — 一键打包 / 发布

Nuitka 构建 + 按 git 版本号打包 + 可选 GitHub 发布，产物放在 `build/dist/`。

**无参数运行 = 交互菜单**（上下键选择，记住上次选择，回车直接重复）：
1. Build + package (full) — 构建并打包（默认）
2. Package only — 已有 build/ 产物时只打包，不跑 Nuitka
3. Release to GitHub... — 输入版本号，自动完成 bump → tag → build → package → push → gh release
4. Exit

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

版本号自动取自 `git describe --tags`，basename 形如 `JLinkRTTViewer-v<ver>-<detail>-win64`：

| 当前 HEAD 所处位置 | `git describe` 输出 | 解析出的 basename 后缀 |
|---|---|---|
| 正好在一个 tag 上（如 `v0.6.0`） | `v0.6.0` | `v0.6.0-release-win64` |
| 在 tag 之后若干提交（如 `v0.6.0-42-gb453d43`） | `v0.6.0-42-gb453d43` | `v0.6.0-dev.42.gb453d43-win64` |
| 无任何 tag | 回退读 `pyproject.toml` 的 version | `v<ver>-untagged.g<short>-win64` |

**想发"不带 `dev.N.g<hash>` 后缀"的版本**，先在要发布的 commit 上打 tag：
```bash
git tag v0.6.0        # 在当前 commit 上打 tag
```
然后再跑 Build + package，basename 即变为 `JLinkRTTViewer-v0.6.0-release-win64`。
（更省事的是直接用菜单选项 3「Release to GitHub...」，它会自动 bump 版本 → commit → 打 tag → 构建 → 推送 → 发 release，产物也是 `release` 后缀、无 `dev.N.g<hash>`。）

重跑不覆盖：产物缺失或 build 源更新才重新生成；其余情况秒级 keep。

Release to GitHub 会先问版本号，再问是否 Dry run（dry run 只打印步骤，不改仓库/不发 release）。

## measure_launch.py — 启动耗时测量

```powershell
python scripts/measure_launch.py build/main.dist/JLinkRTTViewer.exe --runs 5 --tag standalone
```

进程创建 → 窗口 ready（`--startup-bench` 写标记文件）的墙钟时间，结果追加到 scratch/measure/results.jsonl。

## build_icons.py — 重新生成 assets/icons/ 下的 ico/png（改图标源文件后用）
