#!/usr/bin/env python3
"""Cleanup-model regression harness.

Feeds the test battery straight through the REAL cleanup model (same prompt
composition + Formatter the daemon uses), for each style: verbatim, tidy,
formatted. Auto-checks the deterministic properties and prints every output
so the style-specific behavior can be eyeballed.

Run:  make test-cleanup      (or: .venv/bin/python tests/cleanup_regression.py)

Tests the LLM cleanup ONLY — not Whisper. The audio-only test (#9 silent-tap
hallucination) is handled by the deterministic filter elsewhere and isn't here.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# make the package importable when run from repo root or tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from voice_type.format import Formatter  # noqa: E402

STYLES = ["verbatim", "tidy", "formatted"]
# Override with: python tests/cleanup_regression.py <model>
#   e.g. llama-3.1-8b-instant  (much higher free-tier daily token budget)
MODEL = sys.argv[1] if len(sys.argv) > 1 else "llama-3.3-70b-versatile"

NL = "\n"


def has(out: str, *subs: str) -> bool:
    o = out.lower()
    return all(s.lower() in o for s in subs)


def didnt_answer(out: str) -> bool:
    """Heuristic: the question was echoed, not answered."""
    o = out.lower()
    answer_tells = ["i'm doing", "i am doing", "doing well", "doing great",
                    "as an ai", "i don't have", "i cannot", "i can help",
                    "sure,", "of course"]
    return "?" in out and not any(t in o for t in answer_tells)


# Each test: input text + a list of (label, check-fn) that must hold in ALL
# styles. Style-specific nuance is left for the human eye (printed below).
TESTS = [
    {
        "n": 1, "name": "filler + false start",
        "input": "um my name is uh Robbie and I I I wanted to like talk about the thing",
        "checks": [
            ("filler removed", lambda o: "um " not in o.lower() and "uh " not in o.lower()),
            ("kept 'Robbie'", lambda o: has(o, "robbie")),
            ("kept the point", lambda o: has(o, "talk about the thing")),
        ],
    },
    {
        "n": 2, "name": "question (must NOT answer)",
        "input": "how are you doing are you doing fine",
        "checks": [
            ("did not answer", didnt_answer),
        ],
    },
    {
        "n": 3, "name": "self-correction",
        "input": "let's meet on tuesday no wednesday at five",
        "checks": [
            ("kept the correction (wednesday)", lambda o: has(o, "wednesday")),
            ("kept the time", lambda o: has(o, "five") or "5" in o),
        ],
    },
    {
        "n": 4, "name": "run-on rambling",
        "input": "so basically what happened was I went to the store and then um I forgot my wallet so I had to go back home and grab it and then come back",
        "checks": [
            ("content preserved", lambda o: has(o, "store") and has(o, "wallet") and has(o, "home")),
            ("filler removed", lambda o: " um " not in o.lower()),
        ],
    },
    {
        "n": 5, "name": "profanity (uncensored)",
        "input": "this is fucking ridiculous I can't believe this shit",
        "checks": [
            ("kept 'fucking'", lambda o: "fuck" in o.lower()),
            ("kept 'shit'", lambda o: "shit" in o.lower()),
            ("no asterisk censoring", lambda o: "*" not in o),
        ],
    },
    {
        "n": 6, "name": "voice commands (slash, new line)",
        "input": "the path is home slash user slash projects new line thanks",
        "checks": [
            ("slash -> /", lambda o: has(o, "home/user/projects")),
            ("new line -> newline char", lambda o: NL in o.strip()),
            ("did not type the word 'slash'", lambda o: "slash" not in o.lower()),
        ],
    },
    {
        "n": 7, "name": "symbol commands (email, parens)",
        "input": "email me at john dot doe at gmail dot com open paren work close paren",
        "checks": [
            ("-> john.doe@gmail.com", lambda o: "john.doe@gmail.com" in o.lower()),
            ("parens -> (work)", lambda o: "(work)" in o.lower()),
        ],
    },
    {
        "n": 8, "name": "real 'like' vs filler 'like'",
        "input": "I like the way you like things to be done",
        "checks": [
            ("kept both real 'like's", lambda o: o.lower().count("like") >= 2),
        ],
    },
    {
        "n": 10, "name": "disfluency storm",
        "input": "hey my name is um umm how are you doing are you doing fine no no no I hope you're doing fine",
        "checks": [
            ("did not answer the question", didnt_answer),
            ("filler removed", lambda o: "umm" not in o.lower()),
            ("kept the 'no no no' sentiment", lambda o: "no" in o.lower()),
        ],
    },
]


def clean_with_retry(fmt: Formatter, text: str, tries: int = 6) -> str:
    """Free-tier friendly: on a 429, honor the 'try again in Xs' hint and
    retry so the battery completes instead of bailing."""
    for attempt in range(tries):
        try:
            return fmt.clean(text)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" not in msg or attempt == tries - 1:
                raise
            m = re.search(r"try again in ([\d.]+)s", msg)
            wait = float(m.group(1)) + 0.8 if m else 8.0
            time.sleep(min(wait, 30))
    raise RuntimeError("unreachable")


def run_style(style: str, key: str) -> tuple[int, int]:
    fmt = Formatter(api_key=key, model=MODEL, style=style, temperature=0.0)
    passed = total = 0
    print(f"\n{'=' * 64}\n  STYLE: {style.upper()}\n{'=' * 64}")
    for t in TESTS:
        try:
            out = clean_with_retry(fmt, t["input"])
        except Exception as e:  # noqa: BLE001
            print(f"\n  [{t['n']}] {t['name']}: REQUEST FAILED: {e}")
            continue
        results = [(label, fn(out)) for label, fn in t["checks"]]
        ok = all(r for _, r in results)
        passed += sum(1 for _, r in results if r)
        total += len(results)
        mark = "PASS" if ok else "FAIL"
        emdash = " ⚠em-dash" if ("—" in out or "–" in out) else ""
        print(f"\n  [{t['n']}] {t['name']}  [{mark}]{emdash}")
        print(f"      in : {t['input']!r}")
        print(f"      out: {out!r}")
        for label, r in results:
            print(f"      {'✓' if r else '✗'} {label}")
    print(f"\n  -- {style}: {passed}/{total} checks passed --")
    return passed, total


def main() -> int:
    # load key the same way the daemon does
    cfg_env = Path.home() / ".config" / "voice-type" / ".env"
    load_dotenv(cfg_env if cfg_env.is_file() else None)
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        print("GROQ_API_KEY not set (looked in ~/.config/voice-type/.env and env).")
        return 2

    grand_p = grand_t = 0
    for style in STYLES:
        p, t = run_style(style, key)
        grand_p += p
        grand_t += t

    print(f"\n{'=' * 64}\n  TOTAL: {grand_p}/{grand_t} automated checks passed across all styles")
    print("  (style-specific behavior — verbatim minimalism, tidy grammar,")
    print("   formatted bullets — read the outputs above to judge.)")
    print('=' * 64)
    return 0 if grand_p == grand_t else 1


if __name__ == "__main__":
    sys.exit(main())
