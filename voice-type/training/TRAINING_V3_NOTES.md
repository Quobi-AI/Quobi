# Cleanup model — training set v3 (weakness-targeted)

`train_v3.jsonl` — **1,470 verbatim+tidy pairs** = the v2 set (1,245) + **228 new
weakness-targeted pairs authored by Claude Opus** (no API). Train any size
(0.8B / 2B / 4B) on this, 2 epochs, assistant-only loss masking, thinking-off.

## Why v3 — what a 642-test weakness eval found
All three models are **100% on comprehension** (never-answer-questions,
profanity-preserved, filler→empty, restraint, run-on breaking). The ONLY
weaknesses are mechanical transforms:

| weakness | model alone | fix |
|---|---|---|
| email/URL (`at gmail dot com`) | ~0% | **deterministic** (symbols.py) — model correctly leaves it |
| unambiguous contractions (`cant`) | ~25% | **deterministic** (symbols.fix_contractions, 0 regressions) |
| **code identifiers** (`use effect`→`useEffect`) | 48–70% | **TRAINING DATA** (68 new pairs) |
| **spoken numbers** (`four oh four`→`404`) | 8–17% | **TRAINING DATA** (39 new pairs) |
| **ambiguous contractions** (`it's`/`its`, `let's`/`lets`) | — | **TRAINING DATA** (44 new pairs, incl. negatives) |

Deterministic code can't safely guess code-casing, number-vs-word, or contraction
disambiguation — those need the model, so v3 teaches them. The ambiguous-
contraction pairs include NEGATIVES (`its way`, `lets you cancel`, `feeling ill`,
`they were`, `went well`, `I shed a tear`) so the model learns context, not a blind rule.

## Pairs with deterministic scaffold (already shipped in `voice_type/symbols.py`)
`normalize_symbols()` now also does `fix_contractions()` (safe 37-word dict) +
`exclamation point`/`semicolon`. Measured lift on identical model outputs:
0.8B 70.6%→79.9%, 2B 72%→82.7%, 4B 72.9%→84.6%, **zero regressions**. Port this
to the Android C++ `postprocess.cpp` so on-device matches.

## Expected after training on v3
Code-identifier, number, and ambiguous-contraction categories should climb
substantially (they were the only model-weak spots). Combined with the
deterministic scaffold, the cleanup quality should approach the 4B across the board
— at 0.8B size.
