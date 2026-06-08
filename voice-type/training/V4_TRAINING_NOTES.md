# Quobi Cleanup Models — v4 Training Notes (handoff)

Same recipe as v3 — only the **data changed**. Point `run_tier.sh` (already on
the box from v3) at these three files and run it. 2 epochs, assistant-only loss
masking, single-style 0.8B, export Q4_K_M GGUFs. ~2.5 h total.

## Files
| tier | file | rows | styles |
|---|---|---|---|
| 0.8B | `data/train_0.8b.jsonl` | 2757 | verbatim |
| 2B   | `data/train_2b.jsonl`   | 4682 | verbatim + tidy |
| 4B   | `data/train_4b.jsonl`   | 5834 | verbatim + tidy + formatted |

Regenerate anytime: `python3 gen_massive.py` (seed 42, reproducible).

## What's new vs v3 (and which tier gets it)
ALL tiers (verbatim):
- **Foreign passthrough** — clean FR/ES/DE/IT/PT but never translate (fixes 0.8B translating).
- **Anti-ellipsis + ellipsis-strip** — Whisper emits "..." for pauses; strip it (live bug).
- **Mid-phrase repetition collapse** — "too too" -> "too" (live bug).
- **Verbatim reinforcement** — density to hold fidelity.

0.8B ONLY:
- **email/url LEAVE-literal** + **line-break-literal** (the scaffold converts these; the
  0.8B must NOT attempt conversion — this is the v3 style-bleed guard).

2B + 4B ONLY (capability-gated, like email conversion):
- **email/URL CONVERSION** ("at..dot" -> @ / .).
- **Self-correction COLLAPSE** on explicit markers ("send it Monday, scratch that,
  Tuesday" -> "Send it Tuesday."). 0.8B keeps these literal — it can't reliably pick
  the span to delete.

4B ONLY:
- **Hard email** (multi-word hosts, plus-addressing).
- **+235 enterprise FORMATTED** — action items, status updates, structured emails
  (paragraph inference), mixed docs, plus 30 restraint cases (prose stays prose).
  Formatted pool rebalanced 902 -> 1137 (was consumer-skewed: 123 shopping lists, 0 action items).

## Validation gate (run `run_eval1000.py` on each v4 GGUF before shipping)
Ship only if ALL hold:
1. **2B email ≥85% & url ≥92%** (the main 2B uplift target).
2. **0.8B foreign ≥97%** and **paragraph ≥97%**.
3. **Self-correction collapse works on 2B/4B** (spot-check "scratch that" cases).
4. **ZERO regression** on the 24 currently-100% categories — especially the safety set:
   profanity, question (cleaned-not-answered), command_not_obeyed, prompt_injection, minimal_edit.
5. **0.8B email does NOT regress below v3** (the style-bleed canary).
6. **No over-formatting creep** — plain speech / questions must NOT become lists.

## Expected
0.8B ~90 -> ~93% · 2B ~95 -> ~97% · 4B ~97 -> ~97-98% (already near ceiling).

## Deployed alongside (host scaffold, no retrain needed)
`normalize_symbols` (voice_type/symbols.py) gained: half-email repair, lowercase
email local-parts, article-numbers ("a hundred"->100), and pause-ellipsis collapse.
Port these to the Android C++ postprocess for parity.
