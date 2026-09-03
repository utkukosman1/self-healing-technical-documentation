"""DocSentry — self-healing technical documentation.

A GitHub Action that detects when code changes make documentation inaccurate
and either flags the stale sections or opens a PR with corrected docs.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass

from openai import OpenAI

from . import __version__
from .analyzer import Verdict, analyze
from .differ import get_changed_files, get_diff_text, parse_diff
from .docs_parser import DocSection, chunk_markdown, matches_globs, replace_section
from .embeddings import CodeChunk, EmbeddingClient, chunk_code, retrieve
from .github_client import create_fix_pr, post_pr_comment, resolve_pr_number

_TOP_K = 5
_MAX_CODE_CHUNKS = 1000
_MAX_FILE_BYTES = 200_000
_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}

_DEFAULTS = {
    "mode": "check",
    "docs_glob": "README.md,docs/**/*.md",
    "chat_model": "gpt-4o-mini",
    "embedding_model": "text-embedding-3-small",
    "max_sections": "20",
    "fail_on_stale": "false",
}


@dataclass
class StaleFinding:
    path: str
    section: DocSection
    verdict: Verdict


@dataclass
class AnalysisResult:
    message: str  # set when the run stopped early
    analyzed: int
    stale: list[StaleFinding]


def load_config(env: Mapping[str, str] | None = None) -> dict[str, str]:
    if env is None:
        env = os.environ
    return {
        "openai_api_key": env.get("DOCSENTRY_OPENAI_API_KEY", ""),
        "github_token": env.get("DOCSENTRY_GITHUB_TOKEN", ""),
        "mode": env.get("DOCSENTRY_MODE", _DEFAULTS["mode"]),
        "docs_glob": env.get("DOCSENTRY_DOCS_GLOB", _DEFAULTS["docs_glob"]),
        "chat_model": env.get("DOCSENTRY_CHAT_MODEL", _DEFAULTS["chat_model"]),
        "embedding_model": env.get("DOCSENTRY_EMBEDDING_MODEL", _DEFAULTS["embedding_model"]),
        "max_sections": env.get("DOCSENTRY_MAX_SECTIONS", _DEFAULTS["max_sections"]),
        "fail_on_stale": env.get("DOCSENTRY_FAIL_ON_STALE", _DEFAULTS["fail_on_stale"]),
        "reviewer": env.get("DOCSENTRY_REVIEWER", ""),
    }


def resolve_refs(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    if env is None:
        env = os.environ
    base = env.get("GITHUB_BASE_REF")
    if base:
        return f"origin/{base}", "HEAD"
    return "HEAD~1", "HEAD"


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


def _walk_files(workspace: str) -> list[str]:
    files: list[str] = []
    for root, dirs, names in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), workspace)
            files.append(rel.replace(os.sep, "/"))
    return files


def _read(workspace: str, path: str) -> str:
    with open(os.path.join(workspace, path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _select_candidates(
    ranked: list[list[tuple[int, float]]],
    code_chunks: list[CodeChunk],
    changed: set[str],
) -> list[tuple[int, int]]:
    """Keep doc sections whose top-k code chunks include a changed file."""
    candidates: list[tuple[int, int]] = []
    for doc_idx, ranks in enumerate(ranked):
        for chunk_idx, _score in ranks:
            if code_chunks[chunk_idx].path in changed:
                candidates.append((doc_idx, chunk_idx))
                break
    return candidates


def _finding_dict(finding: StaleFinding) -> dict:
    return {
        "path": finding.path,
        "title": finding.section.title or "(top of file)",
        "evidence": finding.verdict.evidence,
        "suggested_rewrite": finding.verdict.suggested_rewrite,
    }


def build_check_comment(analyzed: int, stale: list[dict]) -> str:
    lines = [
        "## DocSentry documentation check",
        "",
        f"Analyzed {analyzed} doc section(s) linked to changed code; "
        f"found {len(stale)} stale.",
    ]
    for item in stale:
        lines += ["", f"### {item['path']} — \"{item['title']}\"", ""]
        if item["evidence"]:
            lines += [f"**Evidence:** {item['evidence']}", ""]
        if item["suggested_rewrite"]:
            lines += [
                "<details><summary>Suggested rewrite</summary>",
                "",
                "```markdown",
                item["suggested_rewrite"],
                "```",
                "",
                "</details>",
            ]
    return "\n".join(lines)


def build_fix_pr_body(applied: list[StaleFinding]) -> str:
    lines = ["## DocSentry fixes", "", "Stale documentation sections updated:", ""]
    for finding in applied:
        lines.append(
            f"- `{finding.path}` — \"{finding.section.title or '(top of file)'}\""
        )
    return "\n".join(lines)


def _analyze(
    config: dict[str, str],
    env: Mapping[str, str],
    workspace: str,
    embed_client,
    chat_client,
) -> AnalysisResult:
    base_ref, head_ref = resolve_refs(env)
    changed = get_changed_files(base_ref, head_ref)
    docs_glob = config["docs_glob"]
    code_changed = [f for f in changed if not matches_globs(f, docs_glob)]
    if not changed:
        return AnalysisResult("no changed files, nothing to do", 0, [])
    if not code_changed:
        return AnalysisResult("only documentation changed, nothing to do", 0, [])

    sections: list[tuple[str, DocSection]] = []
    for path in _walk_files(workspace):
        if not matches_globs(path, docs_glob):
            continue
        for sec in chunk_markdown(_read(workspace, path)):
            if sec.title or sec.content:
                sections.append((path, sec))
    if not sections:
        return AnalysisResult("no documentation sections found", 0, [])

    chunks: list[CodeChunk] = []
    for path in _walk_files(workspace):
        if matches_globs(path, docs_glob):
            continue
        content = _read(workspace, path)
        if len(content.encode("utf-8", errors="ignore")) > _MAX_FILE_BYTES:
            continue
        chunks.extend(chunk_code(path, content))
    chunks = chunks[:_MAX_CODE_CHUNKS]
    if not chunks:
        return AnalysisResult("no code found to compare", 0, [])

    doc_texts = [(sec.title + "\n\n" + sec.content).strip() for _path, sec in sections]
    ranked = retrieve(doc_texts, [c.content for c in chunks], embed_client, top_k=_TOP_K)
    candidates = _select_candidates(ranked, chunks, set(changed))
    candidates = candidates[:_as_int(config["max_sections"], 20)]
    if not candidates:
        return AnalysisResult("no doc sections tied to changed files", 0, [])

    hunks_by_path = {
        fd.path: "\n".join(fd.hunks)
        for fd in parse_diff(get_diff_text(base_ref, head_ref))
        if fd.hunks
    }

    stale: list[StaleFinding] = []
    for doc_idx, chunk_idx in candidates:
        path, sec = sections[doc_idx]
        chunk = chunks[chunk_idx]
        code_context = f"File: {chunk.path} (line {chunk.start_line}+)\n\n{chunk.content}"
        verdict = analyze(
            doc_texts[doc_idx],
            code_context,
            hunks_by_path.get(chunk.path, ""),
            chat_client,
            config["chat_model"],
        )
        print(f"DocSentry: {verdict.status}  {path} :: {sec.title or '(top of file)'}")
        if verdict.status == "STALE":
            stale.append(StaleFinding(path=path, section=sec, verdict=verdict))
    return AnalysisResult("", len(candidates), stale)


def _apply_rewrites(workspace: str, findings: list[StaleFinding]) -> list[StaleFinding]:
    applied: list[StaleFinding] = []
    by_path: dict[str, list[StaleFinding]] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)
    for path, group in by_path.items():
        updated = _read(workspace, path)
        file_applied: list[StaleFinding] = []
        for finding in reversed(group):
            rewrite = finding.verdict.suggested_rewrite
            if not rewrite:
                print(f"DocSentry: STALE without rewrite in {path}; flagged only")
                continue
            result = replace_section(
                updated, finding.section.title, finding.section.level, rewrite
            )
            if result is None:
                print(
                    f"DocSentry: could not locate section "
                    f"\"{finding.section.title}\" in {path}; skipping rewrite"
                )
                continue
            updated = result
            file_applied.append(finding)
        if file_applied:
            with open(os.path.join(workspace, path), "w", encoding="utf-8") as fh:
                fh.write(updated)
            applied.extend(file_applied)
    return applied


def run(
    config: dict[str, str],
    env: Mapping[str, str],
    workspace: str = ".",
    make_embed_client=None,
    make_chat_client=None,
    open_pr=create_fix_pr,
) -> int:
    mode = config["mode"]
    if mode not in ("check", "fix"):
        print(f"DocSentry: unknown mode '{mode}'")
        return 0

    embed_client = (
        make_embed_client or
        (lambda: EmbeddingClient(config["openai_api_key"], config["embedding_model"]))
    )()
    chat_client = (
        make_chat_client or (lambda: OpenAI(api_key=config["openai_api_key"]))
    )()
    result = _analyze(config, env, workspace, embed_client, chat_client)
    if result.message:
        print(f"DocSentry: {result.message}")
        return 0
    if not result.stale:
        _write_output(
            env.get("GITHUB_OUTPUT"),
            f"analyzed={result.analyzed} ok={result.analyzed} stale=0",
        )
        print(f"DocSentry: all {result.analyzed} analyzed section(s) are up to date")
        return 0

    if mode == "check":
        comment = build_check_comment(
            result.analyzed, [_finding_dict(f) for f in result.stale]
        )
        print(comment)
        pr_number = resolve_pr_number(
            env.get("GITHUB_REF"), env.get("GITHUB_HEAD_REF"), config["github_token"],
        )
        if pr_number is not None:
            post_pr_comment(pr_number, comment, config["github_token"])
            print(f"DocSentry: posted comment on PR #{pr_number}")
        _write_output(
            env.get("GITHUB_OUTPUT"),
            f"analyzed={result.analyzed} "
            f"ok={result.analyzed - len(result.stale)} stale={len(result.stale)}",
        )
        if _as_bool(config["fail_on_stale"]):
            print(f"DocSentry: failing run ({len(result.stale)} stale sections)")
            return 1
        return 0

    applied = _apply_rewrites(workspace, result.stale)
    if not applied:
        print("DocSentry: no rewrites could be applied")
        return 0
    title = "docs: fix stale sections flagged by DocSentry"
    branch = f"docsentry/fix-stale-docs-{int(time.time())}"
    open_pr(
        branch,
        [f.path for f in applied],
        title,
        build_fix_pr_body(applied),
        config["github_token"],
        config["reviewer"],
        workspace,
    )
    print(f"DocSentry: opened docs fix PR from branch {branch}")
    return 0


def _write_output(path: str | None, value: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"result={value}\n")


def main() -> int:
    print(f"DocSentry v{__version__}")
    return run(load_config(), os.environ)


if __name__ == "__main__":
    sys.exit(main())
