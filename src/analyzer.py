"""LLM staleness verification and doc rewrite generation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI

from .prompts import SYSTEM_PROMPT, build_analysis_prompt

# Default chat model for verdicts and rewrites. If this changes, the
# documented default in README.md (Inputs table) must change with it.
DEFAULT_CHAT_MODEL = "gpt-4.1-mini"


@dataclass
class Verdict:
    """Structured outcome for one doc section."""

    status: str  # "STALE" | "OK"
    evidence: str
    suggested_rewrite: str


def _parse_verdict(raw: str) -> Verdict:
    """Decode the LLM's JSON response, degrading safely on malformed input."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Verdict(status="OK", evidence="", suggested_rewrite="")
    status = data.get("status", "OK")
    if status not in ("STALE", "OK"):
        return Verdict(status="OK", evidence="", suggested_rewrite="")
    return Verdict(
        status=status,
        evidence=str(data.get("evidence") or "").strip(),
        suggested_rewrite=str(data.get("suggested_rewrite") or "").strip(),
    )


def analyze(
    section: str,
    code: str,
    diff: str,
    client: OpenAI,
    chat_model: str = DEFAULT_CHAT_MODEL,
) -> Verdict:
    """Ask the LLM whether a doc section is stale given code + diff."""
    response = client.chat.completions.create(
        model=chat_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_analysis_prompt(section, code, diff)},
        ],
    )
    return _parse_verdict(response.choices[0].message.content or "")
