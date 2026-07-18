"""Launch-time measurement helper.

Measures process spawn -> app ready (window shown + one event-loop tick).
Writes results to scratch\\measure\\results.jsonl.

Usage:
  python scripts\\measure_launch.py --target build\\main.dist\\JLinkRTTViewer.exe --name baseline_standalone --runs 5
  python scripts\\measure_launch.py --target build\\onefile\\JLinkRTTViewer.exe --name onefile_cached --runs 5 --warmup 1

The target app must support the --startup-bench flag (prints LAUNCH_READY_TS).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT = 120  # generous for first onefile extraction


def _marker_path() -> Path:
    # exe (console disabled) can't write redirected stdout; main.py falls back to
    # writing this marker file from the app. It lives next to the app log under %APPDATA%.
    import os
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "JLinkRTTViewer" / "logs" / "launch_bench.txt"


def run_once(cmd: list) -> float:
    """Spawn target, return launch time in seconds."""
    marker = _marker_path()
    if marker.exists():
        marker.unlink()
    spawn_ts = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    ready_ts = None
    deadline = time.time() + TIMEOUT
    while True:
        rc = proc.poll()
        if marker.exists():
            for line in marker.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("LAUNCH_READY_TS="):
                    ready_ts = float(line.split("=", 1)[1])
                    break
            if ready_ts is not None:
                if rc is None:
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                break
        if rc is not None:
            break
        if time.time() > deadline:
            proc.kill()
            proc.wait()
            raise RuntimeError("target did not report ready within timeout")
        time.sleep(0.01)
    if ready_ts is None:
        raise RuntimeError(f"target exited without LAUNCH_READY_TS, rc={proc.returncode}")
    return ready_ts - spawn_ts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--warmup", type=int, default=1, help="warmup runs whose results are discarded")
    a = p.parse_args()

    cmd = [a.target]
    if a.target.lower().endswith("python.exe"):
        # direct-python baseline: python needs the script path
        cmd += [str(Path(__file__).resolve().parent.parent / "src" / "main.py")]
    cmd += ["--startup-bench"]
    for i in range(a.warmup):
        t = run_once(cmd)
        print(f"[warmup {i + 1}] {t:.3f}s", flush=True)

    times = []
    for i in range(a.runs):
        t = run_once(cmd)
        times.append(t)
        print(f"[run {i + 1}] {t:.3f}s", flush=True)
        time.sleep(1.0)  # let OS settle between runs

    times_sorted = sorted(times)
    median = times_sorted[len(times_sorted) // 2]
    record = {
        "name": a.name,
        "target": a.target,
        "times": times,
        "median": median,
        "min": min(times),
        "max": max(times),
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    out_dir = Path(__file__).resolve().parent.parent / "scratch" / "measure"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"MEDIAN {a.name}: {median:.3f}s  (min={min(times):.3f}, max={max(times):.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
