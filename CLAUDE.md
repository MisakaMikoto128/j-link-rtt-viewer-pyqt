# 项目经验笔记

为后续维护积累的实际踩坑经验。每条都带 **现象 / 原因 / 处理** 三段。

正文在 `docs/pitfalls.md`（全文）。本文件是索引——按场景查对应条目，不全量常驻 context。改对应模块前先扫一眼本索引，命中就 `Read docs/pitfalls.md` 对应章节（或用 Grep 搜关键词）。

**诊断方法论**：凡「现象反直觉、API 语义隐蔽」的硬件问题，不要在大工程里猜改——写最小 demo（纯 pylink / 纯 Qt / 完整 app）在真实硬件上逐步打印每步返回值定位根因，再动手。已有 demo：`scratch/probe_enum_race.py`、`scratch/probe_remote*.py`、`scratch/repro_bug1.py`、`scratch/demo1-5`（未入库）。

---

## 工具使用

- **Headroom MCP 已装（被动模式）**：`mcp__headroom__*` 工具可用。当 Read / Grep / Glob 返回内容 > ~2000 字符，主动调 `headroom_compress` 压缩后纳入上下文，后续需细节用 `headroom_retrieve(hash)` 取回。统计 `headroom_stats`。注意：仅压缩大输出有意义，小输出压缩反费工具调用。

---

## pylink / J-Link 连接与 RTT

- `close()` / `rtt_stop()` 抛 `JLinkException` 不致命 — try/except 包裹，**别**用 `opened()` / `connected()` 守卫。`jlink_worker._do_disconnect`
- `set_tif(SWD | JTAG)` 是错的 — 互斥枚举二选一，不是 bit flag
- **pylink 必须用 1.6.0**，2.x RTT 不工作（`requirements.txt` 锁版本）
- **连接顺序**：open → close → open(serial) → rtt_start → set_tif → set_speed → connect（pylink 1.6.0 硬性要求）
- `rtt_get_num_up_buffers()` 返回声明数不是已分配数，通道数用 buf descriptor 的 `SizeOfBuffer` 计数。`_detect_num_up_channels`
- **DLL 同句柄并发不安全**：所有 pylink DLL 调用必须串行经 `_dll_lock`（RLock）。J-Link 报 access violation 先查这个
- J-Link 烧 axf 变砖、hex 正常：不用 `flash_file`，逐段 `jlink.flash(data, p_paddr)`。`jlink_backend.program`
- `supported_device()` 的 legacy `FlashAddr` / `FlashSize` 不可信，用 `aFlashArea` Size 最大区域。`_pick_main_region`
- 远程连接（Remote Server）：DLL 不做 DNS，域名 Python 侧 `resolve_remote_host` 解析成 IPv4
- 多 J-Link：按 serial 连接 + auto_reconnect 串行匹配（`SerialNumber` 是唯一稳定 ID）
- RTT 读循环用 `threading.Thread` 不用 QTimer；**native thread 不直接 emit Qt signal**，用 lock+buffer，worker 线程 QTimer drain

## QThread / 线程 / Qt 信号

- **永远不继承 QThread** — worker 继承 QObject + `moveToThread`。直接继承 QThread + override run() 是反模式
- `__init__` 跑在主线程 — QTimer / pylink.JLink / Decoder 等事件循环对象必须在 `run()` / `initialize()` 内创建
- worker 退出必须 worker 自己 `quit()` — `stop_requested` 信号 + 主线程 `wait()`，别直接调 worker.quit()
- worker 线程内 QTimer/QObject 退出前必须自己 `stop()` + `deleteLater()`（在 `thread().quit()` 之前）
- **PySide6 跨线程 Signal 不传 dict** — 改 str，或同步方法 + lock 取信息
- `IncrementalDecoder` 自管半字节缓冲 — 别外层叠 `byte_buffer`，`getstate()[1]` 是整数标记不是字节
- worker 退出必须 worker 自己 quit + 不用 os._exit

## 烧录（FlashWorker / backend / 烧录器下拉 / pyOCD / CMSIS-Pack）

- FlashWorker backend 必须按 `FlashParams.burner_kind` 动态创建（`make_backend`），不能预建固定 jlink
- 全片擦除复用烧录流程：加 `erase_only` 标记贯穿，不另写一套
- 烧录器下拉：**单向数据流** — combo 只显示+捕获点选，`_selected_*` 是真源，`_current_burner()` 只读真源
- programmatic `setCurrentIndex` 后别靠 combo 状态解析 serial，存真源字段
- cfg serial 与在线设备不匹配时保持离线占位 + 红点，不自动切其它在线设备
- 烧录器下拉 label：单点真源 `_burner_label(kind, serial, product)`，离线占位保留 kind/product 前缀
- pyOCD `target_override`：CMSIS-Pack `part_number` 'x' 是封装通配，用户填完整型号要通配匹配。`_pack_part_wildcard_eq`
- pyOCD `mass_erase` 前必须先 `halt()`（DAPLink "IPSR=3" 偶发失败）
- `cmsis_pack_manager.Cache`：`json_path` 必须与 `data_path` 一致，否则 download 不落到 data_path
- pack 增删后 `get_pyocd_target_infos` 的 `functools.cache` 必须失效，否则目标列表要重启才刷新
- `download_pack` 返回 `skipped`/`downloaded`/`failed` 区分，避免已装 pack 显示"下载完成"
- pack 结果提示用 `_infobar` 气泡（TOP），不用底部 `lbl_status`（被表格遮挡）
- CMSIS-Pack 是专业名，UI 文案用 "CMSIS-Pack" 不用 "pack"

## qfluentwidgets 控件坑

- `EditableComboBox.setCurrentText(不在 items 的文本)` 是 **no-op** — 继承自 ComboBoxBase，findText 返回 -1 就什么都不做
- `setCurrentText(任意路径)` 多处踩坑 — 业务持久化走「addItem + setCurrentIndex」，测试/临时塞文本走 `setText`
- `setCurrentIndex` 选中后 `currentText()` 可能不同步 — scratch 脚本和真 app 事件循环都踩
- `QTextEdit` 没有 `setMaximumBlockCount` — RTT 显示区用 `QPlainTextEdit`
- 自动滚动判断在**插入前**（`sb.value() >= sb.maximum() - 4`），插入后判断永远 True
- `_tip` 在 `_retranslate_ui` 重复调用叠加 ToolTipFilter 产生重影 — 用动态属性 `_fluent_tip_installed` 只装一次
- 静态按钮文字必须在 `_retranslate_ui` 里显式 `setText`，不能只靠构造时 `tr()`
- `QTableWidget` 排序默认按字符串，数值列用自定义 `_NumericItem` override `__lt__`
- 高频热路径构造 `QColor` 是不必要 alloc — 预构造放模块级 dict（`_ANSI_QCOLORS`）

## i18n / 字体 / QSS

- 自定义 `QTranslator.translate` 未命中必须返回 `source`，不能返回空串（Qt 检查 isNull 非 isEmpty，空串被当有效译文导致控件空白）
- `zh_CN` 也必须安装 JsonTranslator — 否则第三方英文源控件（ColorDialog OK/Cancel 等）在中文界面全程英文
- QSS `font:` 锁定的控件（RadioButton 等）`setFont` 完全无效，必须 `setStyleSheet` 追加规则覆盖。`_ui_font.sync_qss_font_locked_widgets`
- 动态内容 hover 提示用 `FluentHoverTip` 复用 Fluent ToolTip，不用 `QToolTip.showText`
- 内存页 hex 显示区 family 固定跟 RTT（等宽），不跟全局 UI 字体

## 设计原则

- 一次用户操作的编排归属一个模块，不跨 UI↔worker 用 flag 串
- 信号参数不靠 bool 反向区分模式 — 传枚举字符串（`Signal(str)`）或拆信号
- UI 控件文本不是 state enum，不用 `text() == "连接"` 当状态判断 — 维护 `_is_connected` 真状态
- 模式 / 枚举字符串必须有常量，不能字面值散落（`RESET_MODE_*`）
- helper 抽了就在所有同形态处用，不要"抽了一半"
- slot 作为方法被直接调用要明示 — 抽 non-slot 私有 helper（`_do_connect`），slot 退化成 1 行 wrapper
- 跨方法 setter/reset 的 boilerplate 用 `@contextmanager` 包（`_programmatic_scroll_guard`）
- 状态恢复逻辑放回状态机本身，不塞在不相关的 handler 兜底
- 派生公式必须有单点真源（`_byte_start_col`）
- `_open_elf` 必须自己 catch `ELFError` + close，不能依赖调用方包 try

## 测试

- PySide6 信号 spy 用 QObject + bound-method 槽，不用裸 lambda（worker 线程拥有的信号裸 lambda 不触发）
- pytestqt segfault：UI 测试 `stub_make_backend` 别让真实 FlashWorker 碰 J-Link/pyOCD DLL；用 `@pytest.mark.real_make_backend` 退出 stub
- 测试 / scratch 驱动 EditableComboBox：用 `setText` + 直接调页面槽，不靠 `setCurrentIndex` 触发链路

## 配置 / 打包 / 其他

- `user_prefs.json` 放 `%APPDATA%/JLinkRTTViewer/`，不放进 `src/`（Nuitka 打包后 Program Files 只读）
- `ConfigService.set()` 高频值要节流 — dirty 标记 + 200ms flush timer
- `closeEvent` 必须 `cfg.flush()` 强制落盘（节流下最后一次 set 可能没赶上 timer）
- Nuitka 打包：`--include-package-data=qfluentwidgets` 保留；`--include-package=qfluentwidgets` 已删（多余有害，靠 `--follow-imports`）。打包参数详见 `docs/packaging_startup_report.md`
- `target_discovery` 缓存命中时 worker `initialize` 不跑（省启动 ~89ms GIL）
- 断开后状态栏闪回旧值：worker 必须在 `emit(False)` 前清零 `_session_start_ts`（断开态标记）；收发计数跨断开保留
- UI 模块拆分记录（`rtt_monitor_page.py` 3083 → 1929，Step 1-5 详见 pitfalls.md）
- **TODO：发版前敲定作者信息** — `src/ui/about_page.py` 顶部 `AUTHOR_NAME = "待定"` / `AUTHOR_GITHUB`
- RTT 通道选错时显示区无内容 — 确认 SpinBox 通道与 MCU 端 `SEGGER_RTT_printf(N,...)` 一致