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
