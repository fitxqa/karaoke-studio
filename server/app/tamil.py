"""Tamil script -> easy-to-read English letters ("Tanglish"), e.g. நன்றி -> nandri.

Follows how Tamil is actually pronounced: க/ச/ட/த/ப soften after vowels and
nasals (மகன் magan, நண்பா nanba) and stay hard when doubled (அக்கா akkaa).
"""
from __future__ import annotations

VOWELS = {"அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ee", "உ": "u", "ஊ": "oo", "எ": "e",
          "ஏ": "ae", "ஐ": "ai", "ஒ": "o", "ஓ": "o", "ஔ": "au"}
SIGNS = {"ா": "aa", "ி": "i", "ீ": "ee", "ு": "u", "ூ": "oo", "ெ": "e", "ே": "ae",
         "ை": "ai", "ொ": "o", "ோ": "o", "ௌ": "au", "ௗ": "au"}
PULLI = "்"
NASALS = set("ஙஞணநமன")
FIXED = {"ங": "ng", "ஞ": "nj", "ண": "n", "ந": "n", "ம": "m", "ய": "y", "ர": "r",
         "ல": "l", "வ": "v", "ழ": "zh", "ள": "l", "ன": "n", "ஜ": "j", "ஷ": "sh",
         "ஸ": "s", "ஹ": "h"}
# plosives by context: (word start, 2nd of a double, after nasal, between vowels, after other consonant, 1st of a double)
PLOSIVES = {
    "க": ("k", "k", "g", "g", "k", "k"),
    "ச": ("s", "ch", "j", "s", "s", "c"),
    "ட": ("t", "t", "d", "d", "t", "t"),
    "த": ("th", "th", "dh", "dh", "th", "t"),
    "ப": ("p", "p", "b", "p", "p", "p"),
    "ற": ("r", "r", "dr", "r", "r", "t"),
}
CONSONANTS = set(FIXED) | set(PLOSIVES)


def _tokens(text: str) -> list[tuple[str, str, str | None]]:
    """-> [("C", consonant, vowel or "" for pulli) | ("V", "", roman) | ("O", char, None)]"""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch in CONSONANTS:
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt == PULLI:
                out.append(("C", ch, ""))
                i += 2
                continue
            if nxt in SIGNS:
                out.append(("C", ch, SIGNS[nxt]))
                i += 2
                continue
            out.append(("C", ch, "a"))
        elif ch in VOWELS:
            out.append(("V", "", VOWELS[ch]))
        elif ch == "ஃ":
            out.append(("V", "", "h"))
        else:
            out.append(("O", ch, None))
        i += 1
    return out


def romanize(text: str) -> str:
    toks = _tokens(text)
    res = []
    for i, (kind, c, v) in enumerate(toks):
        if kind != "C":
            res.append(v if kind == "V" else c)
            continue
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if c in PLOSIVES:
            forms = PLOSIVES[c]
            if v == "" and nxt and nxt[0] == "C" and nxt[1] == c:
                base = forms[5]                       # first half of a double
            elif prev is None or prev[0] == "O":
                base = forms[0]
            elif prev[0] == "C" and prev[2] == "" and prev[1] == c:
                base = forms[1]
            elif prev[0] == "C" and prev[2] == "" and prev[1] in NASALS:
                base = forms[2]
            elif prev[0] == "C" and prev[2] == "":
                base = forms[4]
            else:
                base = forms[3]
        else:
            base = FIXED[c]
            if v == "" and c in ("ங", "ஞ") and nxt and nxt[0] == "C":
                base = "n"
        res.append(base + v)
    return "".join(res)
