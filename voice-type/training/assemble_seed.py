#!/usr/bin/env python3
"""Assemble hand-authored (raw, clean) seed pairs into training JSONL.

The pairs in data/seed_<style>.json were authored in-session by a strong
teacher (Claude). This wraps them through the SAME production prompt, user
wrapping, and validation guards as build_dataset.py, so the seed set is
byte-for-byte the same format as a Grok/OpenAI run — and the seed labels get
sanity-checked by the same guards.

Usage:
  python training/assemble_seed.py --style verbatim
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Reuse the real prompt, wrapping, and validators (no API calls happen on import).
from build_dataset import WRAP, cleanup_prompt, validate  # noqa: E402  (same dir)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="verbatim",
                    choices=["verbatim", "tidy", "formatted"])
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    seed = json.loads((here / "data" / f"seed_{args.style}.json").read_text())
    system = cleanup_prompt(args.style)
    out_path = here / "data" / f"{args.style}.jsonl"
    rej_path = here / "data" / f"{args.style}.rejects.jsonl"

    kept = dropped = 0
    with out_path.open("w") as fout, rej_path.open("w") as frej:
        for p in seed:
            raw, clean, cat = p["raw"], p["clean"], p.get("category", "plain")
            ok, reason = validate(raw, clean, cat)
            rec = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": WRAP.format(raw=raw)},
                    {"role": "assistant", "content": clean},
                ],
                "category": cat,
                "style": args.style,
                "teacher": "claude-opus-4-8 (in-session seed)",
            }
            if ok:
                fout.write(json.dumps(rec) + "\n")
                kept += 1
            else:
                rec["reject_reason"] = reason
                frej.write(json.dumps(rec) + "\n")
                dropped += 1
                print(f"  REJECT [{cat}] {reason}: {raw[:60]}...")

    print(f"done: {kept} pairs -> {out_path.name}")
    if dropped:
        print(f"      {dropped} rejected -> {rej_path.name}")
    else:
        print("      0 rejected — all seed labels passed the guards.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
