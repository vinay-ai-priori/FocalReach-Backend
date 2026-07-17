"""
Post-processes trafilatura's output. favor_recall=True (needed so we don't
miss real content) also lets through nav/wrapper boilerplate and duplicate
blocks on some sites' markup. This strips known boilerplate lines,
collapses whitespace, and drops duplicate paragraphs without touching any
unique sentence.
"""

from __future__ import annotations

import re

_BOILERPLATE_LINE_RE = re.compile(
    r"^\s*(skip to content|skip to main content|cookie(s)? (settings|policy|consent)|"
    r"subscribe to (our )?newsletter|accept all cookies|back to top)\s*$",
    re.IGNORECASE,
)

_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    if not text:
        return text

    lines = text.split("\n")
    cleaned_lines: list[str] = []
    seen: set[str] = set()

    for raw_line in lines:
        line = _WHITESPACE_RUN_RE.sub(" ", raw_line).strip()

        if not line:
            cleaned_lines.append("")
            continue
        if _BOILERPLATE_LINE_RE.match(line):
            continue

        # Drop exact-duplicate lines (case-sensitive) once already seen,
        # but keep very short lines (numbers, single words like "Read more")
        # since those can legitimately repeat (stats, prices, labels)
        # without being boilerplate/duplication.
        if len(line) > 10:
            if line in seen:
                continue
            seen.add(line)

        cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)
    result = _BLANK_LINES_RE.sub("\n\n", result)
    return result.strip()
