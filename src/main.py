"""DocSentry — self-healing technical documentation.

A GitHub Action that detects when code changes make documentation inaccurate
and either flags the stale sections or opens a PR with corrected docs.
"""

import os
import sys

from . import __version__


def load_config() -> dict:
    env = os.environ
    return {
        "openai_api_key": env.get("DOCSENTRY_OPENAI_API_KEY", ""),
        "github_token": env.get("DOCSENTRY_GITHUB_TOKEN", ""),
        "mode": env.get("DOCSENTRY_MODE", "check"),
        "docs_glob": env.get("DOCSENTRY_DOCS_GLOB", "README.md,docs/**/*.md"),
        "chat_model": env.get("DOCSENTRY_CHAT_MODEL", "gpt-4o-mini"),
        "embedding_model": env.get("DOCSENTRY_EMBEDDING_MODEL", "text-embedding-3-small"),
        "max_sections": env.get("DOCSENTRY_MAX_SECTIONS", "20"),
        "fail_on_stale": env.get("DOCSENTRY_FAIL_ON_STALE", "false"),
        "reviewer": env.get("DOCSENTRY_REVIEWER", ""),
    }


def main() -> int:
    config = load_config()
    print(f"DocSentry v{__version__}")
    print(f"  mode:            {config['mode']}")
    print(f"  docs-glob:       {config['docs_glob']}")
    print(f"  chat-model:      {config['chat_model']}")
    print(f"  embedding-model: {config['embedding_model']}")
    print(f"  max-sections:    {config['max_sections']}")
    if not config["openai_api_key"]:
        print("  warning: OPENAI_API_KEY not set (analysis disabled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())