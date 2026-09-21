"""Prompt injection guard layer.

Two directions:
1. Input guard — blocks messages with prompt injection before LLM.
2. Output guard — detects system prompt leaks in LLM response.

Configurable via env vars and admin API.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Unicode normalization strategy ────────────────────────────────────────
# 1. Bounded fixpoint over the whole pipeline: URL → HTML → escape (one pass
#    each) → invisible-char strip → NFKC + confusables map, until stable
#    (≤5 rounds). A single pass per family in a fixed order is not enough:
#    a stage can spawn syntax only an EARLIER decoder understands
#    (escape → "%", entity → "%", fullwidth ％ → "%"/＆ → "&"/＼ → "\\"),
#    and stripping can reveal encoded syntax ("\\u0025\\u200b69" → "%69"
#    after the URL stage ran). The fixpoint closes the whole class.
# 2. Full confusables table for Latin targets, generated offline from Unicode
#    confusables.txt (UTS #39, version 18.0.0, 2026-08-06) on top of NFKC.
#    Filter: single-codepoint source → single lowercase ASCII letter target,
#    excluding NFKC-covered entries (fullwidth/math alphanumerics stay with
#    NFKC). Manual additions the table lacks as ASCII targets:
#    ε → e (confusables: → ꞓ), χ → x (no source line), п → n (confusables:
#    п → π, π is not an ASCII target). σ → o and υ → u follow confusables.txt
#    final sigma → c (NFKC(lunate sigma) = final sigma interposes, so the
#    table's lunate → c entry never fires for NFKC'd input; ς itself is not
#    a confusables.txt source).
#    (overriding the earlier manual σ → c / υ → y).
#    Regenerate: download https://www.unicode.org/Public/security/latest/confusables.txt,
#    keep only lines "SRC ; TGT ; MA" with one-codepoint SRC/TGT, TGT in [a-z],
#    drop NFKC(SRC) == TGT, merge the manual additions above.
# 3. Rule-based invisible-char stripping (category Cf + FE00–FE0F + two Cn
#    exceptions) instead of a manual codepoint list.
# 4. Collapse whitespace variations so the .{0,20} regex gap matches across
#    tabs, newlines, non-breaking spaces, etc.
CONFUSABLES_MAP: dict[str, str] = {
    # target 'a' (21 sources)
    "\u0251": "a",
    "\u0391": "a",
    "\u03b1": "a",
    "\u0410": "a",
    "\u0430": "a",
    "\u13aa": "a",
    "\u15c5": "a",
    "\ua4ee": "a",
    "\uab64": "a",
    "\U000102a0": "a",
    "\U00016f40": "a",
    "\U0001d6a8": "a",
    "\U0001d6c2": "a",
    "\U0001d6e2": "a",
    "\U0001d6fc": "a",
    "\U0001d71c": "a",
    "\U0001d736": "a",
    "\U0001d756": "a",
    "\U0001d770": "a",
    "\U0001d790": "a",
    "\U0001d7aa": "a",
    # target 'b' (23 sources)
    "\u0184": "b",
    "\u0392": "b",
    "\u0412": "b",
    "\u042c": "b",
    "\u07d5": "b",
    "\u13cf": "b",
    "\u13f4": "b",
    "\u1472": "b",
    "\u15af": "b",
    "\u15f7": "b",
    "\u2c82": "b",
    "\ua4d0": "b",
    "\ua557": "b",
    "\ua7b4": "b",
    "\U00010282": "b",
    "\U000102a1": "b",
    "\U00010301": "b",
    "\U0001031c": "b",
    "\U0001d6a9": "b",
    "\U0001d6e3": "b",
    "\U0001d71d": "b",
    "\U0001d757": "b",
    "\U0001d791": "b",
    # target 'c' (19 sources)
    "\u03c2": "c",  # ς final sigma
    "\u03c3": "c",  # σ sigma — manual override, UTS #39 target is 'o'
    "\u03f2": "c",  # ϲ lunate sigma
    "\u03f9": "c",
    "\u0421": "c",
    "\u0441": "c",
    "\u1004": "c",
    "\u105a": "c",
    "\u13df": "c",
    "\u1c83": "c",
    "\u1d04": "c",
    "\u2ca4": "c",
    "\u2ca5": "c",
    "\ua4da": "c",
    "\uabaf": "c",
    "\U000102a2": "c",
    "\U00010302": "c",
    "\U00010415": "c",
    "\U0001043d": "c",
    "\U0001051b": "c",
    # target 'd' (8 sources)
    "\u0501": "d",
    "\u13a0": "d",
    "\u13e7": "d",
    "\u146f": "d",
    "\u15de": "d",
    "\u15ea": "d",
    "\ua4d2": "d",
    "\ua4d3": "d",
    # target 'e' (18 sources)
    "\u0395": "e",
    "\u03b5": "e",
    "\u0415": "e",
    "\u0435": "e",
    "\u04bd": "e",
    "\u13ac": "e",
    "\u2d39": "e",
    "\ua4f0": "e",
    "\ua5cb": "e",
    "\uab32": "e",
    "\U00010286": "e",
    "\U000118a6": "e",
    "\U000118ae": "e",
    "\U0001d6ac": "e",
    "\U0001d6e6": "e",
    "\U0001d720": "e",
    "\U0001d75a": "e",
    "\U0001d794": "e",
    # target 'f' (18 sources)
    "\u017f": "f",
    "\u0192": "f",
    "\u0284": "f",
    "\u03dc": "f",
    "\u0584": "f",
    "\u07d3": "f",
    "\u15b4": "f",
    "\u1e9d": "f",
    "\ua4dd": "f",
    "\ua798": "f",
    "\ua799": "f",
    "\uab35": "f",
    "\U00010287": "f",
    "\U000102a5": "f",
    "\U00010525": "f",
    "\U000118a2": "f",
    "\U000118c2": "f",
    "\U0001d7ca": "f",
    # target 'g' (8 sources)
    "\u018d": "g",
    "\u0261": "g",
    "\u050c": "g",
    "\u0581": "g",
    "\u13c0": "g",
    "\u13f3": "g",
    "\u1d83": "g",
    "\ua4d6": "g",
    # target 'h' (17 sources)
    "\u0397": "h",
    "\u041d": "h",
    "\u04ba": "h",
    "\u04bb": "h",
    "\u0570": "h",
    "\u10b9": "h",
    "\u13bb": "h",
    "\u13c2": "h",
    "\u157c": "h",
    "\u2c8e": "h",
    "\ua4e7": "h",
    "\U000102cf": "h",
    "\U0001d6ae": "h",
    "\U0001d6e8": "h",
    "\U0001d722": "h",
    "\U0001d75c": "h",
    "\U0001d796": "h",
    # target 'i' (17 sources)
    "\u0131": "i",
    "\u0269": "i",
    "\u026a": "i",
    "\u03b9": "i",
    "\u0456": "i",
    "\u0582": "i",
    "\u13a5": "i",
    "\u2c93": "i",
    "\ua647": "i",
    "\uab75": "i",
    "\U000118c3": "i",
    "\U0001d6a4": "i",
    "\U0001d6ca": "i",
    "\U0001d704": "i",
    "\U0001d73e": "i",
    "\U0001d778": "i",
    "\U0001d7b2": "i",
    # target 'j' (11 sources)
    "\u0237": "j",
    "\u037f": "j",
    "\u03f3": "j",
    "\u0408": "j",
    "\u0458": "j",
    "\u0575": "j",
    "\u13ab": "j",
    "\u148d": "j",
    "\ua4d9": "j",
    "\ua7b2": "j",
    "\U0001d6a5": "j",
    # target 'k' (12 sources)
    "\u039a": "k",
    "\u041a": "k",
    "\u13e6": "k",
    "\u16d5": "k",
    "\u2c94": "k",
    "\ua4d7": "k",
    "\U00010518": "k",
    "\U0001d6b1": "k",
    "\U0001d6eb": "k",
    "\U0001d725": "k",
    "\U0001d75f": "k",
    "\U0001d799": "k",
    # target 'l' (60 sources)
    "\u0196": "l",
    "\u01c0": "l",
    "\u0399": "l",
    "\u0406": "l",
    "\u04c0": "l",
    "\u04cf": "l",
    "\u05d5": "l",
    "\u05df": "l",
    "\u0627": "l",
    "\u07ca": "l",
    "\u13de": "l",
    "\u14aa": "l",
    "\u16c1": "l",
    "\u16d0": "l",
    "\u2110": "l",
    "\u2111": "l",
    "\u2c92": "l",
    "\u2cd0": "l",
    "\u2d4a": "l",
    "\u2d4f": "l",
    "\ua4e1": "l",
    "\ua4f2": "l",
    "\ua56f": "l",
    "\ua781": "l",
    "\ua7ae": "l",
    "\ua7fe": "l",
    "\ufe8d": "l",
    "\ufe8e": "l",
    "\uff29": "l",
    "\U0001028a": "l",
    "\U00010309": "l",
    "\U0001041b": "l",
    "\U0001050e": "l",
    "\U00010526": "l",
    "\U00010926": "l",
    "\U00010c3e": "l",
    "\U00010ca5": "l",
    "\U000118a3": "l",
    "\U000118b2": "l",
    "\U00016d63": "l",
    "\U00016f16": "l",
    "\U00016f28": "l",
    "\U0001d408": "l",
    "\U0001d43c": "l",
    "\U0001d470": "l",
    "\U0001d4d8": "l",
    "\U0001d540": "l",
    "\U0001d574": "l",
    "\U0001d5a8": "l",
    "\U0001d5dc": "l",
    "\U0001d610": "l",
    "\U0001d644": "l",
    "\U0001d678": "l",
    "\U0001d6b0": "l",
    "\U0001d6ea": "l",
    "\U0001d724": "l",
    "\U0001d75e": "l",
    "\U0001d798": "l",
    "\U0001ee00": "l",
    "\U0001ee80": "l",
    # target 'm' (16 sources)
    "\u039c": "m",
    "\u03fa": "m",
    "\u041c": "m",
    "\u13b7": "m",
    "\u15f0": "m",
    "\u16d6": "m",
    "\u2c98": "m",
    "\ua4df": "m",
    "\U000102b0": "m",
    "\U00010311": "m",
    "\U00010c21": "m",
    "\U0001d6b3": "m",
    "\U0001d6ed": "m",
    "\U0001d727": "m",
    "\U0001d761": "m",
    "\U0001d79b": "m",
    # target 'n' (13 sources)
    "\u039d": "n",
    "\u043f": "n",
    "\u0578": "n",
    "\u057c": "n",
    "\u2c9a": "n",
    "\ua4e0": "n",
    "\U00010513": "n",
    "\U00011abe": "n",
    "\U0001d6b4": "n",
    "\U0001d6ee": "n",
    "\U0001d728": "n",
    "\U0001d762": "n",
    "\U0001d79c": "n",
    # target 'o' (78 sources)
    "\u039f": "o",
    "\u03bf": "o",
    "\u03ed": "o",
    "\u041e": "o",
    "\u043e": "o",
    "\u0555": "o",
    "\u0585": "o",
    "\u05e1": "o",
    "\u0647": "o",
    "\u06be": "o",
    "\u06c1": "o",
    "\u06d5": "o",
    "\u07cb": "o",
    "\u0840": "o",
    "\u0b20": "o",
    "\u0d20": "o",
    "\u101d": "o",
    "\u10ff": "o",
    "\u110b": "o",
    "\u11bc": "o",
    "\u12d0": "o",
    "\u1a45": "o",
    "\u1c82": "o",
    "\u1cbf": "o",
    "\u1d0f": "o",
    "\u1d11": "o",
    "\u2c9e": "o",
    "\u2c9f": "o",
    "\u2d54": "o",
    "\u3147": "o",
    "\ua4f3": "o",
    "\uab3d": "o",
    "\ufba6": "o",
    "\ufba7": "o",
    "\ufba8": "o",
    "\ufba9": "o",
    "\ufbaa": "o",
    "\ufbab": "o",
    "\ufbac": "o",
    "\ufbad": "o",
    "\ufee9": "o",
    "\ufeea": "o",
    "\ufeeb": "o",
    "\ufeec": "o",
    "\uffb7": "o",
    "\U00010292": "o",
    "\U000102ab": "o",
    "\U0001030f": "o",
    "\U00010404": "o",
    "\U0001042c": "o",
    "\U000104c2": "o",
    "\U000104ea": "o",
    "\U00010516": "o",
    "\U0001092c": "o",
    "\U00010c17": "o",
    "\U00010d07": "o",
    "\U00011124": "o",
    "\U000118b5": "o",
    "\U000118c8": "o",
    "\U000118d7": "o",
    "\U00016ae9": "o",
    "\U0001d6b6": "o",
    "\U0001d6d0": "o",
    "\U0001d6d4": "c",  # 𝛔 NFKC→σ→c (public-path regression, see header)
    "\U0001d6f0": "o",
    "\U0001d70a": "o",
    "\U0001d70e": "c",  # 𝜎 NFKC→σ→c
    "\U0001d72a": "o",
    "\U0001d744": "o",
    "\U0001d748": "c",  # 𝝈 NFKC→σ→c
    "\U0001d764": "o",
    "\U0001d77e": "o",
    "\U0001d782": "c",  # 𝞂 NFKC→σ→c
    "\U0001d79e": "o",
    "\U0001d7b8": "o",
    "\U0001d7bc": "c",  # 𝞼 NFKC→σ→c
    "\U0001ee24": "o",
    "\U0001ee84": "o",
    # target 'p' (31 sources)
    "\u00fe": "p",
    "\u01bf": "p",
    "\u03a1": "p",
    "\u03c1": "p",
    "\u03f1": "p",
    "\u03f8": "p",
    "\u0420": "p",
    "\u0440": "p",
    "\u13e2": "p",
    "\u146d": "p",
    "\u2ca2": "p",
    "\u2ca3": "p",
    "\u2cce": "p",
    "\u2ccf": "p",
    "\ua4d1": "p",
    "\U00010295": "p",
    "\U0001d6b8": "p",
    "\U0001d6d2": "p",
    "\U0001d6e0": "p",
    "\U0001d6f2": "p",
    "\U0001d70c": "p",
    "\U0001d71a": "p",
    "\U0001d72c": "p",
    "\U0001d746": "p",
    "\U0001d754": "p",
    "\U0001d766": "p",
    "\U0001d780": "p",
    "\U0001d78e": "p",
    "\U0001d7a0": "p",
    "\U0001d7ba": "p",
    "\U0001d7c8": "p",
    # target 'q' (5 sources)
    "\u051a": "q",
    "\u051b": "q",
    "\u0563": "q",
    "\u0566": "q",
    "\u2d55": "q",
    # target 'r' (15 sources)
    "\u01a6": "r",
    "\u024c": "r",
    "\u0433": "r",
    "\u13a1": "r",
    "\u13d2": "r",
    "\u1587": "r",
    "\u1d26": "r",
    "\u2c85": "r",
    "\ua4e3": "r",
    "\uab47": "r",
    "\uab48": "r",
    "\uab81": "r",
    "\U000104b4": "r",
    "\U00016a19": "r",
    "\U00016f35": "r",
    # target 's' (20 sources)
    "\u01bd": "s",
    "\u0405": "s",
    "\u0455": "s",
    "\u054f": "s",
    "\u0d1f": "s",
    "\u10bd": "s",
    "\u10fd": "s",
    "\u13d5": "s",
    "\u13da": "s",
    "\u1cbd": "s",
    "\ua4e2": "s",
    "\ua576": "s",
    "\ua731": "s",
    "\uabaa": "s",
    "\U00010296": "s",
    "\U00010420": "s",
    "\U00010448": "s",
    "\U000118c1": "s",
    "\U00016ad6": "s",
    "\U00016f3a": "s",
    # target 't' (19 sources)
    "\u03a4": "t",
    "\u0422": "t",
    "\u07e0": "t",
    "\u13a2": "t",
    "\u2ca6": "t",
    "\u3112": "t",
    "\u4e05": "t",
    "\ua4d4": "t",
    "\ua50b": "t",
    "\U00010297": "t",
    "\U000102b1": "t",
    "\U00010315": "t",
    "\U000118bc": "t",
    "\U00016f0a": "t",
    "\U0001d6bb": "t",
    "\U0001d6f5": "t",
    "\U0001d72f": "t",
    "\U0001d769": "t",
    "\U0001d7a3": "t",
    # target 'u' (20 sources)
    "\u028b": "u",
    "\u054d": "u",
    "\u057d": "u",
    "\u1200": "u",
    "\u144c": "u",
    "\u1d1c": "u",
    "\ua4f4": "u",
    "\ua79f": "u",
    "\uab4e": "u",
    "\uab52": "u",
    "\U000104ce": "u",
    "\U000104f6": "u",
    "\U000118b8": "u",
    "\U000118d8": "u",
    "\U00016f42": "u",
    "\U0001d6d6": "y",  # 𝛖 NFKC→υ→y (public-path regression, see header)
    "\U0001d710": "y",  # 𝜐 NFKC→υ→y
    "\U0001d74a": "y",  # 𝝊 NFKC→υ→y
    "\U0001d784": "y",  # 𝞄 NFKC→υ→y
    "\U0001d7be": "y",  # 𝞾 NFKC→υ→y
    # target 'v' (22 sources)
    "\u03bd": "v",
    "\u0474": "v",
    "\u0475": "v",
    "\u05d8": "v",
    "\u13d9": "v",
    "\u142f": "v",
    "\u1d20": "v",
    "\u2d38": "v",
    "\ua4e6": "v",
    "\ua6df": "v",
    "\uaba9": "v",
    "\U0001051d": "v",
    "\U00010c1f": "v",
    "\U00011706": "v",
    "\U000118a0": "v",
    "\U000118c0": "v",
    "\U00016f08": "v",
    "\U0001d6ce": "v",
    "\U0001d708": "v",
    "\U0001d742": "v",
    "\U0001d77c": "v",
    "\U0001d7b6": "v",
    # target 'w' (17 sources)
    "\u026f": "w",
    "\u0448": "w",
    "\u0461": "w",
    "\u051c": "w",
    "\u051d": "w",
    "\u0561": "w",
    "\u13b3": "w",
    "\u13d4": "w",
    "\u1d21": "w",
    "\u2cbd": "w",
    "\ua4ea": "w",
    "\ua7fa": "w",
    "\uab83": "w",
    "\uaba4": "w",
    "\U0001170a": "w",
    "\U0001170e": "w",
    "\U0001170f": "w",
    # target 'x' (25 sources)
    "\u03a7": "x",
    "\u03c7": "x",
    "\u0425": "x",
    "\u0445": "x",
    "\u1541": "x",
    "\u157d": "x",
    "\u16b7": "x",
    "\u1763": "x",
    "\u1cf5": "x",
    "\u2cac": "x",
    "\u2d5d": "x",
    "\ua4eb": "x",
    "\ua7b3": "x",
    "\U00010290": "x",
    "\U000102b4": "x",
    "\U00010317": "x",
    "\U00010527": "x",
    "\U00010c13": "x",
    "\U00010c82": "x",
    "\U00010cc2": "x",
    "\U0001d6be": "x",
    "\U0001d6f8": "x",
    "\U0001d732": "x",
    "\U0001d76c": "x",
    "\U0001d7a6": "x",
    # target 'y' (38 sources)
    # target 'y' (39 sources)
    "\u0263": "y",
    "\u03c5": "y",  # υ upsilon — manual override, UTS #39 target is 'u'
    "\u028f": "y",
    "\u03a5": "y",
    "\u03b3": "y",
    "\u03d2": "y",
    "\u0423": "y",
    "\u0443": "y",
    "\u04ae": "y",
    "\u04af": "y",
    "\u07cc": "y",
    "\u10e7": "y",
    "\u13a9": "y",
    "\u13bd": "y",
    "\u1d8c": "y",
    "\u1eff": "y",
    "\u213d": "y",
    "\u2ca8": "y",
    "\u2ca9": "y",
    "\u311a": "y",
    "\u4e2b": "y",
    "\ua4ec": "y",
    "\uab5a": "y",
    "\U000102b2": "y",
    "\U00010c20": "y",
    "\U000118a4": "y",
    "\U000118c4": "y",
    "\U000118dc": "y",
    "\U00016f43": "y",
    "\U0001d6bc": "y",
    "\U0001d6c4": "y",
    "\U0001d6f6": "y",
    "\U0001d6fe": "y",
    "\U0001d730": "y",
    "\U0001d738": "y",
    "\U0001d76a": "y",
    "\U0001d772": "y",
    "\U0001d7a4": "y",
    "\U0001d7ac": "y",
    # target 'z' (20 sources)
    "\u0396": "z",
    "\u10cd": "z",
    "\u13c3": "z",
    "\u1d22": "z",
    "\u2c6b": "z",
    "\u2c6c": "z",
    "\u2c8c": "z",
    "\u2c8d": "z",
    "\u2d2d": "z",
    "\ua4dc": "z",
    "\ua6c9": "z",
    "\uab93": "z",
    "\U00010507": "z",
    "\U000118a9": "z",
    "\U00011abc": "z",
    "\U0001d6ad": "z",
    "\U0001d6e7": "z",
    "\U0001d721": "z",
    "\U0001d75b": "z",
    "\U0001d795": "z",
}


_CONFUSABLES_TRANSLATION = str.maketrans(CONFUSABLES_MAP)

# Fixpoint round budget for ``_normalize_for_guard``. One round per wrapper
# layer is the cost model (see the header block): escape/NFKC → "&" costs one
# round, every ``&amp;`` layer one more, so depth N needs N+1 rounds. 16 leaves
# headroom over any chain reachable in practice while a dirty, deeply wrapped
# message still converges long before the cap (each round shrinks the text).
_NORMALIZE_MAX_ROUNDS = 16


def _normalize_homoglyphs(text: str) -> str:
    """Normalize homoglyphs to Latin via NFKC + confusables table.

    1. NFKC: fullwidth, mathematical alphanumerics, compatibility chars → ASCII
    2. CONFUSABLES_MAP: single-char confusables NFKC does not map directly
       (Greek, Cyrillic, Armenian, Cherokee, … → lowercase ASCII letters)
    """
    nfkc = unicodedata.normalize("NFKC", text)
    return nfkc.translate(_CONFUSABLES_TRANSLATION)


# ── Escape sequence decoding ──────────────────────────────────────────────
# Attackers embed escape sequences (\x69, \u0069, \151) to spell out injection
# keywords.  LLMs and many runtimes decode these before processing; regex
# guard patterns see the literal backslash and do NOT match.
# Single pass per family: nested chains (\x5c\x75…) are expanded by the
# external fixpoint in _normalize_for_guard, not by an internal loop.

_HEX_RUN_ESCAPE = re.compile(r"(?:\\x[0-9a-fA-F]{2})+")
_UNICODE2_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_UNICODE4_ESCAPE = re.compile(r"\\U([0-9a-fA-F]{8})")
_OCTAL_ESCAPE = re.compile(r"\\([0-3][0-7]{0,2})")  # \0 to \377


def _decode_hex_run(match: re.Match[str]) -> str:
    """Decode one run of consecutive \\xNN as a UTF-8 byte sequence.

    A multi-byte pair like \\xD1\\x96 is the UTF-8 encoding of Cyrillic і
    (U+0456); per-byte Latin-1 decoding would yield "Ñ\\x96" and the homoglyph
    map would never see the codepoint. Invalid UTF-8 falls back to per-byte
    Latin-1 so ASCII-only runs keep their meaning.
    """
    pairs = re.findall(r"\\x([0-9a-fA-F]{2})", match.group(0))
    raw = bytes(int(p, 16) for p in pairs)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _decode_unicode_codepoint(m: re.Match[str]) -> str:
    """Decode a \\uNNNN/\\UNNNNNNNN codepoint only when it is a valid scalar.

    Out-of-range values (\\UFFFFFFFF, \\U00110000 — just over the 0x10FFFF
    max) and surrogates are left as the literal match: an invalid escape must
    not raise ValueError and fail the whole turn (fail-closed, but an
    availability loss a trivial 8-digit sequence triggers); the rest of the
    text is scanned further.
    """
    value = int(m.group(1), 16)
    if 0 <= value <= 0x10FFFF and not (0xD800 <= value <= 0xDFFF):
        return chr(value)
    return m.group(0)


def _decode_escapes(text: str) -> str:
    r"""Decode common escape sequences that LLMs/interpreters would expand.

    Single pass (the external fixpoint in _normalize_for_guard iterates):
    - \xNN   (hex byte, e.g. \x69 → i; consecutive runs decode as UTF-8)
    - \uNNNN (4-digit unicode, e.g. \u0069 → i)
    - \UNNNNNNNN (8-digit unicode, valid scalar values only)
    - \NNN   (octal, e.g. \151 → i)
    """
    result = _HEX_RUN_ESCAPE.sub(_decode_hex_run, text)
    result = _UNICODE2_ESCAPE.sub(_decode_unicode_codepoint, result)
    result = _UNICODE4_ESCAPE.sub(_decode_unicode_codepoint, result)
    result = _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), result)
    return result


# ── URL encoding decoding ───────────────────────────────────────────────
# Attackers use %xx to encode keyword characters (e.g., %69 = 'i').
_URL_RUN_ESCAPE = re.compile(r"(?:%[0-9a-fA-F]{2})+")


def _decode_url_run(match: re.Match[str]) -> str:
    """Decode one run of consecutive %XX as a UTF-8 byte sequence.

    %D1%96 is the UTF-8 encoding of Cyrillic і; per-byte Latin-1 decoding
    would yield "Ñ\\x96" and the homoglyph map would never see the codepoint.
    Invalid UTF-8 falls back to per-byte Latin-1.
    """
    pairs = re.findall(r"%([0-9a-fA-F]{2})", match.group(0))
    raw = bytes(int(p, 16) for p in pairs)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _decode_url_encoding(text: str) -> str:
    r"""Decode URL percent-encoding (%69 → i) — single pass.

    Consecutive %XX runs decode as UTF-8 (so %D1%96 → Cyrillic і, which the
    confusables table then normalizes). Double-encoding (%2569 → %69 → i) is
    expanded by the external fixpoint in _normalize_for_guard, not by an
    internal loop.
    """
    return _URL_RUN_ESCAPE.sub(_decode_url_run, text)


# ── Zero-width and invisible character stripping ─────────────────────────
# Rule instead of a manual codepoint list: strip every FORMAT character
# (category Cf — covers 200B–200F, 202A–202E, 2060–2064, 00AD, 061C, FEFF
# and anything Unicode adds later), every control character (category Cc,
# see _KEEP_CONTROLS), plus the variation-selector range FE00–FE0F (category
# Mn, invisible in most renderers), plus two exceptions: U+180E is Cf since
# Unicode 6.3 (Zs before) — covered by the rule on current runtimes, pinned
# explicitly against older unicodedata builds; U+2065 is unassigned (Cn) in
# every version — only reachable via the explicit entry. NBSP (U+00A0, Zs) is
# deliberately NOT listed: the whitespace collapse and NFKC (U+00A0 → space)
# handle it. Tab/newline/CR stay for the same reason — they are reader-visible
# whitespace and the .{0,20} gap is built for them.
_STRIP_CN_EXCEPTIONS = {"\u2065", "\u180e"}
_KEEP_CONTROLS = {"\t", "\n", "\r"}


def _is_invisible_char(ch: str) -> bool:
    """Whether one character is invisible enough to split regex keywords.

    ASCII is decided here (not by category): only the C0 controls and DEL are
    invisible, tab/newline/CR are kept and every printable ASCII character is
    visible — that keeps the per-char cost of a hot path at one comparison.
    """
    if ch < "\u0080":
        if ch in _KEEP_CONTROLS:
            return False
        return ch < " " or ch == "\x7f"
    category = unicodedata.category(ch)
    if category in ("Cf", "Cc"):
        return True
    if "\ufe00" <= ch <= "\ufe0f":
        return True
    return ch in _STRIP_CN_EXCEPTIONS


def _strip_invisible(text: str) -> str:
    """Remove invisible/zero-width characters by rule (see block comment)."""
    return "".join(ch for ch in text if not _is_invisible_char(ch))


# ── Whitespace normalization ─────────────────────────────────────────────
# Normalize all whitespace variants (tabs, newlines, non-breaking spaces,
# em spaces, etc.) to single spaces so the .{0,20} regex gap works uniformly.
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def _normalize_whitespace(text: str) -> str:
    """Collapse all Unicode whitespace to single ASCII spaces.

    This also handles the case where zero-width chars have already been stripped
    and adjacent words need a separator. Newlines, tabs, non-breaking spaces,
    em spaces, thin spaces, etc. all become regular spaces.
    """
    return _WHITESPACE_RE.sub(" ", text)


# ── HTML entity decoding ─────────────────────────────────────────────────
def _decode_html_entities(text: str) -> str:
    r"""Decode HTML entities (&#105; → i) — single pass.

    html.unescape is single-pass by nature: "&#105;" becomes "&#105;".
    Layered entity wrappers are expanded by the external fixpoint in
    _normalize_for_guard, mirroring the URL and escape decoders.
    """
    return html.unescape(text)


def _normalize_for_guard(text: str) -> str:
    r"""Full normalization pipeline for guard input/output.

    Bounded fixpoint over ALL stages — a stage can spawn syntax only an
    earlier decoder understands (escape → "%", entity → "%", fullwidth →
    "%"/"&"/"\\"), and stripping can reveal encoded syntax — so one pass
    per family in a fixed order is not enough. Each round:
    1. Decode URL percent-encoding (%69 → i)
    2. Decode HTML entities (&#105; → i)
    3. Decode escape sequences (\x69 → i, \u0069 → i, etc.)
    4. Strip invisible/zero-width/control characters
    5. NFKC normalization + confusables table (fullwidth, Greek, Cyrillic…)
    Repeat until stable (≤_NORMALIZE_MAX_ROUNDS); whitespace collapse runs
    last — it only collapses separator runs and cannot reveal new decode
    syntax.
    """
    result = text
    for _ in range(_NORMALIZE_MAX_ROUNDS):
        nxt = _decode_url_encoding(result)
        nxt = _decode_html_entities(nxt)
        nxt = _decode_escapes(nxt)
        nxt = _strip_invisible(nxt)
        nxt = _normalize_homoglyphs(nxt)
        if nxt == result:
            break
        result = nxt
    return _normalize_whitespace(result)


# ── Default blocking patterns (input) ────────────────────────────────────────
# Each pattern is a tuple (regex, reason_tag).

DEFAULT_BLOCK_PATTERNS: list[tuple[str, str]] = [
    # Direct system prompt override
    (
        r"(?i)(?:ignore|forget|disregard|override|overwrite).{0,20}"
        r"(?:all|previous|prior).{0,20}"
        r"(?:instruction|prompt|directive|command|rule|guideline)",
        "inject_ignore_instructions",
    ),
    (
        r"(?i)(?:you are not|you aren't).{0,30}(?:assistant|ai|helper|chatbot)",
        "inject_role_override",
    ),
    (
        r"(?i)(?:new|updated).{0,10}(?:instruction|prompt|directive|command|rule).{0,5}:?\s*$",
        "inject_new_instruction",
    ),
    (
        r"(?i)(?:pretend|imagine|act as|from now on|henceforth).{0,30}"
        r"(?:you are|you're|your role|your name)",
        "inject_pretend",
    ),
    (
        r"(?i)(?:system prompt|initial prompt|original prompt|your prompt|my instructions are)",
        "inject_system_prompt_leak",
    ),
    (
        r"(?i)^(?:say|repeat|print|output|display|show|type|write).{0,20}"
        r"(?:the |your |system |above|previous).{0,20}"
        r"(?:prompt|instruction|directive|message)",
        "inject_leak_request",
    ),
    (
        r"(?i)(?:reveal|expose|leak|dump|extract|give me).{0,30}"
        r"(?:prompt|instruction|directive|system)",
        "inject_reveal_request",
    ),
    # Executive override
    (
        r"(?i)(?:you must|you will).{0,20}(?:obey|follow|listen|comply)",
        "inject_executive",
    ),
    (
        r"(?i)(?:do not|don't).{0,20}(?:follow|obey|listen|heed)",
        "inject_disobey",
    ),
    # DAN / jailbreak
    (
        r"(?i)(?:DAN|jailbreak|jail.?break|dev.?mode|developer.?mode)",
        "inject_jailbreak",
    ),
    (
        r"\b(?:do|say).{0,10}(?:anything|whatever|everything).{0,20}"
        r"(?:want|ask|tell|command)",
        "inject_do_anything",
    ),
    # ── RAG-specific: instructions hidden in retrieved documents ──
    (
        r"(?i)(?:according to the document|the document says|as stated in the document)"
        r".{0,30}(?:you must|you will|your task|you are to|your new role)",
        "inject_rag_doc_override",
    ),
    (
        r"(?i)(?:disregard|overwrite|override|ignore).{0,30}"
        r"(?:these instructions|your guidelines|this prompt|the rules above)",
        "inject_rag_override",
    ),
    (
        r"(?i)(?:this is a test|for testing purposes only|this is a hypothetical)"
        r".{0,30}(?:ignore|forget|disregard|override)",
        "inject_rag_test_override",
    ),
]


# ── Default output leak patterns ─────────────────────────────────────────────

DEFAULT_OUTPUT_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?i)(?:my system prompt|my instructions are|i was told to|"
        r"i am programmed to|my core directive)",
        "leak_system_prompt",
    ),
    (
        r"(?i)(?:here (?:are|is|were|was) (?:my|the original|the full|the complete))"
        r".{0,30}(?:instruction|prompt|directive|guideline)",
        "leak_full_prompt",
    ),
    # API keys / tokens in output
    (
        r"(?i)(?:sk-[a-zA-Z][a-zA-Z0-9_\-]{2,}[a-zA-Z0-9]{16,}|"
        r"api.?key[\s\":=]+[a-zA-Z0-9_\-]{16,}|"
        r"secret[\s\":=]+[a-zA-Z0-9_\-]{16,})",
        "leak_credentials",
    ),
    (
        r"(?i)(?:Bearer\s+[a-zA-Z0-9_\-.:]{20,}|Authorization\s*:?\s*Bearer)",
        "leak_bearer_token",
    ),
    # ── Database connection strings with embedded credentials ──────────
    # Only URLs WITH credentials match ("db:5432/store" without @ passes).
    (
        r"(?i)(?:postgres|postgresql|mysql|mongodb|redis|amqp)://"
        r"[a-zA-Z0-9._-]+(?::[^@]+@)",
        "leak_db_connection_string",
    ),
]

# PII patterns for INTERMEDIATE data (raw tool results, tool arguments) only.
# Deliberately NOT in DEFAULT_OUTPUT_PATTERNS: the final answer guard shares
# check_output, and the LLM legitimately mentions dates, article numbers and
# contact emails in answers. A loose phone pattern there would block every
# answer carrying a date (15.09.2026) or article (1234567890) — false-positive
# regression verified before this split. Intermediate raw DB rows, on the other
# hand, must not be readable from the browser's devtools, so email/tight-phone
# blocking is cheap there (the transcript keeps raw content; only the SSE event
# is redacted, and the widget UI ignores tool_result payloads anyway).
#
# Phone formats covered (each ≥10 digits so 8-digit dates never match):
#   +15551234567, +7 999 123-45-67   — international with leading +
#   555-123-4567, 555 123 4567       — US 3-3-4
#   8 999 123-45-67, 7999123-45-67   — RU with leading 7/8
DEFAULT_INTERMEDIATE_PATTERNS: list[tuple[str, str]] = [
    *DEFAULT_OUTPUT_PATTERNS,
    (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "leak_pii_email",
    ),
    (
        # "+" + date-like shape (+15.09.2026, +5/9/26) is not a phone:
        # the lookahead excludes dd.mm.yyyy before the digit run matches.
        r"\+(?!\d{1,2}[./]\d{1,2}[./]\d{2,4})\d[\d\s\-().]{8,}\d"
        r"|\b\d{3}[\s\-.]\d{3}[\s\-.]\d{4}\b"
        r"|\b[78][\s\-().]?\d{3}[\s\-]?\d{3}[\s\-]\d{2}[\s\-]\d{2}\b",
        "leak_pii_phone",
    ),
]


@dataclass
class GuardResult:
    """Result of a guard check."""

    blocked: bool = False
    reason: str = ""
    pattern: str = ""


@dataclass
class GuardConfig:
    """Guard configuration."""

    enabled: bool = True
    # "block" | "warn". Honored by check_input; check_output/check_intermediate
    # always block on match — a warn-only output leak would stream to the
    # browser, so output-side blocking is unconditional by design.
    block_on_match: str = "block"
    input_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_BLOCK_PATTERNS)
    )
    output_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_OUTPUT_PATTERNS)
    )
    intermediate_patterns: list[tuple[str, str]] = field(
        default_factory=lambda: list(DEFAULT_INTERMEDIATE_PATTERNS)
    )
    blocked_count: int = 0

    @classmethod
    def from_env(cls) -> GuardConfig:
        """Load config from env vars."""
        config = cls(
            enabled=os.environ.get("GUARDRAIL_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            block_on_match=os.environ.get("GUARDRAIL_BLOCK_ON_MATCH", "block"),
        )
        # Override patterns from env var (JSON)
        override_raw = os.environ.get("GUARDRAIL_BLOCK_PATTERNS", "")
        if override_raw:
            try:
                overrides = json.loads(override_raw)
                if "input" in overrides:
                    config.input_patterns = [
                        (p["pattern"], p["reason"]) for p in overrides["input"]
                    ]
                if "output" in overrides:
                    config.output_patterns = [
                        (p["pattern"], p["reason"]) for p in overrides["output"]
                    ]
                if "intermediate" in overrides:
                    config.intermediate_patterns = [
                        (p["pattern"], p["reason"]) for p in overrides["intermediate"]
                    ]
                # NB: each key replaces its family wholesale, and "output" does
                # NOT propagate into intermediate_patterns — intermediate keeps
                # the compiled defaults (default output + PII), so an output
                # override cannot re-open the tool-result scan. Operators who
                # ADD output patterns must add them to "intermediate" too.
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to parse GUARDRAIL_BLOCK_PATTERNS: %s", e)
        return config


class GuardChecker:
    """Check messages against prompt injection patterns."""

    def __init__(self, config: GuardConfig | None = None):
        self.config = config or GuardConfig.from_env()
        self._input_compiled = [
            (re.compile(p), reason) for p, reason in self.config.input_patterns
        ]
        self._output_compiled = [
            (re.compile(p), reason) for p, reason in self.config.output_patterns
        ]
        self._intermediate_compiled = [
            (re.compile(p), reason) for p, reason in self.config.intermediate_patterns
        ]

    def reload(self) -> None:
        """Reload config from env."""
        self.config = GuardConfig.from_env()
        self._input_compiled = [
            (re.compile(p), reason) for p, reason in self.config.input_patterns
        ]
        self._output_compiled = [
            (re.compile(p), reason) for p, reason in self.config.output_patterns
        ]
        self._intermediate_compiled = [
            (re.compile(p), reason) for p, reason in self.config.intermediate_patterns
        ]

    def check_input(self, message: str) -> GuardResult:
        """Check user message for prompt injection."""
        if not self.config.enabled:
            return GuardResult()
        if not message:
            return GuardResult()
        # Decode escapes + normalize homoglyphs before regex search
        normalized = _normalize_for_guard(message)
        for compiled, reason in self._input_compiled:
            if compiled.search(normalized):
                self.config.blocked_count += 1
                logger.warning(
                    "[GUARD] Blocked input: %s (pattern: %s)",
                    reason,
                    compiled.pattern[:60],
                )
                if self.config.block_on_match == "block":
                    return GuardResult(
                        blocked=True,
                        reason=reason,
                        pattern=compiled.pattern,
                    )
                return GuardResult(
                    blocked=False,
                    reason=f"warn:{reason}",
                    pattern=compiled.pattern,
                )
        return GuardResult()

    def check_output(self, content: str) -> GuardResult:
        """Check LLM response for system prompt leak or credentials.

        Gates the FINAL answer only: real secrets (credentials, bearer tokens,
        credentialed DB URLs) block. PII email/phone patterns deliberately do
        NOT run here — legitimate answers carry dates, article numbers and
        contact emails; see DEFAULT_INTERMEDIATE_PATTERNS for the split.
        """
        if not self.config.enabled:
            return GuardResult()
        if not content:
            return GuardResult()
        # Decode escapes + normalize homoglyphs before regex search
        normalized = _normalize_for_guard(content)
        for compiled, reason in self._output_compiled:
            if compiled.search(normalized):
                self.config.blocked_count += 1
                logger.warning(
                    "[GUARD] Matched output: %s (pattern: %s)",
                    reason,
                    compiled.pattern[:60],
                )
                return GuardResult(
                    blocked=True,
                    reason=reason,
                    pattern=compiled.pattern,
                )
        return GuardResult()

    def check_intermediate(self, content: str) -> GuardResult:
        """Check intermediate data (raw tool results, tool arguments) for leaks.

        Extends the output scan with PII patterns (email, tight phone formats).
        Raw DB rows must not be readable from the browser's devtools via
        tool_result/tool_call SSE events; the transcript keeps raw content, so
        only the SSE event is redacted and answer quality is unaffected.
        """
        if not self.config.enabled:
            return GuardResult()
        if not content:
            return GuardResult()
        normalized = _normalize_for_guard(content)
        for compiled, reason in self._intermediate_compiled:
            if compiled.search(normalized):
                self.config.blocked_count += 1
                logger.warning(
                    "[GUARD] Matched intermediate: %s (pattern: %s)",
                    reason,
                    compiled.pattern[:60],
                )
                return GuardResult(
                    blocked=True,
                    reason=reason,
                    pattern=compiled.pattern,
                )
        return GuardResult()


# ── Singleton ────────────────────────────────────────────────────────────────

_guard_checker: GuardChecker | None = None


def get_guard_checker() -> GuardChecker:
    """Get or create singleton guard checker."""
    global _guard_checker
    if _guard_checker is None:
        _guard_checker = GuardChecker()
    return _guard_checker
