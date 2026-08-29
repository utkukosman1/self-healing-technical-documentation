"""Extract changed code files and hunks from git diff."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
_DIFF_HEADER_RE = re.compile(r'^diff --git (\S+|"[^"]+") (\S+|"[^"]+")$')

_C_ESCAPE_MAP = {
    "b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r",
    '"': '"', "\\": "\\",
}


@dataclass
class FileDiff:
    """One file's portion of a unified diff."""

    path: str
    status: str  # added | modified | deleted | renamed
    hunks: list[str] = field(default_factory=list)


def get_changed_files(base_ref: str, head_ref: str = "HEAD") -> list[str]:
    """List files changed between two refs via git."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def get_diff_text(base_ref: str, head_ref: str = "HEAD") -> str:
    """Raw unified diff between two refs via git."""
    result = subprocess.run(
        ["git", "diff", f"{base_ref}...{head_ref}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _strip_prefix(path: str) -> str:
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _unquote(path: str) -> str:
    """Decode git's C-style quoted paths, e.g. `"a/my file.py"`.

    Octal escapes are UTF-8 bytes, so the whole inner string is decoded as
    escaped bytes and then interpreted as UTF-8.
    """
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    inner = path[1:-1]
    out = bytearray()
    i = 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner):
            nxt = inner[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < len(inner) and j < i + 4 and inner[j] in "01234567":
                    j += 1
                out.append(int(inner[i + 1:j], 8))
                i = j
                continue
            out.extend(_C_ESCAPE_MAP.get(nxt, nxt).encode("utf-8"))
            i += 2
            continue
        out.extend(inner[i].encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified git diff into per-file sections with hunks."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    current_hunk: list[str] | None = None

    def flush_hunk() -> None:
        nonlocal current_hunk
        if current is not None and current_hunk is not None:
            current.hunks.append("\n".join(current_hunk))
        current_hunk = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush_hunk()
            if current is not None:
                files.append(current)
            header = _DIFF_HEADER_RE.match(line)
            if header:
                b_path = _strip_prefix(_unquote(header.group(2)))
                a_path = _strip_prefix(_unquote(header.group(1)))
                path = b_path or a_path
            else:
                path = ""
            current = FileDiff(path=path, status="modified")
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith("rename from"):
            current.status = "renamed"
        elif line.startswith("rename to "):
            current.path = _unquote(line[len("rename to "):])
        elif line.startswith("+++ ") and line != "+++ /dev/null":
            if current.status == "modified":
                current.path = _strip_prefix(_unquote(line[4:]))
        elif line == "--- /dev/null":
            current.status = "added"
        elif line.startswith("--- ") and line != "--- /dev/null":
            if current.status == "deleted":
                current.path = _strip_prefix(_unquote(line[4:]))
        elif _HUNK_HEADER_RE.match(line):
            flush_hunk()
            current_hunk = [line]
        elif current_hunk is not None:
            current_hunk.append(line)

    flush_hunk()
    if current is not None:
        files.append(current)
    return files
