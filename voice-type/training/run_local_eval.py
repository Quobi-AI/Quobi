#!/usr/bin/env python3
"""Serve a local GGUF + run the eval in ONE foreground process.

Sandbox-safe: llama-server is a managed child of this process and is killed
before we exit, so nothing is left running to be reaped.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval import print_card, score_model, summarize  # noqa: E402

BIN = sys.argv[1] if len(sys.argv) > 1 else "/tmp/llama.cpp/build/bin/llama-server"
GGUF = sys.argv[2] if len(sys.argv) > 2 else "models/gguf/Qwen3.5-2B-Q4_K_M.gguf"
LABEL = sys.argv[3] if len(sys.argv) > 3 else "qwen35-2b-UNTRAINED"
OUT = sys.argv[4] if len(sys.argv) > 4 else "scorecard_base.json"
PORT = "8080"

log = open("/tmp/llama_server.log", "w")
proc = subprocess.Popen(
    [BIN, "-m", GGUF, "--host", "127.0.0.1", "--port", PORT,
     "-c", "4096", "-ngl", "0", "--threads", "16", "--parallel", "2",
     # Qwen3.5 is a thinking model — without these it spends all tokens in a
     # <think> block and returns empty content. Use the model's own template
     # and turn thinking OFF for the cleanup task.
     "--jinja", "--reasoning-format", "none",
     "--chat-template-kwargs", '{"enable_thinking": false}'],
    stdout=log, stderr=subprocess.STDOUT,
)
print(f"llama-server pid={proc.pid}, loading {GGUF} ...")

ready = False
for i in range(180):
    if proc.poll() is not None:
        print(f"!! server exited early (code {proc.returncode}); see /tmp/llama_server.log")
        print(Path("/tmp/llama_server.log").read_text()[-1500:])
        sys.exit(1)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
            if r.status == 200:
                ready = True
                print(f"server READY after {i}s")
                break
    except Exception:
        pass
    time.sleep(1)

if not ready:
    print("!! server never became ready")
    proc.terminate()
    sys.exit(1)

try:
    cases = json.loads(Path("data/eval_verbatim.json").read_text())
    print(f"scoring {len(cases)} cases against local {LABEL} ...")
    results = score_model(f"http://127.0.0.1:{PORT}/v1", "noauth", "local",
                          "verbatim", cases, workers=2)
    card = {"model": LABEL, "base_url": "local-llama-server", "style": "verbatim",
            "summary": summarize(results), "results": results}
    print_card(card)
    Path(OUT).write_text(json.dumps(card, indent=2))
    print(f"\nscorecard -> {OUT}")
    fails = [r for r in results if not r["passed"]]
    if fails:
        print(f"\n{len(fails)} case(s) failed — samples:")
        for r in fails[:8]:
            print(f"  [{r['category']}] failed {r['failed_checks']}")
            print(f"    raw: {r['raw'][:75]}")
            print(f"    out: {r['output'][:90]}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    print("server stopped.")
