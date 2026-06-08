#!/usr/bin/env python3
"""Run the weakness test set through a local GGUF (model output ALONE — no
normalize_symbols), report per-category + per-check pass rates, and dump every
failure grouped by category. Single foreground process (sandbox-safe).

Usage: weakness_eval.py <gguf> <label> [ngl]
"""
from __future__ import annotations
import collections, json, subprocess, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval import run_checks                       # noqa: E402
from voice_type.format import Formatter            # noqa: E402
from voice_type.symbols import normalize_symbols   # noqa: E402

GGUF = sys.argv[1]; LABEL = sys.argv[2]; NGL = sys.argv[3] if len(sys.argv) > 3 else "99"
SCAFFOLD = len(sys.argv) > 4 and sys.argv[4] == "scaffold"   # apply deterministic post-processing
PORT = "8061"

def main() -> int:
    proc = subprocess.Popen(
        ["llama-server", "-m", GGUF, "--host", "127.0.0.1", "--port", PORT,
         "-c", "8192", "-ngl", NGL, "--parallel", "4"],
        stdout=open("/tmp/wk_srv.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(180):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(1)
    base = "http://127.0.0.1:" + PORT
    fmt = Formatter(api_key="local", model="local", base_url=base + "/v1",
                    style="verbatim", local_completion=True, completion_url=base + "/completion")
    fmt.clean("warmup")
    cases = json.loads((Path(__file__).resolve().parent / "data" / "weakness_testset.json").read_text())

    results = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        def one(c):
            try:
                out = fmt.clean(c["raw"])
                if SCAFFOLD: out = normalize_symbols(out)   # deterministic post-processing
            except Exception as e: out = f"<ERR {e}>"
            ch = run_checks(c["raw"], out, c.get("checks", {}))
            return {"raw": c["raw"], "category": c["category"], "output": out,
                    "checks": ch, "passed": all(ch.values()),
                    "failed": [k for k, v in ch.items() if not v]}
        with ThreadPoolExecutor(max_workers=4) as p:
            results = list(p.map(one, cases))
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except Exception: proc.kill()

    # aggregate
    cat = collections.defaultdict(lambda: [0, 0])          # cat -> [pass, total]
    chk = collections.defaultdict(lambda: [0, 0])          # check-kind -> [pass, total]
    for r in results:
        cat[r["category"]][1] += 1; cat[r["category"]][0] += r["passed"]
        for name, ok in r["checks"].items():
            k = name.split(":")[0]; chk[k][1] += 1; chk[k][0] += ok
    npass = sum(r["passed"] for r in results)
    print(f"\n===== {LABEL}  ({Path(GGUF).name}) =====")
    print(f"OVERALL: {npass}/{len(results)} cases passed ({npass/len(results)*100:.1f}%)")
    print("\nby category (cases fully passed):")
    for k in sorted(cat, key=lambda x: cat[x][0]/cat[x][1]):
        p, t = cat[k]; print(f"  {k:14} {p:3}/{t:<3} {p/t*100:5.0f}%")
    print("\nby check kind:")
    for k in sorted(chk, key=lambda x: chk[x][0]/chk[x][1]):
        p, t = chk[k]; print(f"  {k:16} {p:3}/{t:<3} {p/t*100:5.0f}%")

    Path(f"weakness_{LABEL}{'_scaff' if SCAFFOLD else ''}.json").write_text(json.dumps(
        {"label": LABEL, "n": len(results),
         "by_category": {k: round(v[0]/v[1], 3) for k, v in cat.items()},
         "by_check": {k: round(v[0]/v[1], 3) for k, v in chk.items()},
         "results": results}, indent=1))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
