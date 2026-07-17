"""Demo 3: 模拟 worker 的紧凑「断开 -> 立即重连」，看口径B是否也抛控制块异常。

关键差异：worker 的 auto_reconnect 是 rtt_stop + close 后【立即】open + rtt_start，
中间没有 sleep。Demo2 每轮间隔 0.5s 所以不抛。这里测 0 间隔（贴近真实重连）。
"""
import pylink

SERIAL = 602717758
TARGET = "STM32F103C8"


def count_allocated(jl) -> tuple[int, int, str]:
    """返回 (声明数, 实际分配数, 错误信息)。"""
    try:
        declared = int(jl.rtt_get_num_up_buffers())
    except Exception as e:
        return (-1, -1, f"num_up EXC: {e}")
    allocated = 0
    for ch in range(declared):
        try:
            desc = jl.rtt_get_buf_descriptor(ch, up=True)
        except Exception as e:
            return (declared, allocated, f"desc ch{ch} EXC: {e}")
        if getattr(desc, "SizeOfBuffer", 0) > 0:
            allocated += 1
        else:
            break
    return declared, allocated, ""


def connect_and_probe(jl, tag):
    jl.open()
    ser = jl.serial_number
    jl.close()
    jl.open(str(ser))
    jl.rtt_start()
    jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
    jl.set_speed(4000)
    jl.connect(TARGET)
    declared, allocated, err = count_allocated(jl)
    print(f"  [{tag}] declared={declared} allocated={allocated} {err}")
    return declared, allocated


def disconnect(jl):
    try:
        jl.rtt_stop()
    except Exception:
        pass
    try:
        jl.close()
    except Exception:
        pass


if __name__ == "__main__":
    jl = pylink.JLink()
    # 首次
    print("首次连接:")
    connect_and_probe(jl, "first")
    disconnect(jl)
    # 立即重连（0 间隔，模拟 auto_reconnect）
    print("立即重连（0 间隔）:")
    connect_and_probe(jl, "reconnect-0gap")
    disconnect(jl)
    # 再来一次
    print("立即重连（0 间隔，第二次）:")
    connect_and_probe(jl, "reconnect-0gap-2")
    disconnect(jl)
