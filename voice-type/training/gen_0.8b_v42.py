"""v4.2 0.8B — PURE VERBATIM. The 0.8B's only job is verbatim cleanup (strip
fillers, fix punctuation/caps, preserve exact words, self-correction, repetition,
profanity, foreign-passthrough). Everything the deterministic scaffold handles —
emails (@), URLs (dot->.), line-breaks (new line->\n), ellipsis, slashes,
contractions — is STRIPPED from training so the tiny model isn't confused by it.
Code identifiers (camelCase etc.) are also dropped (conversion, not verbatim).
Plain numbers stay. Less dilution => numbers recover; symbols are the scaffold's job."""
import json, random, re
from pathlib import Path
D = Path(__file__).resolve().parent / "data"
RNG = random.Random(421)
load = lambda p: [json.loads(l) for l in (D/p).open()]
out = lambda r: r["messages"][2]["content"]
raw = lambda r: r["messages"][1]["content"]

URL  = re.compile(r"\b[a-z0-9-]+\.(?:com|org|io|net|co|dev|ai|app|edu|gov|biz|me|info|tv)\b", re.I)
CODE = re.compile(r"[a-z][A-Z]|[a-z]_[a-z]|/api|\bnpm \b|\bgit \b|\.json\b|\.tsx?\b|\.py\b|JSON\.|docker ")
def is_conversion(r):
    o = out(r)
    return ("@" in o or "\n" in o or "..." in o
            or (" at " in o and " dot " in o)          # spelled-out email
            or URL.search(o) is not None
            or CODE.search(o) is not None)

base = load("train_0.8b_v3bak.jsonl")
v4   = load("train_0.8b.jsonl")
verbatim = [r for r in base if not is_conversion(r)]
dropped  = len(base) - len(verbatim)
foreign  = [r for r in v4 if any(ord(c) > 127 for c in out(r)) and not is_conversion(r)]
RNG.shuffle(foreign); foreign = foreign[:60]

merged = verbatim + foreign
seen, res = set(), []
for r in merged:
    k = (r.get("style"), raw(r))
    if k in seen: continue
    seen.add(k); res.append(r)
RNG.shuffle(res)
(D/"train_0.8b_v42.jsonl").write_text("\n".join(json.dumps(r) for r in res) + "\n")
print(f"v3 base={len(base)}  dropped {dropped} conversion/symbol examples (email/url/linebreak/ellipsis/code)")
print(f"pure-verbatim kept={len(verbatim)}  + foreign-passthrough={len(foreign)}")
print(f"==> train_0.8b_v42.jsonl = {len(res)} (pure verbatim)")
