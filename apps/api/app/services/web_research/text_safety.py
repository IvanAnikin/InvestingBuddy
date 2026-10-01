"""Prompt-rendering safety for untrusted web text — open-web W3 (threat model §3.3).

Two different things are kept apart on purpose:

* **Storage keeps the original.** A chunk, a page and the raw artifact hold exactly
  what the page carried — Unicode tag characters, bidi overrides and all — because an
  injection attempt is evidence, and an auditor must be able to see it.
* **The prompt gets the rendering.** :func:`render_for_prompt` removes the characters a
  human reader cannot see but a model reads: Unicode TAG characters (U+E0000–E007F,
  the "ASCII smuggling" block), bidirectional overrides/isolates (Trojan-Source), and
  zero-width characters. It also normalises the text to NFKC so full-width look-alikes
  of a fence marker become the marker the fence neutraliser recognises.

This is a presentation defence, not a boundary (§3.2 is the boundary): it lowers the
chance a model obeys hidden text; it cannot stop a model that decides to.

Hidden HTML (``display:none``, ``aria-hidden``, comments …) is removed before main
content extraction — see ``extract.py`` — so it never reaches a chunk, and the raw bytes
keep it.
"""

from __future__ import annotations

import re
import unicodedata

#: Unicode TAG block — invisible ASCII look-alikes used to smuggle instructions.
_TAG_CHARS = r"\U000E0000-\U000E007F"
#: Bidirectional embeddings/overrides/isolates (Trojan Source) and marks.
_BIDI_CHARS = "‪-‮⁦-⁩‎‏؜"
#: Zero-width and invisible formatting characters (BOM mid-text included).
_ZERO_WIDTH_CHARS = "​-‍⁠-⁤﻿᠎­"

_INVISIBLE_RE = re.compile(f"[{_TAG_CHARS}{_BIDI_CHARS}{_ZERO_WIDTH_CHARS}]")
_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")


def invisible_char_count(text: str | None) -> int:
    """How many invisible/smuggling characters ``text`` carries (a taint signal)."""
    return len(_INVISIBLE_RE.findall(text or ""))


def has_tag_characters(text: str | None) -> bool:
    return any(0xE0000 <= ord(ch) <= 0xE007F for ch in (text or ""))


def strip_invisible(text: str | None) -> str:
    """``text`` without tag, bidi-control or zero-width characters. Nothing else changes."""
    return _INVISIBLE_RE.sub("", text or "")


def render_for_prompt(text: str | None) -> str:
    """The form of untrusted web text that may be placed in a prompt.

    NFKC-normalised, invisible characters removed, runs of spaces collapsed. Visible
    words are never altered or dropped: a reader of the prompt sees what a reader of
    the page saw.
    """
    cleaned = strip_invisible(unicodedata.normalize("NFKC", text or ""))
    # NFKC can itself produce a character in the stripped set; strip again.
    cleaned = strip_invisible(cleaned)
    return _SPACE_RUN_RE.sub(" ", cleaned)


__all__ = [
    "has_tag_characters",
    "invisible_char_count",
    "render_for_prompt",
    "strip_invisible",
]
