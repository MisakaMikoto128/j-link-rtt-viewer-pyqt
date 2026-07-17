"""Demo 1: 纯 pylink 探测真实硬件的 RTT 通道数。

目标：回答三个问题
1. rtt_get_num_up_buffers() 在连接/断开循环里返回什么？是否首次=3、重连抛异常？
2. ch1/ch2 的缓冲是否真实存在（buf descriptor SizeOfBuffer）？还是只是 ch0 真实？
3. ch1/ch2 是否真的读不到数据？

不依赖 src/，纯 pylink + 真实硬件。
"""
import sys
import time
import pylink

SERIAL = 602717758
TARGET = "STM32F103C8"


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

    # 1) 通道数
    try:
        n_up = jl.rtt_get_num_up_buffers()
    except Exception as e:
        n_up = f"EXC: {e}"
    try:
        n_down = jl.rtt_get_num_down_buffers()
    except Exception as e:
        n_down = f"EXC: {e}"
    print(f"  num_up_buffers = {n_up!r}")
    print(f"  num_down_buffers = {n_down!r}")

    # 2) 交叉验证：buf descriptor 看 ch0/1/2 是否真实存在
    print("  buf descriptors (up):")
    for ch in range(4):  # 多查一个 ch3，确认超出范围的行为
        try:
            desc = jl.rtt_get_buf_descriptor(ch, up=True)
            size = getattr(desc, "SizeOfBuffer", "?")
            name = getattr(desc, "NameOfBuffer", "?")
            print(f"    ch{ch}: SizeOfBuffer={size!r} Name={name!r}")
        except Exception as e:
            print(f"    ch{ch}: EXC {e}")

    # 3) 各通道实际数据
    print("  rtt_read (up):")
    for ch in range(3):
        try:
            d = jl.rtt_read(ch, 64)
            print(f"    ch{ch}: {len(d)} bytes  {bytes(d)[:50]!r}")
        except Exception as e:
            print(f"    ch{ch}: EXC {e}")

    _safe_close(jl)


def _safe_close(jl):
    try:
        jl.rtt_stop()
    except Exception as e:
        print(f"  rtt_stop: {e}")
    try:
        jl.close()
    except Exception as e:
        print(f"  close: {e}")


if __name__ == "__main__":
    for c in range(1, 4):
        one_cycle(c)
        time.sleep(0.5)
