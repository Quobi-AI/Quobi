# Tier-blocked training sets (massive, synthetic + existing)

Personalization styles are tier-blocked by model size:
- **0.8B (mobile):** verbatim only        -> `data/train_0.8b.jsonl`
- **2B (mid):** verbatim + tidy           -> `data/train_2b.jsonl`
- **4B (flagship):** verbatim+tidy+formatted -> `data/train_4b.jsonl`

Train each on its own file. 2 epochs, assistant-only loss masking, thinking-off,
single base = Qwen3.5-{0.8B,2B,4B}. The 0.8B is SINGLE-STYLE on purpose (it can't
reliably hold two styles apart at that size — proven by an A/B style test).

## How the data was made (no API)
- **verbatim** — reverse corruption: author a clean sentence, then programmatically
  dirty it into realistic raw STT (lowercase, strip punctuation+apostrophes, inject
  ONLY the removable disfluencies um/uh/er/hmm/like-tic/you-know-tic + stutters). The
  clean sentence IS the target -> guaranteed-correct pairs at scale. Plus 228 authored
  weak-spot pairs (code casing, spoken numbers->digits, ambiguous contractions incl.
  negatives) and the prior Grok verbatim.
- **tidy** — verbosify: a concise target + injected redundant filler-words
  (just/really/kind of/you know/basically) -> verbose raw. Tidy = strip the
  redundancy (the actual verbatim-vs-tidy difference). Plus authored + Grok tidy.
- **formatted** — enumeration/list + multi-paragraph templates -> structured output.
  Plus authored + Grok formatted.

All targets validated: 0 control chars, 0 fillers-left-in-target, 0 prompt leaks,
0 dupes, verbatim targets terminal-punctuated.

## Pair counts
| file | total | verbatim | tidy | formatted |
|---|---|---|---|---|
| train_0.8b.jsonl | 2378 | 2378 | – | – |
| train_2b.jsonl   | 4026 | 2378 | 1648 | – |
| train_4b.jsonl   | 4928 | 2378 | 1648 | 902 |

## Pair with the deterministic scaffold
`voice_type/symbols.py::normalize_symbols` already does fix_contractions (safe
dict) + email/url + exclamation/semicolon, applied AFTER the model. Port it to the
Android C++ postprocess for on-device parity.
