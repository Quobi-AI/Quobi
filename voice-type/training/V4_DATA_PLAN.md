# Quobi Cleanup Models — v4 Data Plan

Targeted data additions for the next training cycle, derived from the 1,000-case
eval (`data/eval1000.json`, results in `eval1000_{0.8b,2b,4b}.json`). v3 is
already strong (true accuracy ~90 / 95 / 97% +scaffold); v4 closes the *genuine,
teachable* gaps only. **The 4B needs essentially nothing — this plan is mostly a
2B uplift + two 0.8B bug-fixes.**

## Guiding principle (do NOT repeat the v3 0.8B regression)
The 0.8B showed **style-bleed** when fed symbol/conversion data: it started
half-converting emails (`john@ gmail dot com`). At 0.8B capacity, generative
symbol conversion fights verbatim fidelity.

> **Rule: the 0.8B gets NO new email/URL/number *conversion* examples.** Symbols
> on the 0.8B are the deterministic scaffold's job (`normalize_symbols`). The
> 0.8B only gets *passthrough* and *leave-literal* behaviors, which reinforce —
> not fight — its verbatim identity. Conversion data goes to 2B/4B only.

## The gaps, ranked by ROI

| # | Gap | Tier(s) | v3 now | target | Why teachable |
|---|---|---|---|---|---|
| 1 | **Email/URL conversion** | 2B (+4B) | 2B 64/82% | ~90/95% | 2B has the capacity, just lacks examples (4B proves it) |
| 2 | **Foreign passthrough (no-translate)** | 0.8B | 83% | ~100% | 0.8B *translates* FR/ES/DE → EN; a clear behavior to unteach |
| 3 | **Line-break leave-literal** | 0.8B | 88% | ~100% | 0.8B turns "new line" into a comma; teach it to leave the phrase literal so the scaffold converts |
| 4 | **Ambiguous/multi-word-host email** | 4B | 89% | ~93% | a few harder forms; diminishing returns |
| 5 | Verbatim reinforcement | 0.8B | high | hold | guard against drift while adding #2/#3 |

`number` "a hundred"→100 is **already fixed in the scaffold** (v3.1 `_ARTICLE_NUM_RE`)
— NOT a training target. `self_correction` is a *behavior choice* (verbatim keep),
not a defect — only add collapse examples if we deliberately want that feature
(see "Optional" below).

---

## New data pools

### Pool A — Foreign passthrough  (0.8B + 2B + 4B)  ~90 examples
**Behavior taught:** clean disfluencies + punctuation/capitalization, **keep the
original language, never translate, never answer.**
Languages: French, Spanish, German, Italian, Portuguese (~18 each).
Generation: author a clean sentence per language, inject native fillers
(`euh`/`eh`/`ähm`/`ehm`), target = same sentence cleaned in the same language.

```
in : "bonjour euh je voulais vous dire que le projet avance bien"
out: "Bonjour, je voulais vous dire que le projet avance bien."
in : "necesito el eh informe para la presentación del viernes"
out: "Necesito el informe para la presentación del viernes."
in : "guten tag ähm können wir das budget besprechen"
out: "Guten Tag, können wir das Budget besprechen?"
```
Negative guard (5–10 of them): an English instruction *embedded* in foreign text
must still NOT trigger translation/answering — model treats it as text.

### Pool B — Line-break leave-literal  (0.8B only)  ~30 examples
**Behavior taught:** when the user says "new line"/"new paragraph", the 0.8B
leaves the phrase as literal words (the scaffold turns it into `\n`). It must
NOT replace it with a comma/period or drop it.

```
in : "dear team new line we have an update"
out: "Dear team new line we have an update."      # scaffold -> "Dear team\nwe have an update."
in : "first point is done new paragraph now the second point"
out: "First point is done new paragraph now the second point."
```
> NOTE: 2B/4B keep their v3 behavior (emit real `\n\n` themselves) — this pool is
> 0.8B-exclusive and is the ONE place the tiers diverge on paragraphs.

### Pool C — Email conversion  (2B + 4B only)  ~90 examples
Spoken → address, every common shape. Reverse-generated from real-looking
addresses so the target is exact.

```
in : "email me at jordan at company dot io"          out: "Email me at jordan@company.io."
in : "reach support at acme dot org"                  out: "Reach support@acme.org."
in : "my address is sarah dot lee at outlook dot com" out: "My address is sarah.lee@outlook.com."
in : "ping marcus underscore t at startup dot ai"     out: "Ping marcus_t@startup.ai."
```
Coverage: single + dotted + underscored local parts; 12 domains × 8 TLDs;
"reach/contact/email/send to" lead-ins; lowercase local part enforced in target.

### Pool D — URL/path conversion  (2B + 4B only)  ~50 examples
```
in : "go to docs dot python dot org"                  out: "Go to docs.python.org."
in : "the repo is github dot com slash team slash app" out: "The repo is github.com/team/app."
in : "visit shop dot example dot co"                  out: "Visit shop.example.co."
```
Coverage: bare domains, subdomains, `slash` paths, common TLDs.

### Pool E — Hard email edge  (4B only)  ~25 examples
Multi-word hosts, plus-addressing, the genuinely-ambiguous "name at host"
disambiguated by an explicit "email"/"address" cue.
```
in : "her email is priya dot patel at my company dot com" out: "Her email is priya.patel@mycompany.com."
in : "send it to billing plus invoices at vendor dot net" out: "Send it to billing+invoices@vendor.net."
```

### Pool F — Verbatim reinforcement  (0.8B only)  ~40 examples
More authored contraction / false-start / repetition / profanity-preserve cases
to hold verbatim fidelity steady while A/B are added. No new behaviors — just
density so the small model doesn't drift.

---

## Tier allocation (additions only; v3 files are the base)

| pool | 0.8B (verbatim) | 2B (verb+tidy) | 4B (verb+tidy+fmt) |
|---|---|---|---|
| A foreign passthrough (~90) | ✅ | ✅ | ✅ |
| B line-break literal (~30) | ✅ | ❌ (2B emits `\n`) | ❌ |
| C email conversion (~90) | ❌ **(style-bleed guard)** | ✅ | ✅ |
| D url conversion (~50) | ❌ **(style-bleed guard)** | ✅ | ✅ |
| E hard email edge (~25) | ❌ | ❌ | ✅ |
| F verbatim reinforce (~40) | ✅ | ✅ | ✅ |

**Approx net adds:** 0.8B +160 · 2B +270 · 4B +295 (on top of v3's
2378 / 4026 / 4928). Keeps the same tier-blocked structure as `gen_massive.py`.

## Generation
Extend `gen_massive.py` with pools A–F (authored cores + templated expansion +
reverse-corruption, same idioms already in the file). Seed RNG. Re-emit the 3
tier files. ~30 min of generation, no GPU.

## Training (on the 96 GB box)
Identical recipe to v3 (`run_tier.sh`): 2 epochs, assistant-only loss masking,
single-style 0.8B, export Q4_K_M GGUFs. ~2.5 h total.

## Validation (gate before shipping)
Re-run `run_eval1000.py` on all three v4 GGUFs. **Ship only if:**
1. Gaps move: 2B email ≥85% & url ≥92%; 0.8B foreign ≥97% & paragraph ≥97%.
2. **Zero regression** on the 24 currently-100% categories (esp. the safety set:
   profanity, question, command_not_obeyed, prompt_injection, minimal_edit).
3. 0.8B email does NOT regress below v3 (the style-bleed canary).

## Expected outcome
- 0.8B ~90 → **~93%** (foreign + paragraph fixed; symbols stay scaffold-owned)
- 2B   ~95 → **~97%** (email/url uplift)
- 4B   ~97 → **~97–98%** (marginal; mostly unchanged — it's already at ceiling)

## Optional / explicitly deferred
- **Self-correction collapse** ("scratch that X" → drop X) as a *tidy/formatted*
  feature for 2B/4B only. This is a product decision, not a bug fix — keep
  verbatim (0.8B) untouched. Only build if we want collapsing as a selling point.
- More large/compound number coverage — low ROI, partially scaffold-able.
