# J-Link 远程连接（Remote Server）设计文档

日期：2026-07-18  
状态：待用户批准

## 1. 背景与目标

pylink-square 1.6.0 本身是 J-Link client 库，支持 `JLink.open(ip_addr="ip:port")`（内部调 `JLINKARM_SelectIP`）。目标是在现有本地 USB 设备选择之外，提供"远程连接"模式：用户输入 IP（或域名）+ 端口，像操作本地 J-Link 一样使用 RTT 监控与固件烧录。

实测（`scratch/probe_remote.py` + `scratch/probe_remote_dns.py`，Remote Server @ 192.168.79.1:19020）已确认：

- 远程完整链路工作：`open(ip_addr)` → close → 重开 → `rtt_start` → `set_tif/set_speed` → `connect(target)` → `rtt_read` 持续收到数据 → `rtt_stop/close` 干净。
- **J-Link DLL 不做 DNS 解析**：`open(ip_addr="localhost:19020")` 报 `Cannot connect to J-Link name localhost`。域名必须在 Python 侧解析成 IP 再传。
- 不可达主机约 3s 报错（pylink 内置，无超时参数可设）。
- 远程 open 后 `serial_number` 可读（实测 `602717758`）。

## 2. 总体设计

在现有「本地 USB 设备选择」之上叠加远程模式，**不改动**已有本地路径的既有行为与测试。

### 2.1 UI（RTT 监控页）

- J-Link 下拉（`cb_jlink`）items：`在线 serial…; 远程连接…`（固定最后一项，常量文案「远程连接…」）。
- 选中「远程连接…」→ 下拉下方显示一行：`IP/域名输入框 + 端口输入框`（同一行，等宽排列）。选回本地项 → 隐藏该行。
- 远程模式下红点（`_jlink_status_dot`）变为**状态点**：TCP 可达 → 隐藏；不可达/域名解析失败 → 红点。探测在 UI 侧 `QThreadPool` + `socket.connect_ex`（200ms 一拍，复用 worker 的 `devices_enumerated` 节拍驱动），绝不阻塞 UI。
- 输入持久化（`ConfigService` / `%APPDATA%/JLinkRTTViewer/user_prefs.json`）：
  - `jlink_mode`: `"usb" | "remote"`
  - `last_remote_host`: 原始输入（可能是域名）
  - `last_remote_port`: 端口字符串
  - 重启后按 `jlink_mode` 恢复模式与输入框内容。

### 2.2 域名解析

新增模块级函数（UI 侧，可放 `rtt_monitor_page.py` 或独立 helper）：

```python
def resolve_remote_host(host: str) -> str | None:
    """host 是 IPv4 字面量原样返回；是合法主机名则 socket.getaddrinfo 解析；
    解析失败/非法返回 None。"""
```

- 合法输入：IPv4 字面量 或 RFC1123 主机名（含 `localhost`）。
- 解析失败 → UI 合并警告一条：`无法解析主机名 "xxx"`。
- 解析成功 → 传解析出的 IP 给 worker（`ip:port`）。
- 状态点探测同样先解析再 `connect_ex`。

### 2.3 Worker（`src/core/jlink_worker.py`）

- 新信号：`connect_remote_requested = Signal(str, str, int, int, str)`，参数 `(target, iface, speed, channel, "ip:port")`。
- `_do_connect(..., remote_addr: str = "")`：
  - `remote_addr` 非空 → 跳过 `connected_emulators()` 前置枚举与 serial 在线校验；连接序列：
    ```
    open(ip_addr=remote_addr) → close → open(ip_addr=remote_addr) → rtt_start
    → set_tif → set_speed → connect(target)
    ```
  - `remote_addr` 空 → 走现有本地分支，行为不变。
- `_last_connect_params` 由 5 元组扩为 **6 元组** `(target, iface, speed, channel, serial_or_"", remote_addr)`。
- `_collect_device_info` 增加 `remote_addr` 键（本地为 `""`）。
- 连接失败：捕获异常 → `log_message("error", "无法连接远程 J-Link (ip:port)：请检查 Remote Server 是否运行、IP/端口是否正确")`（本地分支文案不变）。

### 2.4 接收区域连接提示

UI `_set_connected_ui(info)` 收到 `info["remote_addr"]` 非空时，在 RTT 接收区追加一条带时间戳的染色信息行：

```
[HH:MM:SS] 已连接远程 J-Link 192.168.79.1:19020 (S/N: 602717758)
```

本地连接不打印（避免改动现有行为/测试）。样式复用现有标记/状态行的 `QTextCharFormat` 机制。

### 2.5 自动重连

- `_reconnect_target_serial` 扩展为 `_reconnect_target: tuple[str, str]`（serial, remote_addr）。
- 远程掉线（网络断/服务器关）→ `rtt_read` 抛异常 → 现有 `unexpected_disconnect` 路径；勾选自动重连时 `_start_reconnect(serial, remote_addr)`。
- `_reconnect_tick`：
  - `remote_addr` 非空 → **跳过 USB 枚举检查**，直接 `_do_connect(*params)`（6 元组）重试；失败 emit `reconnect_status("failed", n)`，3s 后下一拍。
  - `remote_addr` 空 → 现有本地逻辑不变。
- 重连过程接收区打印橙色提示行（复用现有 `reconnect_status` → 显示区染色行机制）：`正在尝试自动重连远程 J-Link ip:port（第 n 次）`；成功打印绿色行。
- 远程掉线期间状态点变红（探测失败），恢复后自动变绿。

### 2.6 连接校验（用户已确认：TCP 预检后再走 worker）

点击「连接」且当前为远程模式：

1. 读取 host/port 输入；port 必须是 1-65535 整数。
2. `resolve_remote_host(host)` → None → 一条合并警告 `无法解析主机名 "xxx"`，返回。
3. `socket.connect_ex((ip, port))`（短超时 ~2s，worker 线程池异步）不通 → 一条合并警告 `无法连接远程 J-Link (ip:port)，请检查 Remote Server 是否运行、IP/端口是否正确`，返回。
4. 通过 → 持久化 `jlink_mode/last_remote_host/last_remote_port` → 按钮置「连接中…」→ emit `connect_remote_requested`。

### 2.7 烧录页（`src/ui/flash_page.py` + `src/core/flash_worker.py`）

- 烧录器下拉（`cmb_burner`）同样加「远程连接…」项 + IP/Port 行 + 状态点（复用同一探测模式）。
- 持久化：`flash_jlink_mode`、`flash_remote_host`、`flash_remote_port`。
- `FlashParams` 增加 `remote_addr: str = ""`；`FlashWorker._do_connect` 远程分支用 `open(ip_addr=...)` 双开（跳过 USB 枚举与 serial 校验）。
- **RTT 协调（同一逻辑设备）**：烧录目标与 RTT 当前连接「同为本地同 serial」或「同为同一 remote_addr」→ 先断 RTT → 烧 → 断 → 按原参数（6 元组，含 remote_addr）回连。判定依据 `rtt_worker.get_device_info()` 的 `jlink_serial` 与 `remote_addr`。

### 2.8 i18n

所有新增 UI 文案走 `self.tr()`，在 `src/i18n/*.json` 补「远程连接…」「IP」「端口」「无法解析主机名」「已连接远程 J-Link…」等键。zh_CN.json 只收英文 source→中文映射（第三方控件），本特性源文本是中文，按现有约定不进 zh_CN.json。

## 3. 错误处理汇总

| 场景 | 处理 |
|---|---|
| 域名解析失败 | UI 一条合并警告，不进 worker |
| TCP 不可达 | UI 预检拦截，一条合并警告 |
| pylink open 失败（3s 超时等） | worker `log_message("error", …)` + `_do_disconnect` 回正按钮 |
| 会话中网络中断/服务器关闭 | `unexpected_disconnect`；勾自动重连则按 remote_addr 3s 轮询重连 |
| 状态点 | 远程：探测失败/解析失败 → 红点；恢复 → 隐藏 |

## 4. 测试计划

执行委托 subagent，新增/更新：

- `tests/test_jlink_worker.py`：
  - 远程连接序列：mock pylink，断言 `open(ip_addr=)` 被调两次（双开）、`rtt_start` 在 `set_tif` 前、最终 `connect(target)`。
  - `_last_connect_params` 6 元组，`_reset_with_reconnect` 远程回放。
  - `_reconnect_tick` 远程分支跳过 `connected_emulators()`。
- `tests/test_rtt_monitor_page.py`：
  - 选中「远程连接…」显示 IP/Port 行，选回本地隐藏。
  - `resolve_remote_host`：IPv4 原样、localhost 解析、非法返回 None。
  - 远程模式下红点随探测结果显隐（mock connect_ex）。
  - `_set_connected_ui` 收到 remote_addr 时接收区出现提示行。
- `tests/test_flash_page.py`：
  - 烧录页远程项 + IP/Port 行显隐。
  - `FlashParams.remote_addr` 传递；协调逻辑：同 remote_addr 触发 RTT 断开/回连。
- 全量回归：`uv run python -m pytest tests/ -x -q`（已知偶发 flaky `test_unexpected_disconnect_emits_signal` 单独重跑确认）。

## 5. 明确不做（YAGNI）

- 不做远程设备自动发现（`connected_emulators(host=IP)` 实测返回空，不可靠）。
- 不做 IPv6 字面量输入（Remote Server 场景以 IPv4/域名为主；需要时后续加）。
- 不改本地 USB 路径的任何既有行为。
- 不在 worker 侧做 TCP 预检（预检在 UI 侧，worker 只负责 pylink 连接）。
