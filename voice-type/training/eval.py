#!/usr/bin/env python3
"""Score a cleanup model against the held-out eval set — objective, per-check.

Provider-agnostic: points at any OpenAI-compatible endpoint, so you can score
the UNTRAINED base model and the TRAINED model the same way (serve each with
llama-server / vLLM and run this twice), then diff the scorecards.

  # serve + score the base model
  vllm serve ~/models/Qwen3.5-2B --port 8000        # or llama-server
  python eval.py --base-url http://localhost:8000/v1 --model Qwen3.5-2B \
      --out scorecard_base.json

  # serve + score the fine-tuned model
  vllm serve out/qwen35-2b-verbatim --port 8001
  python eval.py --base-url http://localhost:8001/v1 --model qwen35-2b-verbatim \
      --out scorecard_trained.json

  # side-by-side
  python eval.py --compare scorecard_base.json scorecard_trained.json

Checks are objective string assertions tied to each case (see data/eval_*.json).
Two checks run on EVERY case: no_refusal and no_meta (prompt leak / preamble).
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_dataset import WRAP, _api_key, _call, cleanup_prompt

_FILLERS = re.compile(r"\b(um+|uh+|er|hmm+)\b", re.IGNORECASE)
# Refusal-specific phrases. Deliberately NOT bare "i cannot"/"i can't" — those
# false-positive on legitimate content like "I cannot believe this happened".
_REFUSAL = (
    "i can't help", "i cannot help", "i can't assist", "i cannot assist",
    "i can't provide", "i cannot provide", "i can't do that", "i cannot do that",
    "i won't be able", "i'm not able to", "i am not able to", "i'm unable to",
    "i am unable to", "i'm sorry, but", "i am sorry, but", "as an ai",
    "i can't repeat", "i cannot repeat", "i won't repeat", "i can't comply",
    "i cannot comply", "i'm not comfortable", "i am not comfortable",
)
_META = ("<transcript>", "cleaned transcript", "here is the cleaned",
         "here's the cleaned", "i have cleaned", "i've cleaned",
         "as requested", "the cleaned version")


def _contains_ok(sub: str, out: str) -> bool:
    """Substring assertion for required tokens. Case-SENSITIVE by default so code
    identifiers stay strict (`useEffect` != `useeffect`), with two principled
    exceptions: (1) sentence-initial capitalization — `you're` matches `You're`
    via a first-letter-only case swap (mid-word case diffs still fail), and
    (2) spoken numbers — `1500` matches a thousands-grouped `1,500`."""
    if sub in out:
        return True
    if sub and (sub[0].swapcase() + sub[1:]) in out:   # sentence-start capital
        return True
    if sub.isdigit() and len(sub) > 3 and format(int(sub), ",") in out:
        return True
    return False


def run_checks(raw: str, out: str, checks: dict) -> dict:
    """Return {check_name: bool}. Always includes no_refusal + no_meta."""
    res: dict[str, bool] = {}
    low = out.lower()
    res["no_refusal"] = not any(p in low for p in _REFUSAL)
    res["no_meta"] = not any(p in low for p in _META)

    rw = max(len(raw.split()), 1)
    cw = len(out.split())

    if checks.get("empty"):
        res["empty"] = out.strip() == ""
        return res  # filler-only: nothing else to check
    res["non_empty"] = out.strip() != ""

    if checks.get("removes_fillers"):
        res["removes_fillers"] = not _FILLERS.search(out)
    if checks.get("keeps_question"):
        res["keeps_question"] = "?" in out
    if checks.get("ends_terminal"):
        res["ends_terminal"] = out.rstrip()[-1:] in (".", "!", "?")
    if "max_ratio" in checks:
        res["max_ratio"] = (cw / rw) <= checks["max_ratio"]
    if "min_ratio" in checks:
        res["min_ratio"] = (cw / rw) >= checks["min_ratio"]
    for sub in checks.get("preserves", []):          # profanity etc. (case-insens)
        res[f"preserves:{sub}"] = sub.lower() in low
    for sub in checks.get("must_contain", []) + checks.get("contains", []):  # identifiers/names (ALL required)
        res[f"contains:{sub}"] = _contains_ok(sub, out)
    if checks.get("any_of"):                          # ANY one acceptable form present
        opts = checks["any_of"]
        res[f"any_of:{opts[0]}"] = any(_contains_ok(s, out) for s in opts)
    for sub in checks.get("no_answer", []):          # must NOT have answered
        res[f"no_answer:{sub}"] = sub.lower() not in low
    return res


def score_model(base_url, key, model, style, cases, workers):
    system = cleanup_prompt(style)

    def one(case):
        try:
            out = _call(base_url, key, model, system, WRAP.format(raw=case["raw"]))
        except RuntimeError as e:
            out = f"<ERROR {e}>"
        checks = run_checks(case["raw"], out, case.get("checks", {}))
        failed = [k for k, v in checks.items() if not v]
        return {"raw": case["raw"], "category": case["category"],
                "output": out, "checks": checks,
                "passed": not failed, "failed_checks": failed}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, cases))
    return results


def summarize(results):
    import collections
    by_cat = collections.defaultdict(lambda: [0, 0])     # cat -> [pass, total]
    by_check = collections.defaultdict(lambda: [0, 0])   # check-type -> [pass, total]
    checks_pass = checks_total = 0
    for r in results:
        c = r["category"]
        by_cat[c][1] += 1
        by_cat[c][0] += 1 if r["passed"] else 0
        for name, ok in r["checks"].items():
            kind = name.split(":")[0]
            by_check[kind][1] += 1
            by_check[kind][0] += 1 if ok else 0
            checks_total += 1
            checks_pass += 1 if ok else 0
    cases_pass = sum(1 for r in results if r["passed"])
    return {
        "n_cases": len(results),
        "cases_fully_passed": cases_pass,
        "case_pass_rate": round(cases_pass / max(len(results), 1), 3),
        "check_pass_rate": round(checks_pass / max(checks_total, 1), 3),
        "by_category": {k: round(v[0] / v[1], 3) for k, v in sorted(by_cat.items())},
        "by_check": {k: round(v[0] / v[1], 3) for k, v in sorted(by_check.items())},
    }


def print_card(card):
    s = card["summary"]
    print(f"\n  model: {card['model']}  ({card['base_url']})")
    print(f"  cases fully passed : {s['cases_fully_passed']}/{s['n_cases']}  "
          f"({s['case_pass_rate']*100:.0f}%)")
    print(f"  individual checks  : {s['check_pass_rate']*100:.0f}% passed")
    print("  by category:")
    for k, v in s["by_category"].items():
        print(f"    {k:14} {v*100:5.0f}%")
    print("  by check type:")
    for k, v in s["by_check"].items():
        print(f"    {k:18} {v*100:5.0f}%")


def do_compare(a_path, b_path):
    a = json.loads(Path(a_path).read_text())
    b = json.loads(Path(b_path).read_text())
    print("=" * 64)
    print(f"COMPARE   A={a['model']}   vs   B={b['model']}")
    print("=" * 64)
    sa, sb = a["summary"], b["summary"]
    print(f"\n{'metric':24} {'A':>8} {'B':>8} {'Δ':>8}")
    print(f"{'case pass rate':24} {sa['case_pass_rate']*100:7.0f}% {sb['case_pass_rate']*100:7.0f}% "
          f"{(sb['case_pass_rate']-sa['case_pass_rate'])*100:+7.0f}")
    print(f"{'check pass rate':24} {sa['check_pass_rate']*100:7.0f}% {sb['check_pass_rate']*100:7.0f}% "
          f"{(sb['check_pass_rate']-sa['check_pass_rate'])*100:+7.0f}")
    cats = sorted(set(sa["by_category"]) | set(sb["by_category"]))
    print(f"\n{'category':24} {'A':>8} {'B':>8} {'Δ':>8}")
    for c in cats:
        va, vb = sa["by_category"].get(c, 0), sb["by_category"].get(c, 0)
        print(f"{c:24} {va*100:7.0f}% {vb*100:7.0f}% {(vb-va)*100:+7.0f}")
    chks = sorted(set(sa["by_check"]) | set(sb["by_check"]))
    print(f"\n{'check type':24} {'A':>8} {'B':>8} {'Δ':>8}")
    for c in chks:
        va, vb = sa["by_check"].get(c, 0), sb["by_check"].get(c, 0)
        print(f"{c:24} {va*100:7.0f}% {vb*100:7.0f}% {(vb-va)*100:+7.0f}")
    # cases A failed that B passed (the wins) and vice versa
    am = {r["raw"]: r for r in a["results"]}
    bm = {r["raw"]: r for r in b["results"]}
    wins = [r for raw, r in bm.items() if not am.get(raw, {}).get("passed", True) and r["passed"]]
    regr = [r for raw, r in bm.items() if am.get(raw, {}).get("passed", False) and not r["passed"]]
    print(f"\nB fixed {len(wins)} case(s) A failed; B regressed on {len(regr)}.")
    for r in wins[:8]:
        print(f"  [win  {r['category']}] {r['raw'][:70]}")
    for r in regr[:8]:
        print(f"  [REGR {r['category']}] {r['raw'][:70]}  -> failed {r['failed_checks']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", default="data/eval_verbatim.json")
    ap.add_argument("--style", default="verbatim",
                    choices=["verbatim", "tidy", "formatted"])
    ap.add_argument("--base-url", default="https://api.x.ai/v1")
    ap.add_argument("--key-env", default="")
    ap.add_argument("--model", default="grok-4.3")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"))
    args = ap.parse_args()

    if args.compare:
        do_compare(*args.compare)
        return 0

    cases = json.loads(Path(args.eval_set).read_text())
    # Local servers (llama-server/vLLM) don't need a key; only require one for
    # real remote providers.
    if "127.0.0.1" in args.base_url or "localhost" in args.base_url:
        key = "noauth"
    else:
        key = _api_key(args.key_env)
    print(f"scoring {len(cases)} cases | model={args.model} | {args.base_url}")
    results = score_model(args.base_url, key, args.model, args.style, cases, args.workers)
    card = {"model": args.model, "base_url": args.base_url, "style": args.style,
            "summary": summarize(results), "results": results}
    print_card(card)
    out = args.out or f"scorecard_{re.sub(r'[^a-zA-Z0-9]+','_',args.model)}.json"
    Path(out).write_text(json.dumps(card, indent=2))
    print(f"\n  scorecard -> {out}")
    # show a few failures to eyeball
    fails = [r for r in results if not r["passed"]]
    if fails:
        print(f"\n  {len(fails)} case(s) failed — samples:")
        for r in fails[:6]:
            print(f"    [{r['category']}] {r['failed_checks']}")
            print(f"      raw: {r['raw'][:80]}")
            print(f"      out: {r['output'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
