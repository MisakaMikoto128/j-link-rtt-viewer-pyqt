"""Step 1b: hypothesis — the 'RTT Control Block has not yet been found' exception
is triggered by an MCU reset cycle (app's reset_with_reconnect path), not by
plain disconnect/reconnect. Test: connect -> reset -> rtt_stop/close -> immediate
reconnect -> rtt_get_num_up_buffers().
"""
import time

import pylink

TARGET = "STM32F103C8"
SPEED = 4000


def connect(jl: pylink.JLink, tag: str) -> None:
    jl.open()
    ser = jl.serial_number
    jl.close()
    jl.open(str(ser))
    jl.rtt_start()
    jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
    jl.set_speed(SPEED)
    jl.connect(TARGET)
    print(f"[{tag}] connect ok, connected() = {jl.connected()}")


def try_get_num_up(jl: pylink.JLink, tag: str) -> None:
    try:
        n = jl.rtt_get_num_up_buffers()
        print(f"[{tag}] rtt_get_num_up_buffers() = {n}")
    except Exception as e:
        print(f"[{tag}] rtt_get_num_up_buffers() RAISED: {type(e).__name__}: {e}")


def teardown(jl: pylink.JLink, tag: str) -> None:
    try:
        jl.rtt_stop()
    except Exception as e:
        print(f"[{tag}] rtt_stop RAISED: {type(e).__name__}: {e}")
    try:
        jl.close()
    except Exception as e:
        print(f"[{tag}] close RAISED: {type(e).__name__}: {e}")


def main() -> None:
    print(f"pylink version: {pylink.__version__}")
    for i in range(1, 4):
        tag = f"round{i}"
        print(f"\n===== Round {i}: connect -> reset -> close -> IMMEDIATE reconnect =====")
        jl = pylink.JLink()
        connect(jl, tag)
        try_get_num_up(jl, tag + "/before-reset")
        # MCU reset (like worker _reset_with_reconnect step 1)
        try:
            jl.reset(1, False)
            print(f"[{tag}] reset(1,False) ok")
        except Exception as e:
            print(f"[{tag}] reset RAISED: {type(e).__name__}: {e}")
        teardown(jl, tag)
        # app sleeps 0.3s here in _reset_with_reconnect; test BOTH 0.3s and 0s
        time.sleep(0.3)
        jl2 = pylink.JLink()
        connect(jl2, tag + "/reconnect")
        try_get_num_up(jl2, tag + "/reconnect")
        teardown(jl2, tag + "/reconnect")


if __name__ == "__main__":
    main()
