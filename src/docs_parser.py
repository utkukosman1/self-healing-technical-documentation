"""Split Markdown documentation into heading-level section chunks."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class DocSection:
    """One markdown section: a heading plus everything under it."""

    title: str
    level: int
    content: str


def chunk_markdown(text: str) -> list[DocSection]:
    """Split markdown into sections at every heading line.

    Content before the first heading becomes a section with an empty title
    (level 0). Heading-like lines inside fenced code blocks are ignored.
    """
    sections: list[DocSection] = []
    title, level = "", 0
    body: list[str] = []
    in_fence = False

    def flush() -> str:
        nonlocal body
        content = "\n".join(body).strip()
        body = []
        return content

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading and not in_fence:
            content = flush()
            if title or content:
                sections.append(DocSection(title=title, level=level, content=content))
            title, level = heading.group(2).strip(), len(heading.group(1))
            continue
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        body.append(line)

    content = flush()
    if title or content:
        sections.append(DocSection(title=title, level=level, content=content))
    return sections


def matches_globs(path: str, glob_patterns: str) -> bool:
    """True if path matches any of the comma-separated glob patterns."""
    patterns = [p.strip() for p in glob_patterns.split(",") if p.strip()]
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def _heading_positions(text: str) -> list[tuple[int, int, str]]:
    """Line index, level, and title of each heading outside code fences."""
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    for i, line in enumerate(text.splitlines()):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            headings.append((i, len(match.group(1)), match.group(2).strip()))
    return headings


def replace_section(file_text: str, title: str, level: int, new_text: str) -> str | None:
    """Replace one raw section (heading at `level` titled `title`) with new_text.

    Returns the updated file text, or None if the section cannot be located
    unambiguously, so a failed match never corrupts the file.
    """
    lines = file_text.splitlines()
    headings = _heading_positions(file_text)

    if level == 0:
        start, end = 0, headings[0][0] if headings else len(lines)
    else:
        starts = [i for i, lv, t in headings if (lv, t) == (level, title.strip())]
        if len(starts) != 1:
            return None
        start = starts[0]
        end = next((i for i, _lv, _t in headings if i > start), len(lines))

    updated = "\n".join(lines[:start] + new_text.splitlines() + lines[end:])
    if file_text.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    return updated
