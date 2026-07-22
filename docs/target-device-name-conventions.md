# 烧录目标设备名：pylink 与 pyOCD 的约定差异与自动获取方案

> 本文回答两个问题：
> 1. 为什么同样一颗芯片，pylink 填 `STM32F030C8` 就能连，pyOCD 却可能报“未知 target”？
> 2. 能不能不手动维护 `config.json` 的 `chip_models`，而是直接从两个库读出可用目标设备列表？

---

## 1. 当前代码里的“翻译函数”

非 J-Link 烧录走 `src/core/probe/pyocd_backend.py` 的静态方法：

```python
PyOCDBackend._resolve_target_type(device_name: str) -> str | None
```

它做四步匹配：

1. `device_name.lower()` 直接命中 pyOCD 内置 `TARGET` 字典；
2. 已安装 CMSIS-Pack 的 `part_number` 等长匹配，其中 pack 里的 `'x'` 当封装/等级通配（`STM32F030C8Tx` ≈ `STM32F030C8T6`）；
3. pack `part_number` 以用户输入为前缀，或反之（`STM32F030C8` → `STM32F030C8Tx`）；
4. 都没中返回 `None`，连接时报“未知 target，请装 CMSIS-Pack”。

J-Link 烧录走 `src/core/probe/jlink_backend.py`，直接把 `device_name` 原样丢给 `jlink.connect(device_name)`，由 J-Link DLL 内部解析。

---

## 2. pylink（J-Link DLL）的目标名约定

### 2.1 设备数据库

pylink-square 1.6.0 封装了 SEGGER J-Link SDK。所有支持的目标都存在 J-Link DLL 内部，可通过 API 枚举：

```python
import pylink
j = pylink.JLink()
count = j.num_supported_devices()          # 实测 11130+
info  = j.supported_device(index)          # JLinkDeviceInfo 结构
name  = info.name                          # 例如 "STM32F030C8"
```

`JLinkDeviceInfo` 的关键字段：

| 字段 | 含义 |
|------|------|
| `name` | SEGGER 给这颗芯片起的名，连接时用的就是这个 |
| `manufacturer` / `sManu` | 厂商 |
| `Core` / `CoreId` | 内核信息 |
| `FlashAddr` / `FlashSize` | Flash 地址与大小 |
| `RAMAddr` / `RAMSize` | RAM 地址与大小 |

### 2.2 命名特点

- **通常用“短名”**：对 STM32 多数是 `STM32F030C8`、`STM32F103C8`、`STM32H750VB` 这种“family + flash size + package 前缀”形式，**不一定带完整封装/温度等级后缀**。
- **同一芯片可能有多个条目**：例如 `STM32F030C8` 和 `STM32F030C8 (allow opt. bytes)`，后者允许操作 option bytes。
- **大小写敏感/不敏感由 DLL 决定**：实测 `jlink.connect("STM32F030C8")` 和 `"stm32f030c8"` 多数情况都能过，但文档和 GUI 都用大写，建议保持大写。
- **nRF 系列带 underscore**：如 `nRF52840_xxAA`。

SEGGER 这么设计的原因是：J-Link 的 device 名主要服务于“Flash 下载算法”和“复位/连接脚本”。同一个封装变体（T6/T7）共享同一套 Flash 算法，所以不需要区分到后缀。

---

## 3. pyOCD 的目标名约定

pyOCD 0.45 的目标名分两个来源。

### 3.1 内置 target（builtin）

```python
from pyocd.target import TARGET
len(TARGET)   # 约 200+
list(TARGET.keys())[:5]  # ['mps2_an521', 'stm32f103rc', 'stm32h750xx', ...]
```

特点：

- key 是**全小写**；
- 名称通常带容量/封装通配，例如 `stm32h750xx`（`xx` 表示任意后缀）、`stm32f103rc`；
- 由 pyOCD 内置的 Flash 算法和内存映射定义，不依赖额外 pack。

### 3.2 CMSIS-Pack（DFP）

```python
from pyocd.target.pack.pack_target import ManagedPacks
devs = ManagedPacks.get_installed_targets() or []
for dev in devs:
    print(dev.part_number)   # 例如 "STM32F030C8", "STM32F030C8Tx"
```

特点：

- `part_number` 来自 Keil CMSIS-Pack 的 `.pdsc` 文件；
- 可能带封装/等级后缀（`Tx`、`Ux`、`Yx` 等），其中 `x` 是通配符；
- 用户必须用 `pyocd pack install "<part>*" -u` 预先安装，否则列表为空；
- 烧录时传给 `ConnectHelper.session_with_chosen_probe(target_override=...)`。

### 3.3 命名特点

- pyOCD 优先认 `TARGET` 字典的**小写 key** 或 pack 的 **part_number**；
- 对 STM32，短名 `STM32F030C8` 可能同时出现在 pack 里（无后缀），也可能 pack 里只有 `STM32F030C8Tx`；
- 完整型号如 `STM32F030C8T6` 通常**不会**直接命中 pack，需要 `_pack_part_wildcard_eq` 把 `Tx` 通配成 `T6`；
- STM32H7 这类在 pack 里常以 `STM32H750xx` 形式出现，所以用户填 `STM32H750VB` 反而可能匹配失败，需要手动尝试 `STM32H750xx` 或装对应 DFP。

---

## 4. 核心差异对照

| 维度 | pylink / J-Link DLL | pyOCD |
|------|---------------------|-------|
| 目标名来源 | J-Link SDK 内置设备数据库（11130+） | 内置 `TARGET`（~200）+ 已安装 CMSIS-Pack |
| 命名风格 | SEGGER 短名，大写，常省略封装后缀 | 小写内置 key；pack part_number 可能带 `x` 通配后缀 |
| 是否需额外安装 | 不需要，随 J-Link 软件安装 | pack target 需要 `pyocd pack install` |
| 输入容错 | 对大小写、后缀较宽松 | 严格依赖小写 key 或 pack part_number |
| 同一芯片变体 | 常合并为一个条目（如 `STM32F030C8`） | pack 可能拆成 `C8Tx`、`C8Ux` 等 |
| 典型写法 | `STM32F030C8`、`STM32H750VB`、`nRF52840_xxAA` | `stm32f030c8`、`stm32h750xx`、`nrf52840`（视 pack 而定） |

---

## 5. 为什么用户填 `STM32F030C8` 能烧 J-Link，但 pyOCD 可能失败？

- **pylink**：`jlink.connect("STM32F030C8")` 直接命中 J-Link DLL 里的条目，SEGGER 把 T6/T7 等封装变体视为同一设备。
- **pyOCD**：
  - 若已装 STM32F0xx_DFP，pack 里可能有 `STM32F030C8`（无后缀）或 `STM32F030C8Tx`；
  - 若用户填 `STM32F030C8T6`（完整型号），pack 里是 `STM32F030C8Tx`，需走 `'x'` 通配匹配；
  - 若 pack 没装，或 pack 里只有 `STM32F030C8Tx` 但用户填 `STM32F030C8T6` 且通配逻辑未覆盖，就会报“未知 target”。

当前 `_resolve_target_type` 已经做了模糊匹配来缓解这个问题，但根因是两套命名空间本来就不一样。

---

## 6. `config.json` 的 `chip_models` 已被移除

从 v0.3.x 起，项目不再维护 `config.json` 里的 `chip_models` 列表，而是直接通过 `src/core/target_discovery.py` 在运行时从当前活动的后端自动发现目标设备名。UI 下拉框实时反映后端能识别的设备，用户仍可手动输入任意名称做连接尝试。

### 6.1 自动发现实现

`target_discovery.py` 提供两个核心函数：

- `get_pylink_target_names()`：从 pylink-square / J-Link DLL 枚举支持的 MCU（调用 `num_supported_devices()` / `supported_device()`），过滤常见 MCU 前缀后返回大写排序去重列表。
- `get_pyocd_target_names()`：从 pyocd 内置 `TARGET` 字典与已安装 CMSIS-Pack 的 `part_number` 合并，同样返回大写排序去重列表。

两者都使用 `functools.lru_cache` 做进程级缓存，首次枚举后不再重复扫描 DLL / Pack 索引。Flash 页按当前烧录器 kind 路由：

```python
from core.target_discovery import target_names_for_burner_kind
names = target_names_for_burner_kind("jlink")   # -> pylink 列表
names = target_names_for_burner_kind("cmsisdap") # -> pyOCD 列表
```

### 6.2 推荐用法

1. **不要**在 `config.json` 里维护 `chip_models`；项目已删除该键。
2. **J-Link 烧录**：下拉自动列出 SEGGER 设备库中常见 MCU；手动输入时后端直接交给 `jlink.connect(device_name)` 解析。
3. **CMSIS-DAP / ST-Link 烧录**：下拉列出 pyOCD 内置 target + 已安装 pack；若目标不在列表中，手动输入后仍由 `PyOCDBackend._resolve_target_type()` 做模糊匹配。
4. **列表过大时的过滤**：`get_pylink_target_names()` 已内置常见 MCU 前缀过滤，避免 11130+ 条噪声淹没下拉；如需更窄的列表，可在 UI 层做补全/搜索，而不是改 config。

---

## 7. 实现位置

- `src/core/target_discovery.py`：自动发现函数与缓存。
- `src/core/probe/jlink_backend.py` / `src/core/probe/pyocd_backend.py`：后端连接与目标名解析。
- `src/core/config_service.py`：不再读取 `chip_models`；`config.json` 仅保留默认 interface / speed / font 等基础配置。

---

## 8. 总结

- pylink 的设备名来自 SEGGER J-Link SDK，习惯用短名、大写、合并封装变体；
- pyOCD 的设备名来自内置 target + CMSIS-Pack，习惯用小写或带 `x` 通配的 part_number；
- `_resolve_target_type` 已经在做跨命名空间的模糊翻译，但不可能 100% 覆盖；
- `config.json` 的 `chip_models` 已移除，目标设备列表改为运行时从当前后端自动发现，由 `src/core/target_discovery.py` 统一提供。
