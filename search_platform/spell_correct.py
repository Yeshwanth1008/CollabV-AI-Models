"""
Per-token fuzzy spell correction against the live vocabulary. Only replaces
a token when a close-but-imperfect match exists — exact matches and
unrecognized-but-plausible tokens (rare skill names, etc.) are left alone
so we don't "correct" a correctly-spelled, just-uncommon term.
"""
import re

from rapidfuzz import fuzz, process

from .vocabulary import SearchVocabulary

TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+#.\-]*")
MIN_SCORE = 82  # 0-100; below this we assume "not a typo, just unknown"
MIN_TOKEN_LEN = 3


def correct_query(query: str, vocab: SearchVocabulary) -> tuple[str, bool]:
    """Returns (corrected_query, was_corrected)."""
    if not vocab.tokens:
        return query, False

    tokens = TOKEN_RE.findall(query)
    corrected_any = False
    out_tokens = []

    for tok in tokens:
        low = tok.lower()
        if len(low) < MIN_TOKEN_LEN or low in vocab.tokens:
            out_tokens.append(tok)
            continue
        match = process.extractOne(low, vocab.tokens, scorer=fuzz.WRatio)
        if match and match[1] >= MIN_SCORE and match[0] != low:
            out_tokens.append(match[0])
            corrected_any = True
        else:
            out_tokens.append(tok)

    if not corrected_any:
        return query, False

    # Rebuild by replacing tokens positionally, preserving other characters.
    corrected = query
    for orig, new in zip(tokens, out_tokens):
        if orig != new:
            corrected = re.sub(rf"\b{re.escape(orig)}\b", new, corrected, count=1)
    return corrected, True
