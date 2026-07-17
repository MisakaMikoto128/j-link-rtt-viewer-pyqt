"""Demo 4: 口径B + retry，确认紧凑重连能最终拿到 allocated=1。

模拟最终修法：探测时若 num_up 抛「控制块未找到」或返回0，短间隔重试。
"""
import time
import pylink

SERIAL = 602717758
TARGET = "STM32F103C8"


def count_allocated_with_retry(jl, retries=4, interval=0.15):
    """口径B + retry：返回 allocated（实际分配的连续通道数），失败回退 1。"""
    last_err = None
    for attempt in range(retries):
        try:
            declared = int(jl.rtt_get_num_up_buffers())
            if declared < 1:
                last_err = f"declared={declared}"
                raise RuntimeError(last_err)
            allocated = 0
            for ch in range(declared):
                desc = jl.rtt_get_buf_descriptor(ch, up=True)
                if getattr(desc, "SizeOfBuffer", 0) > 0:
                    allocated += 1
                else:
                    break
            return allocated, attempt + 1, ""
        except Exception as e:
            last_err = str(e)
        if attempt < retries - 1:
            time.sleep(interval)
    return 1, retries, f"回退1: {last_err}"


def connect(jl):
    jl.open()
    ser = jl.serial_number
    jl.close()
    jl.open(str(ser))
    jl.rtt_start()
    jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
    jl.set_speed(4000)
    jl.connect(TARGET)


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
    print("首次:")
    connect(jl)
    a, tries, err = count_allocated_with_retry(jl)
    print(f"  allocated={a} (第{tries}次) {err}")
    disconnect(jl)

    print("紧凑重连(0间隔):")
    connect(jl)
    a, tries, err = count_allocated_with_retry(jl)
    print(f"  allocated={a} (第{tries}次) {err}")
    disconnect(jl)

    print("紧凑重连(0间隔) 第二次:")
    connect(jl)
    a, tries, err = count_allocated_with_retry(jl)
    print(f"  allocated={a} (第{tries}次) {err}")
    disconnect(jl)
