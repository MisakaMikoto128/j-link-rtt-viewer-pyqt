# Contributing

欢迎贡献！请遵循以下约定。

## 提 Issue

- **Bug**：用 Bug Report 模板，附 OS / Python / J-Link 型号 / 复现步骤
- **新功能**：用 Feature Request 模板，说明使用场景

## 提 PR

1. Fork → 新分支（`feat/xxx` / `fix/xxx` / `docs/xxx` / `refactor/xxx`）
2. 中文 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 风格 commit message：
   - `feat(rtt): 加 XX 功能`
   - `fix(memory): 修 hex dump 在 32 字节/行下错位`
   - `refactor: 抽 _scroll_helpers`
   - `docs(user_guide): 加快捷键章节`
3. PR 描述要点：
   - **Why**：解决什么问题 / 满足什么需求
   - **What**：技术方案概要
   - **How to test**：审核者怎么验证

## 开发流程

```bash
# 1. clone + venv
git clone https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt.git
cd j-link-rtt-viewer-pyqt
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt

# 2. 跑测试（67 个，全过）
pytest -q

# 3. 启动
python src/main.py
```

## 代码规范

- **不要继承 `QThread`**：worker 走 `QObject + moveToThread` 范式，详见 [CLAUDE.md](CLAUDE.md) "QThread 必须独立于业务对象"
- **pylink-square 锁定 1.6.0**：2.x 的 RTT API 在 SEGGER DLL 下不工作
- **避免提前抽象**：3 行重复可以容忍；只在 2+ 处使用且非平凡时才提取 helper
- **`set()` 高频值必须节流**：`ConfigService` 已经有 200ms 节流，调用方直接 `cfg.set` 即可
- **关闭事件务必 `cfg.flush()`**：不然最后 200ms 内的偏好改动会丢

## 工程踩坑笔记

[CLAUDE.md](CLAUDE.md) 记录了项目演进中遇到的真实 Qt / pylink / 打包问题与解法，强烈建议改 worker / 线程 / 配置相关代码前先翻一下。
