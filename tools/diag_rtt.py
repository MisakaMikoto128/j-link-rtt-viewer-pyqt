"""J-Link RTT 诊断脚本：用真实硬件验证 pylink 1.6.0 调用方式。

直接运行：
    venv\Scripts\python.exe tools\diag_rtt.py

会自动按参考项目顺序连接，扫描可用 RTT 通道，从每个有数据的通道读 10 秒，
把所有原始字节和解码后的字符串都打出来。
"""
from __future__ import annotations

import codecs
import sys
import time

import pylink


TARGET = "STM32H750VB"
INTERFACE = "SWD"
SPEED = 4000  # kHz


def main() -> int:
    print(f"=== pylink 版本：{pylink.__version__} ===")

    jl = pylink.JLink()

    # === 步骤 1：发现 J-Link ===
    print("\n--- step 1: connected emulators ---")
    try:
        emulators = jl.connected_emulators()
        for e in emulators:
            print(f"  found: serial={e.SerialNumber}, nickname={e.acProduct.decode(errors='ignore')!r}")
    except Exception as exc:
        print(f"  connected_emulators failed: {exc!r}")

    # === 步骤 2：参考项目的连接顺序 ===
    print("\n--- step 2: open ---")
    try:
        jl.open()
        print(f"  opened OK, serial_number={jl.serial_number}")
        print(f"  firmware_version={jl.firmware_version!r}")
        print(f"  hardware_version={jl.hardware_version!r}")
    except Exception as exc:
        print(f"  open failed: {exc!r}")
        return 1

    print("\n--- step 3: close + reopen with serial ---")
    try:
        ser = jl.serial_number
        jl.close()
        jl.open(str(ser))
        print(f"  reopened OK with serial {ser}")
    except Exception as exc:
        print(f"  reopen failed: {exc!r}")
        return 1

    print("\n--- step 4: rtt_start (before connect) ---")
    try:
        jl.rtt_start()
        print("  rtt_start OK")
    except Exception as exc:
        print(f"  rtt_start failed: {exc!r}")

    print("\n--- step 5: set_tif + set_speed ---")
    try:
        jl.set_tif(pylink.enums.JLinkInterfaces.SWD if INTERFACE == "SWD"
                   else pylink.enums.JLinkInterfaces.JTAG)
        jl.set_speed(SPEED)
        print(f"  set_tif={INTERFACE}, set_speed={SPEED} kHz OK")
    except Exception as exc:
        print(f"  set_tif/set_speed failed: {exc!r}")
        return 1

    print(f"\n--- step 6: connect target {TARGET} ---")
    try:
        jl.connect(TARGET)
        print(f"  connect OK, connected={jl.connected()}")
        print(f"  core_name={jl.core_name()}, core_id={hex(jl.core_id())}")
    except Exception as exc:
        print(f"  connect failed: {exc!r}")
        return 1

    # === 步骤 7：扫描 RTT 通道 ===
    print("\n--- step 7: scan RTT channels ---")
    try:
        # pylink 1.6.0 API
        num_up = jl.rtt_get_num_up_buffers()
        num_down = jl.rtt_get_num_down_buffers()
        print(f"  up buffers (target→host)={num_up}, down buffers (host→target)={num_down}")
        for i in range(num_up):
            try:
                desc = jl.rtt_get_buf_descriptor(i, up=True)
                print(f"  up[{i}]: name={desc.name!r}, size={desc.SizeOfBuffer}, flags={desc.Flags}")
            except Exception as exc:
                print(f"  up[{i}] desc failed: {exc!r}")
    except AttributeError as exc:
        print(f"  scan API not available in this pylink version: {exc!r}")
    except Exception as exc:
        print(f"  scan failed: {exc!r}")

    # === 步骤 8：从通道 0..3 各读 3 秒，看哪个有数据 ===
    print("\n--- step 8: poll rtt_read on channels 0..3 for 3s each ---")
    for ch in range(4):
        print(f"\n  >>> channel {ch}:")
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        total_bytes = 0
        end = time.time() + 3.0
        while time.time() < end:
            try:
                data = jl.rtt_read(ch, 4096)
            except Exception as exc:
                print(f"      rtt_read(ch={ch}) failed: {exc!r}")
                break
            if data:
                total_bytes += len(data)
                # data 是 list of int
                raw_bytes = bytes(data)
                text = decoder.decode(raw_bytes)
                print(f"      got {len(data)} bytes, raw[:40]={raw_bytes[:40]!r}, decoded={text!r}")
            time.sleep(0.1)
        print(f"      total {total_bytes} bytes on channel {ch}")

    # === 步骤 9：清理 ===
    print("\n--- step 9: cleanup ---")
    try:
        jl.rtt_stop()
        print("  rtt_stop OK")
    except Exception as exc:
        print(f"  rtt_stop failed: {exc!r}")
    try:
        jl.close()
        print("  close OK")
    except Exception as exc:
        print(f"  close failed: {exc!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
