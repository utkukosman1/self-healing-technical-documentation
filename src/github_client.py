"""GitHub interactions via the gh CLI."""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys

_PR_REF_RE = re.compile(r"refs/pull/(\d+)")

REVIEW_MARKER = "<!-- docsentry:jev-review:v1 -->"


class ReviewGitHubError(RuntimeError):
    """A review operation failed; messages never include provider output or PR content."""


def same_pr_revision(before: dict, after: dict) -> bool:
    return after["state"] == "OPEN" and not after["isDraft"] and all(
        before[key] == after[key]
        for key in ("baseRefOid", "headRefOid", "updatedAt", "title", "body")
    )


class ReviewGitHub:
    """Read PR data and maintain our comment without checking out contributor code."""

    def __init__(self, repo: str, number: int, token: str):
        self.repo, self.number, self.token = repo, number, token

    def _gh(self, args: list[str], stdin: str | None = None) -> str:
        try:
            result = subprocess.run(
                ["gh", *args], input=stdin, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=True, timeout=60,
                env={**os.environ, "GH_TOKEN": self.token, "GH_PROMPT_DISABLED": "1"},
            )
        except (OSError, subprocess.SubprocessError):
            raise ReviewGitHubError("GitHub review operation failed") from None
        return result.stdout

    def metadata(self) -> dict:
        return json.loads(self._gh([
            "pr", "view", str(self.number), "--repo", self.repo, "--json",
            "title,body,headRefOid,baseRefOid,updatedAt,state,isDraft,files,changedFiles,additions,deletions",
        ]))

    def diff(self) -> str:
        return self._gh(["pr", "diff", str(self.number), "--repo", self.repo, "--color", "never"])

    def _graphql(self, query: str, variables: dict) -> dict:
        result = json.loads(self._gh(
            ["api", "graphql", "--input", "-"],
            json.dumps({"query": query, "variables": variables}),
        ))
        if result.get("errors"):
            raise ReviewGitHubError("GitHub review query failed")
        return result["data"]

    def upsert_comment(self, body: str, expected: dict) -> bool:
        owner, name = self.repo.split("/")
        cursor = None
        comment_id = None
        while True:
            data = self._graphql(
                """query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
                  viewer { login }
                  repository(owner: $owner, name: $name) {
                    pullRequest(number: $number) {
                      comments(first: 100, after: $cursor) {
                        nodes { id body author { login } }
                        pageInfo { hasNextPage endCursor }
                      }
                    }
                  }
                }""",
                {"owner": owner, "name": name, "number": self.number, "cursor": cursor},
            )
            comments = data["repository"]["pullRequest"]["comments"]
            for comment in comments["nodes"]:
                if ((comment.get("author") or {}).get("login") == data["viewer"]["login"]
                        and comment["body"].startswith(REVIEW_MARKER + "\n")):
                    comment_id = comment["id"]
                    break
            if comment_id or not comments["pageInfo"]["hasNextPage"]:
                break
            next_cursor = comments["pageInfo"]["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise ReviewGitHubError("GitHub comment pagination failed")
            cursor = next_cursor

        # Lookup can take time on busy PRs. Recheck immediately before the write.
        if not same_pr_revision(expected, self.metadata()):
            return False
        if comment_id:
            self._graphql(
                """mutation($id: ID!, $body: String!) {
                  updateIssueComment(input: {id: $id, body: $body}) { issueComment { id } }
                }""", {"id": comment_id, "body": body},
            )
        else:
            self._gh([
                "pr", "comment", str(self.number), "--repo", self.repo, "--body-file", "-",
            ], body)
        return True


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
