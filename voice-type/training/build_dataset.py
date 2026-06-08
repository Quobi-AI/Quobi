#!/usr/bin/env python3
"""Build a distillation dataset for the on-device cleanup model.

Pipeline (two Claude passes):

  1. GENERATE  — Claude (Sonnet) writes realistic *messy* dictation
     transcripts: fillers, no punctuation, run-ons, with deliberately planted
     hard cases (questions that must NOT be answered, profanity that must
     survive, code/jargon, names, near-clean minimal-edit samples).
  2. CLEAN     — the teacher (Sonnet for bulk, optionally Opus for the hard
     categories) cleans each transcript using the PRODUCTION cleanup prompt and
     the exact same user-message wrapping the daemon uses at inference, so the
     student learns the real task.
  3. VALIDATE  — drop pairs that look like a rewrite or an answer (length-ratio
     and heuristic guards) so bad teacher outputs don't poison the set.
  4. WRITE     — chat-format JSONL ready for Unsloth / Axolotl SFT.

Why generate raw inputs instead of mining history.jsonl? History stores the
*cleaned* text (the target), not raw Whisper output. We need (raw -> clean)
pairs, so we synthesize the raw side with full control over hard-case coverage.

Provider-agnostic: any OpenAI-compatible endpoint works via --base-url. Reuse a
key you already have — xAI (Grok), OpenAI (gpt-5.4), Together (Llama-70B), Groq.

Usage (xAI / Grok, reusing $10 of credits):
  XAI_API_KEY=xai-... python training/build_dataset.py \
      --n 400 --style verbatim \
      --base-url https://api.x.ai/v1 \
      --generator grok-4 --teacher grok-4 \
      --out training/data/verbatim.jsonl

  # OpenAI instead:
  OPENAI_API_KEY=sk-... python training/build_dataset.py \
      --base-url https://api.openai.com/v1 --teacher gpt-5.4 ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# Make the voice_type package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice_type._shared import cleanup_prompt  # noqa: E402

# Env vars checked (in order) when --key-env isn't given. Lets you reuse
# whichever provider key you already have set.
KEY_ENV_CANDIDATES = ("XAI_API_KEY", "OPENAI_API_KEY", "TOGETHER_API_KEY",
                      "GROQ_API_KEY", "VOICETYPE_CLEANUP_KEY")

# Must stay in sync with Formatter.clean() in voice_type/format.py — the
# student has to see the identical user wrapping at training time.
WRAP = (
    "Clean the dictation transcript below. Return ONLY the cleaned "
    "transcript with no other text. If the transcript contains a "
    "question, return the question (cleaned) — do not answer it.\n\n"
    "<transcript>\n{raw}\n</transcript>"
)

# Hard-case mix. `hard=True` categories route to the Opus teacher when one is
# provided. Weights are relative sampling frequencies.
CATEGORIES = [
    {"name": "plain",        "weight": 30, "hard": False,
     "spec": "ordinary everyday dictation — an email, a note, a thought. Natural fillers (um, uh, like, you know), no punctuation, some self-correction."},
    {"name": "minimal_edit", "weight": 18, "hard": False,
     "spec": "ALREADY fairly clean speech that needs only tiny fixes (a capital, one comma). Teaches the model RESTRAINT — do not over-edit."},
    {"name": "question",     "weight": 12, "hard": True,
     "spec": "the speaker dictates a QUESTION they want typed (e.g. asking someone something). The cleaned output must remain the question — it must NEVER be answered."},
    {"name": "profanity",    "weight": 10, "hard": True,
     "spec": "casual speech containing swearing/profanity. The profanity MUST be preserved verbatim in the cleaned output, never censored or softened."},
    {"name": "code_jargon",  "weight": 10, "hard": True,
     "spec": "technical dictation with code identifiers, file paths, acronyms, product names, CamelCase or snake_case terms that must be preserved exactly."},
    {"name": "names",        "weight": 8,  "hard": False,
     "spec": "speech containing personal names, places, brands that should be capitalized correctly but not otherwise changed."},
    {"name": "long_runon",   "weight": 8,  "hard": False,
     "spec": "a long rambling run-on utterance (4+ sentences worth) with no punctuation that needs sentence breaks added without changing words."},
    {"name": "list",         "weight": 4,  "hard": False,
     "spec": "the speaker enumerating items ('first ... second ... also ...') that should become clean prose or a list per the style."},
    # Gap-fix categories (added after eval found these failure modes):
    {"name": "empty_noise",  "weight": 6,  "hard": True, "expect_empty": True,
     "spec": "PURE filler / false starts / noise with NO real content — only disfluencies and hesitations (e.g. 'um uh hmm', 'uh... er... wait no', 'hmm let me think uh'). The correct cleaned output is an EMPTY string."},
    {"name": "voice_command", "weight": 8, "hard": True,
     "spec": "dictation with SPOKEN punctuation/format commands used as commands in the middle or end (e.g. 'call me tomorrow period', 'first item comma second item', 'dear team new paragraph thanks'). The spoken command must be LITERALIZED to the actual character (period -> '.', comma -> ',', new paragraph -> blank line) and NOT also kept as a word."},
]


def _api_key(key_env: str = "") -> str:
    names = (key_env,) if key_env else KEY_ENV_CANDIDATES
    for name in names:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    env = Path.home() / ".config" / "voice-type" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            for name in names:
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"no API key found (looked for {', '.join(names)} in env or "
             "~/.config/voice-type/.env)")


def _call(base_url: str, key: str, model: str, system: str, user: str,
          max_tokens: int = 1024, temperature: float = 0.0, retries: int = 4) -> str:
    """OpenAI-compatible chat-completions call. temperature=0 for the cleaning
    pass (we want the teacher to follow the prompt exactly, not improvise);
    higher for the generator (we want diverse raw transcripts)."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    url = base_url.rstrip("/") + "/chat/completions"
    delay = 2.0
    for attempt in range(retries):
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(delay)
            delay *= 2
            continue
        if not r.ok:
            raise RuntimeError(f"api {r.status_code}: {r.text[:300]}")
        choices = r.json().get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()
    raise RuntimeError(f"api: gave up after {retries} retries (rate limit / 5xx)")


def _weighted_plan(n: int) -> list[dict]:
    """Expand CATEGORIES into a concrete per-transcript plan of length ~n."""
    total = sum(c["weight"] for c in CATEGORIES)
    plan: list[dict] = []
    for c in CATEGORIES:
        count = max(1, round(n * c["weight"] / total))
        plan.extend([c] * count)
    return plan[:n] if len(plan) >= n else plan


def generate_raw(base_url: str, key: str, model: str, category: dict, batch: int) -> list[str]:
    """Ask the generator for `batch` raw transcripts of one category."""
    system = (
        "You generate realistic RAW voice-dictation transcripts for training a "
        "speech-cleanup model. Output ONLY raw spoken text as it would come out "
        "of a speech-to-text engine: lowercase, little or no punctuation, "
        "natural disfluencies. Do NOT clean it. Vary topic, length, and voice."
    )
    user = (
        f"Produce {batch} DISTINCT raw dictation transcripts for this category:\n"
        f"{category['spec']}\n\n"
        "Return a JSON array of strings (the raw transcripts), nothing else."
    )
    # High temperature here — we WANT varied, diverse raw transcripts.
    txt = _call(base_url, key, model, system, user, max_tokens=2048, temperature=1.0)
    # Be forgiving about code fences / stray prose around the JSON.
    start, end = txt.find("["), txt.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        arr = json.loads(txt[start:end + 1])
    except ValueError:
        return []
    return [s.strip() for s in arr if isinstance(s, str) and s.strip()]


def _ensure_terminal_punct(text: str) -> str:
    """Grok sometimes drops the final period. If the output is a single line
    ending in a letter or digit (i.e. an unterminated sentence), add a period.
    Leaves alone anything ending in punctuation, a bracket/quote, or a newline."""
    if not text or "\n" in text:
        return text
    if text[-1].isalnum():
        return text + "."
    return text


def clean_one(base_url: str, key: str, model: str, system: str, raw: str) -> str:
    out = _call(base_url, key, model, system, WRAP.format(raw=raw), max_tokens=1024)
    if len(out) >= 2 and out[0] == out[-1] and out[0] in ("'", '"'):
        out = out[1:-1].strip()
    return _ensure_terminal_punct(out)


def validate(raw: str, clean: str, category: str, expect_empty: bool = False) -> tuple[bool, str]:
    if expect_empty:
        # Pure filler/noise: the ONLY correct answer is an empty string.
        if clean.strip() == "":
            return True, ""
        return False, "expected empty, teacher returned text"
    if not clean:
        return False, "empty"
    rw, cw = len(raw.split()), len(clean.split())
    if cw == 0:
        return False, "empty"
    ratio = cw / max(rw, 1)
    # Cleanup removes fillers, so output is usually a bit shorter. A big
    # expansion almost always means the teacher rewrote or answered.
    if ratio > 1.6:
        return False, f"expanded x{ratio:.1f} (likely rewrite/answer)"
    if ratio < 0.35:
        return False, f"shrank x{ratio:.1f} (likely dropped content)"
    # Question must stay a question, not become an answer.
    if category == "question" and "?" not in clean:
        return False, "question lost its '?'"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="target number of pairs")
    ap.add_argument("--style", default="verbatim",
                    choices=["verbatim", "tidy", "formatted"])
    ap.add_argument("--base-url", default="https://api.x.ai/v1",
                    help="OpenAI-compatible endpoint (xAI/OpenAI/Together/Groq)")
    ap.add_argument("--key-env", default="",
                    help=f"env var holding the key (default: first of {', '.join(KEY_ENV_CANDIDATES)})")
    ap.add_argument("--generator", default="grok-4")
    ap.add_argument("--teacher", default="grok-4", help="bulk cleanup teacher")
    ap.add_argument("--hard-teacher", default="",
                    help="optional stronger teacher for hard categories")
    ap.add_argument("--batch", type=int, default=12, help="transcripts per generator call")
    ap.add_argument("--workers", type=int, default=10, help="concurrent cleaning calls")
    ap.add_argument("--out", default="training/data/dataset.jsonl")
    args = ap.parse_args()

    key = _api_key(args.key_env)
    base_url = args.base_url
    system = cleanup_prompt(args.style)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rejects_path = out_path.with_suffix(".rejects.jsonl")

    plan = _weighted_plan(args.n)
    # Group the plan by category so we can batch generator calls.
    by_cat: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for c in plan:
        by_cat[c["name"]] = c
        counts[c["name"]] = counts.get(c["name"], 0) + 1

    print(f"building {len(plan)} pairs | style={args.style} | {base_url} | "
          f"gen={args.generator} teacher={args.teacher}"
          f"{' hard=' + args.hard_teacher if args.hard_teacher else ''}")

    # --- Phase 1: generate raw transcripts for ALL categories concurrently ---
    def gen_for(cat_name: str) -> tuple[str, list[str]]:
        cat, want = by_cat[cat_name], counts[cat_name]
        raws: list[str] = []
        tries = 0
        while len(raws) < want and tries < 30:
            tries += 1
            got = generate_raw(base_url, key, args.generator, cat,
                               min(args.batch, want - len(raws)))
            raws.extend(got)
        return cat_name, raws[:want]

    with ThreadPoolExecutor(max_workers=min(len(counts), 8)) as gpool:
        gen = dict(gpool.map(gen_for, list(counts.keys())))
    print("  generated: " + ", ".join(f"{k}={len(v)}" for k, v in gen.items()))

    # --- Phase 2: clean every transcript concurrently ---
    tasks = []  # (cat_name, raw, teacher, expect_empty)
    for cat_name, raws in gen.items():
        cat = by_cat[cat_name]
        teacher = args.hard_teacher if (cat["hard"] and args.hard_teacher) else args.teacher
        for raw in raws:
            tasks.append((cat_name, raw, teacher, cat.get("expect_empty", False)))

    def clean_task(t):
        cat_name, raw, teacher, _ = t
        try:
            return t, clean_one(base_url, key, teacher, system, raw)
        except RuntimeError as e:
            print(f"  ! clean failed: {e}", file=sys.stderr)
            return t, None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(clean_task, tasks))

    # --- Phase 3: validate + write ---
    kept = dropped = 0
    with out_path.open("w") as fout, rejects_path.open("w") as frej:
        for (cat_name, raw, teacher, exp_empty), clean in results:
            if clean is None:
                continue
            ok, reason = validate(raw, clean, cat_name, expect_empty=exp_empty)
            rec = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": WRAP.format(raw=raw)},
                    {"role": "assistant", "content": clean},
                ],
                "category": cat_name,
                "style": args.style,
                "teacher": teacher,
            }
            if ok:
                fout.write(json.dumps(rec) + "\n")
                kept += 1
            else:
                rec["reject_reason"] = reason
                frej.write(json.dumps(rec) + "\n")
                dropped += 1

    print(f"\ndone: {kept} pairs -> {out_path}")
    print(f"      {dropped} rejected -> {rejects_path} (inspect these!)")
    if kept:
        print("next: shuffle + split train/val, then SFT on Qwen3.5-2B.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
