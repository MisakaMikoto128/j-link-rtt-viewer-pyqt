# J-Link RTT Viewer (PyQt)

基于 PySide6 + QFluentWidgets 重写的 J-Link RTT 实时数据查看 / 内存导出工具。

## 功能

- **RTT 监控**：实时显示 MCU 通过 SEGGER RTT 输出的日志，支持 UTF-8 中文 / ANSI 颜色 / 0-15 通道切换 / 文本与十六进制发送 / 实时日志记录
- **内存查看**：任意地址 hex dump、固件按区间导出 `.bin`

## 开发

```bat
:: 首次创建 venv
python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt -i https://pypi.org/simple/

:: 启动
start.bat

:: 测试
pytest -q
```

## 打包

```bat
build_nuitka.bat
```

需要在系统上安装 SEGGER J-Link 驱动；`JLinkARM.dll` 由 pylink 自带，无需另置。
