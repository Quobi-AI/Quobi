"""Deterministic spoken-symbol normalization, applied AFTER the cleanup LLM.

The LLM is told to convert spoken commands ("at gmail dot com" -> @gmail.com,
"slash" -> /) but does it inconsistently across models (Llama 70B is good;
GPT-5 nano leaves emails literal). Rather than depend on the model, we fill in
the high-confidence, pattern-anchored conversions in code so they're correct
regardless of which model ran. If the model already converted them, these
patterns simply don't match — no double conversion.

Conservative by design: only conversions that are unambiguous or anchored
enough not to mangle ordinary prose. Genuinely ambiguous spoken punctuation
("comma", "period" as words) is left to the LLM.
"""
from __future__ import annotations

import re

_TLDS = "com|org|net|io|gov|edu|co|dev|ai|app|me|info|biz|xyz|uk|us|ca"

# "john dot doe at gmail dot com" -> "john.doe@gmail.com"
_EMAIL_RE = re.compile(
    r"\b([a-z0-9]+(?:\s+dot\s+[a-z0-9]+)*)\s+at\s+"
    r"([a-z0-9]+(?:\s+dot\s+[a-z0-9]+)*)\s+dot\s+(" + _TLDS + r")\b",
    re.IGNORECASE,
)

# "google dot com" -> "google.com" (URL without an @). Anchored by a TLD so it
# won't fire on arbitrary "X dot Y".
_URL_RE = re.compile(
    r"\b([a-z0-9-]+(?:\s+dot\s+[a-z0-9-]+)*)\s+dot\s+(" + _TLDS + r")\b",
    re.IGNORECASE,
)


def _join_dots(s: str) -> str:
    return re.sub(r"\s+dot\s+", ".", s, flags=re.IGNORECASE)


def _email_sub(m: re.Match) -> str:
    return f"{_join_dots(m.group(1))}@{_join_dots(m.group(2))}.{m.group(3).lower()}"


def _url_sub(m: re.Match) -> str:
    return f"{_join_dots(m.group(1))}.{m.group(2).lower()}"


# The cleanup model often converts the spoken "dot com" -> ".com" (and repairs a
# Whisper-split domain like "g mail" -> "gmail") but LEAVES the spoken "at" as a
# word: "Jordan at gmail.com". That strips the " dot " anchor _EMAIL_RE needs, so
# the address never gets its "@". Catch the already-dotted form too.
#
# Unlike the spelled-out form ("dot com" is never said casually), "X at Y.com" is
# ambiguous — "look at google.com" is a website, not an email. So we only convert
# when the local part LOOKS like an email handle, decided in _email_dotted_sub:
# Capitalized (a name the model cased, e.g. "Jordan") or containing a dot/digit
# ("john.doe", "user1"). A bare lowercase verb like "look" is left alone.
# Optional leading recipient cue ("send TO rabih at gmail.com", "EMAIL ...") — a
# strong email signal that lets a lowercase handle through, since "<cue> X at
# Y.com" is virtually always an address (vs "look at google.com", which has no
# cue and a lowercase non-handle, so it's left alone).
_EMAIL_DOTTED_RE = re.compile(
    r"(?:\b(to|e-?mail|contact|cc|from|reach)\s+)?"
    r"\b([A-Za-z0-9._%+-]+)\s+at\s+"
    r"([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:" + _TLDS + r"))\b",
    re.IGNORECASE,
)


def _email_dotted_sub(m: re.Match) -> str:
    cue, local, domain = m.group(1), m.group(2), m.group(3)
    looks_like_handle = local[:1].isupper() or any(c.isdigit() or c == "." for c in local)
    if cue or looks_like_handle:
        prefix = f"{cue} " if cue else ""
        return f"{prefix}{local}@{domain}"
    return m.group(0)


# Small models sometimes write the "@" but leave a stray space before the
# domain ("alex@ gmail.com"). Collapse it so the address is well-formed. Anchored
# to word-char @ word-char so it won't touch a deliberate "@ handle".
_HALF_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])@\s+(?=[A-Za-z0-9])")

# Any finished email address -> lowercase. Models capitalize local parts when the
# words look like names ("Priya.Patel@work.com"); email is case-insensitive and
# convention is lowercase, so normalize it.
_ANY_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Spoken round numbers with an article that the models leave as words
# ("a hundred" / "one thousand"). Convert the bare forms only — the negative
# lookahead leaves compound numbers ("a hundred and fifty") to the model.
_ARTICLE_NUM = {"hundred": "100", "thousand": "1000", "million": "1000000"}
_ARTICLE_NUM_RE = re.compile(
    r"\b(?:a|one)\s+(hundred|thousand|million)\b(?!\s+(?:and|hundred|thousand)\b)",
    re.IGNORECASE,
)


# Unambiguous contractions: the no-apostrophe spelling is NOT a common English
# word with a different meaning (so this is safe to apply deterministically).
# Deliberately EXCLUDES ambiguous ones: its/it's, lets/let's, ill/I'll,
# well/we'll, were/we're, id/I'd, hed/he'd, shed/she'd, wed/we'd.
_CONTRACTIONS = {
    "cant": "can't", "wont": "won't", "dont": "don't", "doesnt": "doesn't",
    "didnt": "didn't", "isnt": "isn't", "wasnt": "wasn't", "arent": "aren't",
    "werent": "weren't", "havent": "haven't", "hasnt": "hasn't", "hadnt": "hadn't",
    "couldnt": "couldn't", "shouldnt": "shouldn't", "wouldnt": "wouldn't",
    "mustnt": "mustn't", "youre": "you're", "theyre": "they're", "im": "I'm",
    "ive": "I've", "youve": "you've", "weve": "we've", "theyve": "they've",
    "youll": "you'll", "theyll": "they'll", "whats": "what's", "thats": "that's",
    "theres": "there's", "heres": "here's", "wheres": "where's", "hes": "he's",
    "shes": "she's", "whos": "who's", "wouldve": "would've", "couldve": "could've",
    "shouldve": "should've",
}
_CONTR_RE = re.compile(r"\b(" + "|".join(_CONTRACTIONS) + r")\b", re.IGNORECASE)


def fix_contractions(text: str) -> str:
    """Restore apostrophes on unambiguous contractions (cant -> can't). Small
    cleanup models drop these; doing it in code fixes it for any model."""
    def sub(m: re.Match) -> str:
        rep = _CONTRACTIONS[m.group(0).lower()]
        if rep[0] == "I":            # I'm / I've — always capital I
            return rep
        return rep[0].upper() + rep[1:] if m.group(0)[0].isupper() else rep
    return _CONTR_RE.sub(sub, text)


def normalize_symbols(text: str) -> str:
    if not text:
        return text
    text = fix_contractions(text)
    # emails first (they contain the URL pattern as a sub-part)
    text = _EMAIL_RE.sub(_email_sub, text)
    text = _URL_RE.sub(_url_sub, text)
    # already-dotted emails the model half-converted ("Jordan at gmail.com")
    text = _EMAIL_DOTTED_RE.sub(_email_dotted_sub, text)
    # repair half-converted emails ("alex@ gmail.com"), then lowercase any
    # finished address so model-capitalized local parts are normalized.
    text = _HALF_EMAIL_RE.sub(r"\1@", text)
    text = _ANY_EMAIL_RE.sub(lambda m: m.group(0).lower(), text)
    # spoken round numbers the models leave as words ("a hundred" -> "100")
    text = _ARTICLE_NUM_RE.sub(lambda m: _ARTICLE_NUM[m.group(1).lower()], text)
    # pause-ellipsis: Whisper emits "..." for a spoken pause and the model keeps
    # it. Mid-sentence -> comma, trailing -> period (collapse 2+ dots / "…").
    text = text.replace("…", "...")
    text = re.sub(r"(\w)\s*\.{2,}\s+(\w)", r"\1, \2", text)   # word ... word -> "word, word"
    text = re.sub(r"\s*\.{2,}\s*$", ".", text.rstrip())       # trailing ... -> .
    text = re.sub(r"\s*\.{2,}\s*", " ", text)                 # any leftover run -> space

    # explicit line commands (standalone) -> real breaks, eating the spaces
    # around the command so words don't end up indented.
    text = re.sub(r"[ \t]*\bnew paragraph\b[ \t]*", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]*\bnew ?line\b[ \t]*", "\n", text, flags=re.IGNORECASE)

    # spoken punctuation the small models miss (the model handles comma/period/
    # question reliably; back-stop the rarer ones).
    text = re.sub(r"\s*\bexclamation (?:point|mark)\b", "!", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\bsemicolon\b\s*", "; ", text, flags=re.IGNORECASE)

    # tighten spaced slashes between word chars: "home / user" -> "home/user"
    text = re.sub(r"(\w)\s*/\s*(\w)", r"\1/\2", text)

    # collapse stray spaces around line breaks
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text
