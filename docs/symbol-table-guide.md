# 固件分析（符号 / 段 / 占用汇总）— 使用指南

> 选中 `.axf` / `.elf` 固件时，烧录页**底部**自动出现固件分析面板，顶部用分段开关
> 切换三个共用同一固件的视图：**符号 Symbols / 段 Sections / 占用汇总 Summary**。
> 直接读取 ELF，无需 `arm-none-eabi-nm` / `size` / `fromelf` 等命令行工具。

![符号表查看器](../img/flashing2.png)

> 仅 `.axf` / `.elf` 含这些信息；选中 `.hex` / `.bin` 时整个面板隐藏。

---

## 三个视图

| 视图 | 内容 |
| --- | --- |
| **符号 Symbols** | 全部符号表（详见下文），含每个符号占所属段大小的「% 段」列。 |
| **段 Sections** | 占用内存的段（`SHF_ALLOC`）：名称 / 地址 / 大小 / RWX 属性 / 对齐。看 `.text`/`.rodata`/`.data`/`.bss` 各占多少、落在哪。 |
| **占用汇总 Summary** | 采用 `arm-none-eabi-size` 的 Berkeley 统计方式：**Flash = text+data**、**RAM = data+bss**，以及 text/data/bss 明细；外加 **Entry point**、Cortex-M **初始 SP**、**Reset_Handler**（按 Cortex-M 约定从最低 LOAD 段头读向量表第 0、1 个字，非 Cortex-M 无意义）。 |

---

## 1. 作用

快速查看固件里都有哪些函数、变量及其**地址 / 大小 / 类型 / 绑定 / 所属段**：

- 核对某个函数 / 全局变量是否被链接进来、落在哪个地址；
- 按大小排序找出**占空间最大**的函数 / 变量；
- 排查重复符号、确认 RAM / Flash 段分布；
- 复制符号信息贴到笔记 / issue。

> 仅 `.axf` / `.elf` 含符号表。选中 `.hex` / `.bin` 时面板自动隐藏。
> 若固件被 strip（无 `.symtab`），面板显示为空。

---

## 2. 表格列

| 列 | 含义 |
| --- | --- |
| **Name** | 符号名（函数名 / 变量名等）。 |
| **Address** | 符号地址（`0x` 十六进制）。 |
| **Size** | 占用字节数。 |
| **Type** | 符号类型，彩色 pill：`FUNC`（函数，紫）/ `OBJECT`（变量，蓝）/ `FILE` / `SECTION` 等。 |
| **Binding** | 绑定：`GLOBAL` / `LOCAL` / `WEAK`。 |
| **Section** | 所属段（如 `ER_IROM1`、`RW_IRAM1`、`.bss`）。 |

**点列头排序**：Address / Size 按**数值**排序（不是字符串），Name / Type 等按字典序。

---

## 3. 过滤：chip 多选

过滤条件都是同一层的 **chip 开关**——亮起=显示该类，熄灭=隐藏，互相独立、没有先后依赖。鼠标悬停每个 chip 有中英文说明。

### 3.1 显示 Show（按类别）

| chip | 含义 | 默认 |
| --- | --- | --- |
| **Functions 函数** | 代码函数 `STT_FUNC` | ✅ 亮 |
| **Variables 变量** | 全局 / 静态变量 `STT_OBJECT` | ✅ 亮 |
| **File markers 文件标记** | 源文件名标记 `STT_FILE`（编译器生成） | ⬜ 灭 |
| **Sections 段** | 段符号 `STT_SECTION`（编译器生成） | ⬜ 灭 |
| **Other 其它** | 无类型 / 其它 `STT_NOTYPE` 等 | ⬜ 灭 |

> **为什么默认只亮 Functions 和 Variables？** 这两类是你日常真正关心的「代码 + 数据」符号。File markers / Sections / Other 多是编译器/链接器生成的辅助符号，数量大且通常不关心——需要时点亮对应 chip 即可。

### 3.2 绑定 Binding

`Global 全局` / `Local 局部` / `Weak 弱` 三个 chip，默认全亮（显示所有绑定）。熄灭某个即隐藏该绑定的符号。

### 3.3 名称搜索

顶部搜索框按符号名**子串实时过滤**（不分大小写），与 chip 条件 **叠加（AND）** 生效。

标题旁会显示统计：全部显示时 `N 符号 symbols`，有过滤时 `显示 X / 共 Y`。

---

## 4. 复制

选中若干行（按住 Ctrl / Shift 多选），点 **复制选中 Copy**，把 `名称 + 地址 + 大小`（Tab 分隔，每行一条）复制到剪贴板，可直接粘进表格 / 笔记。

---

## 5. 实用技巧

- **找最大的函数**：只亮 `Functions`，点 **Size** 列头降序——最占 Flash 的函数排最前。
- **看 RAM 占用**：只亮 `Variables`，看 `Section` 列里 `RW_IRAM` / `.bss` 的变量。
- **确认某函数地址**：搜索框输入函数名，立即定位 Address。
- **看链接器辅助符号**：临时点亮 `Sections` / `Other` 排查段布局，看完熄灭即可。

---

参见：[固件烧录页使用指南](flashing-guide.md)
