"""Prompt templates for DocSentry."""

SYSTEM_PROMPT = (
    "You are a documentation accuracy auditor for a software repository. "
    "You compare a section of documentation against the relevant current "
    "code and the changes made in a pull request. You decide whether the "
    "section has become STALE (a specific claim is now inaccurate or out of "
    "date because of the code change) or is still OK.\n"
    "Rules:\n"
    "- Respond with a JSON object only, no other text.\n"
    '- JSON keys: "status", "evidence", "suggested_rewrite".\n'
    '- "status" is "STALE" only when the code change contradicts or '
    'invalidates a specific claim in the section; otherwise "OK".\n'
    '- "evidence" cites the exact code (function names, behavior, or diff '
    'lines) proving the section is stale. Empty string when OK.\n'
    '- "suggested_rewrite" is the complete replacement markdown for the '
    "section, including its heading, when STALE; empty string when OK. "
    "Keep the original heading level, wording, and style; only change what "
    "the code change makes wrong."
)


def build_analysis_prompt(section: str, code: str, diff: str) -> str:
    """Compose the per-section user prompt from doc, code, and diff."""
    return (
        "Documentation section:\n"
        f"---\n{section}\n---\n\n"
        "Relevant current code:\n"
        f"---\n{code}\n---\n\n"
        "Pull request diff for this file:\n"
        f"---\n{diff}\n---\n\n"
        "Is this documentation section still accurate after the code "
        "change? Respond with JSON."
    )
