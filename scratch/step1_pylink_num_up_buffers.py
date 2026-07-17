"""Step 1: pure pylink — replicate the app's exact connect sequence 3 rounds,
print rtt_get_num_up_buffers() result (or exception) each round.

Hardware: J-Link serial 602717758 + STM32F103C8, firmware MaxNumUpBuffers=3.
"""
import time
import traceback

import sys

import pylink

INTER_ROUND_SLEEP = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5

TARGET = "STM32F103C8"
SPEED = 4000


def one_round(round_idx: int) -> None:
    print(f"\n===== Round {round_idx} =====")
    jl = pylink.JLink()
    try:
        # exact app sequence: open -> close -> open(serial) -> rtt_start -> set_tif -> set_speed -> connect
        jl.open()
        ser = jl.serial_number
        print(f"open() ok, serial_number = {ser}")
        jl.close()
        jl.open(str(ser))
        print(f"open({ser}) ok")

        jl.rtt_start()
        print("rtt_start() ok")

        jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
        jl.set_speed(SPEED)
        jl.connect(TARGET)
        print(f"connect({TARGET}) ok, connected() = {jl.connected()}")

        # the call under test
        t0 = time.time()
        try:
            n_up = jl.rtt_get_num_up_buffers()
            dt = (time.time() - t0) * 1000
            print(f"rtt_get_num_up_buffers() = {n_up}   (took {dt:.1f} ms)")
        except Exception as e:
            print(f"rtt_get_num_up_buffers() RAISED: {type(e).__name__}: {e}")

        # bonus: also check down buffers for reference
        try:
            n_down = jl.rtt_get_num_down_buffers()
            print(f"rtt_get_num_down_buffers() = {n_down}")
        except Exception as e:
            print(f"rtt_get_num_down_buffers() RAISED: {type(e).__name__}: {e}")

        # quick sanity: read a bit of ch0
        try:
            data = jl.rtt_read(0, 256)
            print(f"rtt_read(0,256) -> {len(data)} bytes: {bytes(data)[:80]!r}")
        except Exception as e:
            print(f"rtt_read RAISED: {type(e).__name__}: {e}")

        try:
            jl.rtt_stop()
            print("rtt_stop() ok")
        except Exception as e:
            print(f"rtt_stop() RAISED: {type(e).__name__}: {e}")
        try:
            jl.close()
            print("close() ok")
        except Exception as e:
            print(f"close() RAISED: {type(e).__name__}: {e}")
    except Exception:
        print("OUTER FAILURE:")
        traceback.print_exc()
        try:
            jl.close()
        except Exception:
            pass


def main() -> None:
    print(f"pylink version: {pylink.__version__}, inter-round sleep = {INTER_ROUND_SLEEP}s")
    for i in range(1, 4):
        one_round(i)
        if i < 3:
            print(f"--- sleeping {INTER_ROUND_SLEEP}s before next round ---")
            time.sleep(INTER_ROUND_SLEEP)


if __name__ == "__main__":
    main()
