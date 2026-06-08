#!/usr/bin/env python3
"""Benchmark speculative decoding modes for the cleanup workload on the 4B.

Runs the SAME set of realistic dictation-cleanup prompts through several
llama-server --spec-type configs (baseline / ngram / draft-mtp), measuring
wall-clock latency + server-reported tokens/sec + draft acceptance. Single
foreground process. Uses a SEPARATE port (8090) and kills its server by PID —
never pkill — so the live daemon on 8080 is untouched.

Usage: bench_spec.py
"""
from __future__ import annotations
import json, subprocess, sys, time, statistics, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_dataset import WRAP, cleanup_prompt

MODEL = str(Path.home() / ".local/share/voice-type/models/quill-4b-Q4_K_M.gguf")
PORT = "8090"
SYS = cleanup_prompt("verbatim")

# Pre-rendered ChatML with thinking OFF (assistant turn pre-seeded), matching the
# daemon's recipe. stop on <|im_end|>.
def render(raw: str) -> str:
    user = WRAP.format(raw=raw)
    return (f"<|im_start|>system\n{SYS}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n<think>\n\n</think>\n\n")

# Realistic dictation inputs, short -> long. Cleanup output ~ echoes input, so
# longer ones are where ngram/spec should help most (more tokens to draft).
PROMPTS = [
    "um so can you send the report by friday",
    "i was thinking we could grab lunch around noon",
    "this is fucking ridiculous the build broke again",
    "email me at john at gmail dot com",
    "what time does the pharmacy close on sunday",
    "so i was thinking we should probably push the launch to next week because the testing "
    "isnt done and the client wants more time to review everything before we ship",
    "okay so for the launch we need to do three things first finalize the pricing second "
    "update the landing page and third brief the support team before friday",
    "so basically what happened was i woke up late missed the bus had to call a cab and by "
    "the time i got to the office the standup was already over and everyone had left",
    "hey just following up on our chat from yesterday could you send over the updated contract "
    "by end of day i want to get it to legal before the weekend if at all possible thanks",
    "the deploy failed again this morning and im really not totally sure why it keeps doing that "
    "i checked the logs and it looks like the migration script is timing out on the production database",
    "i talked to sarah and michael about the trip to paris and they said they can make it in "
    "september but we need to book the flights soon before the prices go up any more than they already have",
    "so the quarterly numbers came in higher than expected revenue was up about twenty five percent "
    "quarter over quarter but our margins compressed a bit because of the new infrastructure spend",
]

def start(spec_args):
    cmd = ["llama-server", "-m", MODEL, "--host", "127.0.0.1", "--port", PORT,
           "-c", "8192", "-ngl", "99", "--parallel", "1"] + spec_args
    proc = subprocess.Popen(cmd, stdout=open("/tmp/bench_srv.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(180):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).status == 200:
                return proc
        except Exception:
            time.sleep(1)
    return proc

def complete(raw):
    body = json.dumps({"prompt": render(raw), "n_predict": 256, "temperature": 0.0,
                       "cache_prompt": False, "stop": ["<|im_end|>"]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    dt = (time.time() - t) * 1000
    return dt, r

def bench(label, spec_args):
    proc = start(spec_args)
    try:
        complete("warm up the model")   # warmup
        lat, tps, pred, draftacc = [], [], [], []
        for p in PROMPTS:
            dt, r = complete(p)
            tm = r.get("timings", {})
            lat.append(dt); tps.append(tm.get("predicted_per_second", 0))
            pred.append(tm.get("predicted_n", 0))
            # speculative acceptance, if the server reports it
            dn = tm.get("draft_n") or r.get("draft_n")
            da = tm.get("draft_n_accepted") or tm.get("draft_accepted_n") or r.get("draft_n_accepted")
            if dn:
                draftacc.append(da / dn if dn else 0)
        return {"label": label, "lat": lat, "tps": tps, "pred": pred,
                "acc": (statistics.mean(draftacc) if draftacc else None),
                "sample": complete(PROMPTS[5])[1].get("content", "")[:70]}
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except Exception: proc.kill()
        time.sleep(2)

CONFIGS = [
    ("baseline",      ["--spec-type", "none"]),
    ("ngram-simple",  ["--spec-type", "ngram-simple"]),
    ("ngram-map-k",   ["--spec-type", "ngram-map-k"]),
    ("draft-mtp",     ["--spec-type", "draft-mtp"]),
]

def main():
    print(f"model: {Path(MODEL).name}  | {len(PROMPTS)} prompts | port {PORT} (daemon on 8080 untouched)\n")
    base_lat = None
    results = []
    for label, args in CONFIGS:
        try:
            res = bench(label, args)
        except Exception as e:
            print(f"{label:14} FAILED: {e}\n"); continue
        mean = statistics.mean(res["lat"]); med = statistics.median(res["lat"])
        tps = statistics.mean(res["tps"]); ptot = sum(res["pred"])
        if base_lat is None and label == "baseline":
            base_lat = mean
        speed = f"{base_lat/mean:.2f}x" if base_lat else "-"
        acc = f" accept={res['acc']*100:.0f}%" if res["acc"] is not None else ""
        print(f"{label:14} mean={mean:6.0f}ms  median={med:6.0f}ms  {tps:5.0f} tok/s  "
              f"({ptot} tok gen)  vs-baseline={speed}{acc}")
        results.append((label, mean))
    print()
    if base_lat:
        best = min(results, key=lambda x: x[1])
        print(f"FASTEST: {best[0]} ({base_lat/best[1]:.2f}x vs baseline)")

if __name__ == "__main__":
    raise SystemExit(main())
