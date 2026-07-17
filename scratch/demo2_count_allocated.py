"""Demo 2: 验证「用 buf descriptor SizeOfBuffer 计数」能否得到真实可用通道数。

对比两种口径：
  A. rtt_get_num_up_buffers()        -> 声明的 MaxNumUpBuffers（含空槽）= 3
  B. 遍历 buf descriptor 数 SizeOfBuffer>0 -> 实际分配的缓冲数         = ?

并验证：重连时 buf descriptor 是否也抛「控制块未找到」（决定要不要 retry）。
"""
import time
import pylink

SERIAL = 602717758
TARGET = "STM32F103C8"


def count_allocated(jl) -> tuple[int, int]:
    """返回 (声明数, 实际分配数-连续从0起)。"""
    try:
        declared = int(jl.rtt_get_num_up_buffers())
    except Exception as e:
        return (-1, -1)  # 控制块未找到
    allocated = 0
    for ch in range(declared):
        try:
            desc = jl.rtt_get_buf_descriptor(ch, up=True)
        except Exception:
            break
        if getattr(desc, "SizeOfBuffer", 0) > 0:
            allocated += 1
        else:
            break  # 从 0 起连续，遇到空槽即停
    return declared, allocated


def one_cycle(cycle: int) -> None:
    print(f"\n===== Cycle {cycle} =====")
    jl = pylink.JLink()
    try:
        jl.open()
        ser = jl.serial_number
        jl.close()
        jl.open(str(ser))
        jl.rtt_start()
        jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
        jl.set_speed(4000)
        jl.connect(TARGET)
    except Exception as e:
        print(f"  连接异常: {e}")
        _safe_close(jl)
        return

    declared, allocated = count_allocated(jl)
    print(f"  口径A num_up_buffers (声明数, 含空槽) = {declared}")
    print(f"  口径B SizeOfBuffer>0 连续计数 (实际分配) = {allocated}")
    print(f"  -> SpinBox 上限应为 {allocated - 1}（只允许 0..{allocated - 1} + 全部）")
    _safe_close(jl)


def _safe_close(jl):
    try:
        jl.rtt_stop()
    except Exception:
        pass
    try:
        jl.close()
    except Exception:
        pass


if __name__ == "__main__":
    for c in range(1, 4):
        one_cycle(c)
        time.sleep(0.5)
