"""Step 1c: like step 1 but REUSE one persistent pylink.JLink instance across
rounds (matches app worker, which creates JLink once in initialize()).
Also run a read loop between connect and disconnect (app has a read thread).
"""
import threading
import time

import pylink

TARGET = "STM32F103C8"
SPEED = 4000


def main() -> None:
    print(f"pylink version: {pylink.__version__}")
    jl = pylink.JLink()  # persistent, like the app worker
    stop_read = False

    def read_loop():
        while not stop_read:
            try:
                jl.rtt_read(0, 4096)
            except Exception:
                pass
            time.sleep(0.1)

    for i in range(1, 4):
        print(f"\n===== Round {i} (persistent JLink, no reset) =====")
        jl.open()
        ser = jl.serial_number
        jl.close()
        jl.open(str(ser))
        jl.rtt_start()
        jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
        jl.set_speed(SPEED)
        jl.connect(TARGET)
        print(f"connect ok, connected() = {jl.connected()}")

        # app calls _detect_num_up_channels() right after connect()
        try:
            n = jl.rtt_get_num_up_buffers()
            print(f"rtt_get_num_up_buffers() = {n}")
        except Exception as e:
            print(f"rtt_get_num_up_buffers() RAISED: {type(e).__name__}: {e}")

        # run read thread ~1.5s like the app being connected for a bit
        stop_read = False
        t = threading.Thread(target=read_loop, daemon=True)
        t.start()
        time.sleep(1.5)
        stop_read = True
        t.join(timeout=2.0)

        try:
            jl.rtt_stop()
        except Exception as e:
            print(f"rtt_stop RAISED: {type(e).__name__}: {e}")
        try:
            jl.close()
        except Exception as e:
            print(f"close RAISED: {type(e).__name__}: {e}")
        print("--- immediate next round (0s gap) ---")


if __name__ == "__main__":
    main()
