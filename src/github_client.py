"""GitHub interactions via the gh CLI."""

from __future__ import annotations

import os
import re
import subprocess
import sys

_PR_REF_RE = re.compile(r"refs/pull/(\d+)")


def _run_gh(args: list[str], token: str, stdin: str | None = None) -> str:
    env = {**os.environ, "GH_TOKEN": token}
    try:
        result = subprocess.run(
            ["gh", *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"gh command failed: {exc.stderr.strip()}", file=sys.stderr)
        raise
    return result.stdout


def resolve_pr_number(
    ref: str | None,
    head_ref: str | None,
    token: str,
) -> int | None:
    """PR number from GITHUB_REF, else by searching for the head branch."""
    if ref:
        match = _PR_REF_RE.match(ref)
        if match:
            return int(match.group(1))
    if head_ref:
        out = _run_gh(
            ["pr", "list", "--head", head_ref, "--state", "open",
             "--json", "number", "--jq", ".[0].number"],
            token,
        ).strip()
        if out.isdigit():
            return int(out)
    return None


def post_pr_comment(pr_number: int, body: str, token: str) -> None:
    """Add a markdown comment to a PR (stdin avoids shell-escaping issues)."""
    _run_gh(["pr", "comment", str(pr_number), "--body-file", "-"], token, stdin=body)


def _run_git(args: list[str], workspace: str) -> None:
    try:
        subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"git command failed: {exc.stderr.strip()}", file=sys.stderr)
        raise


def create_fix_pr(
    branch: str,
    files: list[str],
    title: str,
    body: str,
    token: str,
    reviewer: str = "",
    workspace: str = ".",
) -> None:
    """Commit doc fixes on a new branch, push, and open a PR via gh."""
    _run_git(["checkout", "-b", branch], workspace)
    _run_git(["add", *files], workspace)
    _run_git(
        ["-c", "user.name=github-actions[bot]",
         "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
         "commit", "-m", title],
        workspace,
    )
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    _run_git(["push", push_url, branch], workspace)
    args = ["pr", "create", "--title", title, "--body", body, "--head", branch]
    if reviewer:
        args += ["--reviewer", reviewer]
    _run_gh(args, token)
