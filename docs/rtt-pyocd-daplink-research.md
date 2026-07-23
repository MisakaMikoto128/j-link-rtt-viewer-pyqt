# SEGGER RTT 在 pyOCD / DAPLink 上的支持情况研究

> 调研日期:2026-07-22
> 调研目的:确认 pyOCD 是否原生支持 SEGGER RTT;DAPLink 能否支持 SEGGER RTT,以何种方式支持。

---

## TL;DR

1. **pyOCD 原生支持 SEGGER RTT**,从 v0.33.0 起内置 `pyocd rtt` 子命令,v0.43.0(2026-02)和 v0.44.0(2026-04)做了大幅增强,支持 per-channel 配置(stdio / TCP server / SystemView),还提供 Python `RTTClient` API。
2. **DAPLink 也支持 SEGGER RTT**,但要分两个层面理解:
   - **作为探针读目标 MCU 的 RTT**:完全支持。RTT 不是探针协议特性,本质是"目标 RAM 中的环形缓冲 + host 用 SWD 内存读写轮询"。DAPLink 提供 CMSIS-DAP 标准 SWD 内存访问,所以天然能跑 RTT,只是 RTT 逻辑在 host 端软件(pyOCD / OpenOCD / DAPLinkUtility)里,不在 DAPLink 固件里。
   - **DAPLink 固件自身用 RTT 输出调试日志**:也支持。DAPLink 源码集成了 SEGGER_RTT 库,通过 `DAPLINK_DEBUG` + `DAPLINK_DEBUG_RTT` 宏开启,需要自己改 yaml 配置并重新编译固件。

---

## 一、pyOCD 对 SEGGER RTT 的支持

### 1.1 版本演进时间线

| 版本 | 发布日期 | RTT 相关变化 |
|------|----------|--------------|
| **v0.33.0** | 2022 年初 | 首次加入 `pyocd rtt` 子命令,基本可用 |
| **v0.43.0** | 2026-02-23 | 重大升级:RTT 集成进 `run` 子命令,支持 per-channel 配置,新增 SystemView 捕获模式 |
| **v0.44.0** | 2026-04-01 | 通道模式 `telnet` 重命名为 `server`;提升鲁棒性、灵活性、传输速度;新增 SystemView Server 模式(IP Recorder) |

### 1.2 支持的通道模式(v0.44.0)

在 `*.cbuild-run.yml` 中按通道独立配置:

| 模式 | 说明 |
|------|------|
| `stdio` | 通道桥接到标准输入输出,目标输出打到 stdout,stdin 输入发回目标 |
| `server` | 启动 TCP 服务器,远程客户端通过 TCP 桥接到该 RTT 通道(原 `telnet` 模式) |
| `systemview` | 捕获该通道的 trace 数据,写入 `*.SVDat` 文件,可直接用 SEGGER SystemView 打开 |
| `systemview-server` | 通过 TCP/IP 直流到 SEGGER SystemView IP Recorder |

### 1.3 控制块发现机制(按优先级自动尝试)

1. 配置中显式指定 `control-block.address` → 直接校验该地址
2. 配置中指定 `address` + `size` → 扫描该内存范围
3. `auto-detect: true` → 扫描默认 RAM 区查找 `SEGGER RTT` 签名
4. 检查 ELF 文件中的 `_SEGGER_RTT` 符号

如果都找不到,该 core 的 RTT 自动禁用。

### 1.4 两种使用方式

**方式一:CLI 命令行**

```bash
# 安装
pip install pyocd

# 安装目标芯片的 CMSIS-Pack
pyocd pack install stm32f1

# 启动 RTT 查看(连接 DAPLink 或 J-Link 均可)
pyocd rtt -t stm32f103c8
```

**方式二:Python API**

```python
import pyocd
from pyocd.rtt import RTTClient

with RTTClient(pyocd.get_session()) as rtt:
    while True:
        data = rtt.read(0)  # 从上行通道 0 读取
        if data:
            print(data.decode(), end='')
```

### 1.5 配置文件示例

```yaml
# pyocd.yaml 或 *.cbuild-run.yml
debugger:
  name: CMSIS-DAP@pyOCD
  protocol: swd
  rtt:
    - pname: Core0
      control-block:
        address: 0x20000000
        size: 0x00020000
      channel:
        - number: 0
          mode: stdio
        - number: 2
          mode: server
          port: 4444
```

### 1.6 已知限制

- RTT 目前**只在 `pyocd run` 和 `pyocd rtt` 命令里启用**,gdbserver 子命令暂未集成(官方计划后续版本加入)
- 控制块第一字符缺失的边界 bug 已在 v0.43.0 修复
- up/down 通道的可用字节查询在 v0.43.0 修复

---

## 二、DAPLink 对 SEGGER RTT 的支持

### 2.1 关键认知:RTT 不是探针协议特性

这是最容易误解的点。SEGGER RTT 本质上是:

```
目标 MCU 端                          Host PC 端
─────────────                       ─────────────
SEGGER_RTT.c 库    ──写入──>         pyOCD / OpenOCD /
  ↓                                  J-Link 工具
RAM 中的环形缓冲   <──SWD 读取──     (周期性轮询该 RAM 区域)
```

- **目标端**:SEGGER 提供的开源 C 库(`SEGGER_RTT.c` / `SEGGER_RTT.h`),链接进目标固件,在 RAM 里维护环形缓冲
- **Host 端**:用调试器 SWD 接口周期性读取目标 RAM 中那块缓冲区,解析 SEGGER RTT 控制块格式
- **探针端**:只需要提供"SWD 内存读写"能力,不需要懂 RTT 协议

所以**任何能读 SWD 内存的探针都能跑 RTT**,包括 DAPLink、ST-Link、Picoprobe 等。

### 2.2 层面 A:DAPLink 作为探针读目标 MCU 的 RTT

**结论:完全支持。** DAPLink 的核心能力就是 CMSIS-DAP SWD 内存读写,跑 RTT 绰绰有余。RTT 逻辑放在 host 端软件里:

| Host 工具 | 类型 | 典型用法 | 备注 |
|-----------|------|----------|------|
| **pyOCD** | 开源 Python | `pyocd rtt -t <target>` | 最便捷,跨平台 |
| **OpenOCD** | 开源 C | `rtt setup 0x20000000 0x1000 "SEGGER RTT"` + `rtt start` + `rtt server start 8888 0` | 需要写 cfg 文件 |
| **DAPLinkUtility** | 三方 Windows GUI | 直接运行 `DAPLinkUtility.exe` | 国人开发,V0.0.21 最新,带 RTT Viewer,支持 16 通道 + ANSI 彩色 + 时间戳 + 日志保存,但下行通道暂不支持 |
| **RTTView.py** | 三方 Python(PyQt4) | 直接调 pyOCD probe API 读内存 | 老旧,仅作参考 |

#### OpenOCD 配置示例

新建 `rtt.cfg`:

```bash
init
# 在 RAM 中扫描 SEGGER RTT 控制块
rtt setup 0x20000000 0x1000 "SEGGER RTT"
rtt start
# 启动 RTT TCP 服务器,通道 0 桥接到 8888 端口
rtt server start 8888 0
```

启动:

```bash
openocd -f interface/cmsis-dap.cfg -f target/stm32f1x.cfg -f rtt.cfg
```

然后用 `telnet 127.0.0.1 8888` 即可看到 RTT 日志。

#### 性能对比

| 探针 | RTT 速度 | 原因 |
|------|----------|------|
| J-Link | 最快(几 MB/s) | 专用 USB 协议 + 优化的 batch memory read |
| DAPLink (CMSIS-DAP v2) | 中等(HID Bulk 端点) | 走标准 CMSIS-DAP memory read 命令,受端点带宽限制 |
| DAPLink (CMSIS-DAP v1) | 较慢(HID 中断端点 64 字节) | v1 走 HID 中断端点,带宽更小 |
| ST-Link | 中等 | ST 私有协议,效率不如 J-Link 但优于 v1 DAPLink |

实测:STM32 + DAPLink v2 + pyOCD,1kHz 级别的 `SEGGER_RTT_printf` 输出无压力。如果是高频 ADC 数据流(几十 MB/s),DAPLink 会丢数据,这时只能上 J-Link。

### 2.3 层面 B:DAPLink 固件自身用 RTT 输出调试日志

**结论:也支持,需要改源码加宏重新编译。**

DAPLink 源码已经完整集成 SEGGER_RTT 库,通过宏开关控制:

- `DAPLINK_DEBUG` — 开启调试日志输出
- `DAPLINK_DEBUG_RTT` — 走 RTT 通道输出(而不是 UART)
- 配套宏:`debug_msg()` 和 `debug_data()` 用于输出日志

修改方法(以 STM32F103 HIC 为例):

1. 编辑 `records/hic_hal/stm32f103xb.yaml`,添加两个宏:

   ```yaml
   module_define:
     - DAPLINK_DEBUG
     - DAPLINK_DEBUG_RTT
   ```

2. 如果用 CMake 编译,需要注释掉 `records/tools/gcc_arm.yaml` 中的 `-Werror`,否则警告会变错误阻断编译

3. 生成工程并编译:

   ```bash
   progen generate -t cmake -v -p stm32f103xb_bl
   progen generate -t cmake -v -p stm32f103xb_stm32f103rb_if
   ```

4. 烧录新固件后,需要**用另一个调试器**(如 ST-Link)连接 DAPLink 板子的 SWD 引脚,然后用 pyOCD/OpenOCD 读 RTT,才能看到 DAPLink 自己输出的 HIC 层日志

> 注意:`__FILE__` 宏会输出全路径,Keil 用 `__MODULE__` 替代;GCC 工具链下用 `strrchr()` 运行期处理,有性能损耗,不建议生产环境用。

---

## 三、综合对比表

| 维度 | J-Link + SEGGER 工具 | DAPLink + pyOCD | DAPLink + OpenOCD |
|------|---------------------|-----------------|-------------------|
| 探针成本 | 高(几百到几千元) | 低(几十元,开发板自带) | 同左 |
| RTT 原理 | SEGGER 专用协议 | SWD 内存轮询 | SWD 内存轮询 |
| RTT 速度 | 最快 | 中等(v2 优于 v1) | 中等 |
| Host 工具 | J-Link RTT Viewer / JLinkExe / Ozone | `pyocd rtt` 或 Python API | `rtt setup/start` + TCP server |
| 跨平台 | Windows 主导,Linux/macOS 也有 | 全平台 Python | 全平台 C |
| Python API | pylink-square(本项目用 1.6.0) | pyOCD `RTTClient`(原生) | 需自己写 TCP 客户端 |
| 多通道 | 支持(16 通道) | 支持(per-channel 配置) | 支持(`rtt server start <port> <channel>`) |
| SystemView | 原生集成 | v0.43.0 起支持 | 需手动处理 |
| 目标端代码 | SEGGER_RTT.c(同一份) | SEGGER_RTT.c(同一份) | SEGGER_RTT.c(同一份) |
| 社区生态 | 商业支持,文档全 | 活跃开源,Python 友好 | 老牌开源,配置繁琐 |

---

## 四、对本项目(J-Link RTT Viewer PyQt)的启示

本项目当前架构:
- 锁定 `pylink-square==1.6.0`,只能用 J-Link 探针
- 通过 `pylink.JLink` 直接调用 SEGGER 的 DLL

如果要支持 DAPLink 作为低成本备选探针:

1. **目标端代码不需要改** — `SEGGER_RTT.c` 在目标 MCU 上跑得跟探针无关
2. **加一条 pyOCD 后端** — 抽象出 `RttBackend` 接口,`JLinkBackend` 调 pylink,`PyOcdBackend` 调 `pyocd.rtt.RTTClient`
3. **API 迁移成本不高** — `RTTClient.read(channel)` 和 `pylink.JLink.rtt_read(channel, N)` 行为对应
4. **保留 J-Link 为默认** — 高频场景(几 MB/s 数据流)DAPLink 撑不住,文档里说明限制即可
5. **pylink 2.x 不工作** 的坑项目里已经踩过(AGENTS.md 有记录),pyOCD 的版本管理相对稳定,但仍建议锁 `pyocd>=0.43.0`(RTT 功能成熟期)

### 推荐抽象层设计草案

```python
from abc import ABC, abstractmethod

class RttBackend(ABC):
    @abstractmethod
    def connect(self, target: str, interface: str = "swd") -> None: ...
    @abstractmethod
    def read(self, channel: int) -> bytes: ...
    @abstractmethod
    def write(self, channel: int, data: bytes) -> int: ...
    @abstractmethod
    def disconnect(self) -> None: ...

class JLinkBackend(RttBackend):
    # 包装 pylink.JLink,现有 JLinkWorker 逻辑搬过来
    ...

class PyOcdBackend(RttBackend):
    # 包装 pyocd.rtt.RTTClient
    ...
```

这样 UI 层完全无感,用户在设置里切换探针类型即可。

---

## 五、参考来源

- pyOCD 0.43.0 release notes: https://pyocd.io/posts/2026/02-23-pyocd-0.43.0-released.html
- pyOCD 0.44.0 release notes: https://pyocd.io/posts/2026/04-01-pyocd-0.44.0-released.html
- pyOCD 官网: https://www.pyocd.io
- Open-CMSIS-Pack pyOCD Debugger 文档: https://open-cmsis-pack.github.io/cmsis-toolbox/pyOCD-Debugger
- DAPLink + OpenOCD RTT 实战: https://www.cnblogs.com/luyaocf/p/18457344
- DAPLinkUtility V0.0.21 下载与使用: https://blog.csdn.net/weixin_42880082/article/details/148463358
- pyOCD RTT API 示例: https://makerinchina.cn/article/20220201120952.html
- SEGGER RTT 源码获取: https://www.segger.com/downloads/jlink/#J-LinkSoftwareAndDocumentationPack
