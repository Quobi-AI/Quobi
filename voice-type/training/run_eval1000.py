#!/usr/bin/env python3
"""Run the 1000-case comprehensive suite (data/eval1000.json) through a local
GGUF, model output ALONE (no scaffold), save every output + score, and print a
per-category scorecard. Single foreground process (sandbox-safe).

Designed to be *conclusive*: a slow request, a transient hiccup, or a server
that's slow to warm must never silently score a case 0 and masquerade as a
model regression. Infrastructure errors are tracked separately from content
failures, errored cases are retried, and a run with too many unrecovered
errors exits non-zero instead of printing a misleading scorecard.

Usage: run_eval1000.py <gguf> <label> [ngl]
Env:   EVAL_TIMEOUT (per-request seconds, default 180)
       EVAL_WORKERS (parallel requests, default 4)
       EVAL_RETRIES (retry passes for errored cases, default 2)
       EVAL_MAX_ERR_FRAC (abort threshold, default 0.02 = 2%)
Saves: eval1000_<label>.json   (full results — re-score / scaffold offline)
"""
from __future__ import annotations
import collections, json, os, subprocess, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval import run_checks                       # noqa: E402  (corrected matcher)
from voice_type.format import Formatter           # noqa: E402

GGUF = sys.argv[1]; LABEL = sys.argv[2]; NGL = sys.argv[3] if len(sys.argv) > 3 else "99"
PORT = "8062"
TIMEOUT = int(os.environ.get("EVAL_TIMEOUT", "180"))
WORKERS = int(os.environ.get("EVAL_WORKERS", "4"))
RETRIES = int(os.environ.get("EVAL_RETRIES", "2"))
MAX_ERR_FRAC = float(os.environ.get("EVAL_MAX_ERR_FRAC", "0.02"))

# A result whose output starts with this marker is an infra error (timeout,
# network, server fault) — NOT a model content failure. Tracked apart so it
# never quietly drags a category to 0%.
ERR_PREFIX = "<ERR "


def _wait_healthy(proc: subprocess.Popen, timeout_s: int = 300) -> None:
    """Block until llama-server answers /health, or abort the whole run. A
    server that never comes up used to fall through and score every case 0,
    which looked exactly like a catastrophic model regression."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"FATAL: llama-server exited (code {proc.returncode}) "
                             f"before becoming healthy — see /tmp/ev1k_srv.log")
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).status == 200:
                return
        except Exception:
            time.sleep(1)
    proc.terminate()
    raise SystemExit(f"FATAL: llama-server not healthy after {timeout_s}s — "
                     f"see /tmp/ev1k_srv.log. Not scoring (would be inconclusive).")


def _score(c: dict, out: str) -> dict:
    is_err = out.startswith(ERR_PREFIX)
    # Don't let an infra error pretend to be a content judgement.
    ch = {} if is_err else run_checks(c["raw"], out, c.get("checks", {}))
    return {"raw": c["raw"], "category": c["category"], "output": out,
            "checks": ch, "error": is_err,
            "passed": (not is_err) and all(ch.values()),
            "failed": ["<infra-error>"] if is_err else [k for k, v in ch.items() if not v]}


def main() -> int:
    proc = subprocess.Popen(
        ["llama-server", "-m", GGUF, "--host", "127.0.0.1", "--port", PORT,
         "-c", "8192", "-ngl", NGL, "--parallel", str(WORKERS)],
        stdout=open("/tmp/ev1k_srv.log", "w"), stderr=subprocess.STDOUT)
    try:
        _wait_healthy(proc)
        base = "http://127.0.0.1:" + PORT
        fmt = Formatter(api_key="local", model="local", base_url=base + "/v1",
                        style="verbatim", local_completion=True,
                        completion_url=base + "/completion", timeout_sec=TIMEOUT)
        fmt.clean("warmup")
        cases = json.loads((Path(__file__).resolve().parent / "data" / "eval1000.json").read_text())

        def run_one(c: dict) -> dict:
            try:
                out = fmt.clean(c["raw"])
            except Exception as e:                 # FormatError, timeouts, etc.
                out = f"{ERR_PREFIX}{e}>"
            return _score(c, out)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=WORKERS) as p:
            results = list(p.map(run_one, cases))

        # Retry errored cases — single-threaded so a contention-induced timeout
        # gets a fair, uncontended shot before we call it a real failure.
        for attempt in range(1, RETRIES + 1):
            errored = [i for i, r in enumerate(results) if r["error"]]
            if not errored:
                break
            print(f"  retry pass {attempt}: {len(errored)} errored case(s), "
                  f"single-threaded…", flush=True)
            for i in errored:
                results[i] = run_one(cases[i])
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except Exception: proc.kill()

    n = len(results)
    n_err = sum(r["error"] for r in results)
    n_ok = n - n_err
    npass = sum(r["passed"] for r in results)

    cat = collections.defaultdict(lambda: [0, 0, 0])  # [pass, total, err]
    for r in results:
        cat[r["category"]][1] += 1
        cat[r["category"]][0] += r["passed"]
        cat[r["category"]][2] += r["error"]

    print(f"\n===== {LABEL}  ({Path(GGUF).name}) =====")
    # Pass rate over *scoreable* cases (errors excluded), plus the raw count.
    scored_pct = (npass / n_ok * 100) if n_ok else 0.0
    print(f"OVERALL: {npass}/{n_ok} scoreable ({scored_pct:.1f}%)   "
          f"[{n_err} infra-error of {n}]")
    for k in sorted(cat, key=lambda x: cat[x][0] / max(1, cat[x][1] - cat[x][2])):
        pa, tot, er = cat[k]
        denom = max(1, tot - er)
        tag = f"  ⚠{er}err" if er else ""
        print(f"  {k:20} {pa:3}/{denom:<3} {pa/denom*100:5.0f}%{tag}")

    Path(f"eval1000_{LABEL}.json").write_text(json.dumps(
        {"label": LABEL, "n": n, "errors": n_err, "results": results}, indent=1))

    # A run riddled with unrecovered infra errors is NOT a conclusive result —
    # exit non-zero so a CI/automation step (or you) doesn't trust the number.
    if n_err > n * MAX_ERR_FRAC:
        print(f"\nINCONCLUSIVE: {n_err}/{n} ({n_err/n*100:.1f}%) infra errors "
              f"exceed the {MAX_ERR_FRAC*100:.0f}% threshold. Re-run "
              f"(raise EVAL_TIMEOUT / lower EVAL_WORKERS). Results saved but not trusted.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
