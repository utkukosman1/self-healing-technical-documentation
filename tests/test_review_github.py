import json
import subprocess
from types import SimpleNamespace

import pytest

from src.github_client import REVIEW_MARKER, ReviewGitHub, ReviewGitHubError, same_pr_revision


def snapshot():
    return dict(state="OPEN", isDraft=False, baseRefOid="b" * 40, headRefOid="a" * 40,
                updatedAt="now", title="Title", body="Description")


def page(comments, more=False, cursor=None):
    return {"data": {"viewer": {"login": "github-actions[bot]"}, "repository": {
        "pullRequest": {"comments": {"nodes": comments, "pageInfo": {
            "hasNextPage": more, "endCursor": cursor,
        }}},
    }}}


def comment(identity, body, comment_id="ours"):
    return {"id": comment_id, "body": body, "author": {"login": identity}}


def fake_cli(monkeypatch, responses):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(stdout=json.dumps(response) if not isinstance(response, str) else response)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_marker_and_identity_are_both_required(monkeypatch):
    responses = [page([
        comment("contributor", REVIEW_MARKER + "\nspoof"),
        comment("github-actions[bot]", "## DocSentry documentation check"),
    ]), snapshot(), "created"]
    calls = fake_cli(monkeypatch, responses)
    assert ReviewGitHub("owner/repo", 42, "token").upsert_comment(REVIEW_MARKER + "\nreport", snapshot())
    assert calls[-1][0] == ["gh", "pr", "comment", "42", "--repo", "owner/repo", "--body-file", "-"]
    assert calls[-1][1]["input"] == REVIEW_MARKER + "\nreport"
    assert calls[-1][1]["env"]["GH_TOKEN"] == "token"
    assert calls[-1][1]["timeout"] == 60
    assert "shell" not in calls[-1][1]


def test_pagination_finds_our_comment_and_updates_by_id(monkeypatch):
    calls = fake_cli(monkeypatch, [
        page([comment("github-actions[bot]", "docs comment")], True, "cursor1"),
        page([comment("github-actions[bot]", REVIEW_MARKER + "\nold", "I_42")]),
        snapshot(), {"data": {"updateIssueComment": {"issueComment": {"id": "I_42"}}}},
    ])
    body = REVIEW_MARKER + "\nnew"
    assert ReviewGitHub("owner/repo", 42, "token").upsert_comment(body, snapshot())
    assert json.loads(calls[1][1]["input"])["variables"]["cursor"] == "cursor1"
    mutation = json.loads(calls[-1][1]["input"])
    assert mutation["variables"] == {"id": "I_42", "body": body}
    assert "updateIssueComment" in mutation["query"]
    assert not any(call[0][1:3] == ["pr", "comment"] for call in calls)


def test_repeated_runs_create_then_edit_one_comment(monkeypatch):
    calls = fake_cli(monkeypatch, [
        page([]), snapshot(), "created",
        page([comment("github-actions[bot]", REVIEW_MARKER + "\nold")]),
        snapshot(), {"data": {"updateIssueComment": {}}},
    ])
    client = ReviewGitHub("owner/repo", 42, "token")
    for body in ["first", "second"]:
        assert client.upsert_comment(REVIEW_MARKER + "\n" + body, snapshot())
    assert sum(args[1:3] == ["pr", "comment"] for args, _ in calls) == 1


@pytest.mark.parametrize("change", [
    {"headRefOid": "c" * 40}, {"baseRefOid": "c" * 40}, {"title": "new title"},
    {"body": "edited"}, {"updatedAt": "later"}, {"state": "CLOSED"}, {"isDraft": True},
])
def test_changed_pr_never_gets_stale_comment(monkeypatch, change):
    current = {**snapshot(), **change}
    calls = fake_cli(monkeypatch, [page([]), current])
    assert not same_pr_revision(snapshot(), current)
    assert not ReviewGitHub("owner/repo", 42, "token").upsert_comment("report", snapshot())
    assert len(calls) == 2


def test_graphql_errors_fail_without_creating_duplicate(monkeypatch):
    fake_cli(monkeypatch, [{"errors": [{"message": "sensitive"}]}])
    with pytest.raises(ReviewGitHubError, match="query failed"):
        ReviewGitHub("owner/repo", 42, "token").upsert_comment("report", snapshot())


def test_subprocess_errors_are_sanitized(monkeypatch, capsys):
    fake_cli(monkeypatch, [subprocess.CalledProcessError(1, "gh", stderr="secret")])
    with pytest.raises(ReviewGitHubError) as exc:
        ReviewGitHub("owner/repo", 42, "token").diff()
    assert "secret" not in str(exc.value) + capsys.readouterr().out + capsys.readouterr().err


def test_diff_always_names_the_repository(monkeypatch):
    calls = fake_cli(monkeypatch, ["diff"])
    assert ReviewGitHub("owner/repo", 42, "token").diff() == "diff"
    assert calls[0][0] == ["gh", "pr", "diff", "42", "--repo", "owner/repo", "--color", "never"]
