#!/usr/bin/env python3
"""Benchmark a local cleanup GGUF: resource usage (VRAM/CPU/RAM) sampled DURING
the accuracy run, scored against the held-out eval set with the production
pipeline (Formatter local mode + normalize_symbols).

Usage: bench.py <gguf> <label> [ngl]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval import run_checks, summarize          # noqa: E402
from voice_type.format import Formatter           # noqa: E402
from voice_type.symbols import normalize_symbols  # noqa: E402

GGUF = sys.argv[1]
LABEL = sys.argv[2] if len(sys.argv) > 2 else "local"
NGL = sys.argv[3] if len(sys.argv) > 3 else "99"
PORT = "8071"
CLK = os.sysconf("SC_CLK_TCK")


def _vram_mib(pid: int) -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) == 2 and p[0].isdigit() and int(p[0]) == pid:
                return int(p[1])
    except Exception:
        pass
    return 0


def _rss_mib(pid: int) -> int:
    try:
        for l in open(f"/proc/{pid}/status"):
            if l.startswith("VmRSS"):
                return int(l.split()[1]) // 1024
    except Exception:
        pass
    return 0


def _cpu_ticks(pid: int) -> int:
    try:
        f = open(f"/proc/{pid}/stat").read().split()
        return int(f[13]) + int(f[14])
    except Exception:
        return 0


def main() -> int:
    proc = subprocess.Popen(
        ["llama-server", "-m", GGUF, "--host", "127.0.0.1", "--port", PORT,
         "-c", "4096", "-ngl", NGL],
        stdout=open("/tmp/bench_srv.log", "w"), stderr=subprocess.STDOUT)
    pid = proc.pid
    for _ in range(180):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(1)

    base = "http://127.0.0.1:" + PORT
    fmt = Formatter(api_key="local", model="local", base_url=base + "/v1",
                    style="verbatim", local_completion=True,
                    completion_url=base + "/completion")
    fmt.clean("warmup the model")               # load + warm
    idle_vram = _vram_mib(pid)                   # VRAM with model loaded, idle

    cases = json.loads(Path("data/eval_verbatim.json").read_text())

    # Resource sampler thread runs during the accuracy pass.
    samples = {"vram": [], "rss": [], "cpu": []}
    stop = threading.Event()

    def sampler():
        last_t, last_c = time.monotonic(), _cpu_ticks(pid)
        while not stop.is_set():
            time.sleep(0.25)
            now, c = time.monotonic(), _cpu_ticks(pid)
            dt = now - last_t
            cpu_pct = (c - last_c) / (dt * CLK) * 100 if dt > 0 else 0
            last_t, last_c = now, c
            samples["vram"].append(_vram_mib(pid))
            samples["rss"].append(_rss_mib(pid))
            samples["cpu"].append(cpu_pct)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    results, lats = [], []
    try:
        for case in cases:
            t = time.monotonic()
            out = normalize_symbols(fmt.clean(case["raw"]))   # full production path
            lats.append((time.monotonic() - t) * 1000)
            checks = run_checks(case["raw"], out, case.get("checks", {}))
            failed = [k for k, v in checks.items() if not v]
            results.append({"raw": case["raw"], "category": case["category"],
                            "output": out, "checks": checks, "passed": not failed,
                            "failed_checks": failed})
    finally:
        stop.set(); th.join(timeout=2)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    s = summarize(results)
    peak_vram = max(samples["vram"] or [idle_vram])
    peak_cpu = max(samples["cpu"] or [0])
    avg_cpu = sum(samples["cpu"]) / len(samples["cpu"]) if samples["cpu"] else 0
    peak_rss = max(samples["rss"] or [0])
    lats_sorted = sorted(lats)
    p50 = lats_sorted[len(lats_sorted) // 2]
    report = {
        "label": LABEL, "gguf": Path(GGUF).name, "ngl": NGL,
        "resources": {
            "vram_loaded_mib": idle_vram, "vram_peak_mib": peak_vram,
            "cpu_peak_pct": round(peak_cpu, 1), "cpu_avg_pct": round(avg_cpu, 1),
            "ram_rss_peak_mib": peak_rss,
        },
        "latency_ms": {"p50": round(p50), "min": round(min(lats)), "max": round(max(lats))},
        "accuracy": s,
    }
    print(json.dumps(report, indent=2))
    Path(f"bench_{LABEL}.json").write_text(json.dumps({**report, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
