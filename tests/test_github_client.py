from types import SimpleNamespace

from src import github_client


class FakeSubprocess:
    def __init__(self, stdout: str = ""):
        self.stdout = stdout
        self.calls: list[dict] = []

    def __call__(self, args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        return SimpleNamespace(stdout=self.stdout, stderr="", returncode=0)


class TestResolvePrNumber:
    def test_from_github_ref(self, monkeypatch):
        fake = FakeSubprocess()
        monkeypatch.setattr(github_client.subprocess, "run", fake)
        assert github_client.resolve_pr_number("refs/pull/42/merge", None, "tok") == 42
        assert fake.calls == []

    def test_fallback_via_head_ref(self, monkeypatch):
        fake = FakeSubprocess(stdout="7\n")
        monkeypatch.setattr(github_client.subprocess, "run", fake)
        assert github_client.resolve_pr_number(None, "feature/x", "tok") == 7
        call = fake.calls[0]
        assert call["args"][0] == "gh"
        assert call["args"][1] == "pr"
        assert "--head" in call["args"] and "feature/x" in call["args"]
        assert call["env"]["GH_TOKEN"] == "tok"

    def test_none_when_no_context(self, monkeypatch):
        fake = FakeSubprocess()
        monkeypatch.setattr(github_client.subprocess, "run", fake)
        assert github_client.resolve_pr_number(None, None, "tok") is None
        assert fake.calls == []

    def test_non_numeric_output_is_none(self, monkeypatch):
        fake = FakeSubprocess(stdout="")
        monkeypatch.setattr(github_client.subprocess, "run", fake)
        assert github_client.resolve_pr_number(None, "feature/x", "tok") is None


class TestPostPrComment:
    def test_posts_via_gh_with_stdin_body(self, monkeypatch):
        fake = FakeSubprocess()
        monkeypatch.setattr(github_client.subprocess, "run", fake)
        body = "## report\nstale stuff"
        github_client.post_pr_comment(42, body, "tok")
        call = fake.calls[0]
        assert call["args"] == ["gh", "pr", "comment", "42", "--body-file", "-"]
        assert call["input"] == body
        assert call["env"]["GH_TOKEN"] == "tok"
        assert call["check"] is True


class TestCreateFixPr:
    def _run(self, monkeypatch, reviewer="alice"):
        fake = FakeSubprocess()
        monkeypatch.setattr(github_client.subprocess, "run", fake)
        monkeypatch.setenv("GITHUB_REPOSITORY", "utku/docsentry")
        github_client.create_fix_pr(
            "docsentry/fix-x",
            ["README.md"],
            "docs: fix",
            "body",
            "tok",
            reviewer=reviewer,
            workspace="/ws",
        )
        return fake

    def test_git_sequence(self, monkeypatch):
        fake = self._run(monkeypatch)
        assert fake.calls[0]["args"] == ["git", "checkout", "-b", "docsentry/fix-x"]
        assert fake.calls[0]["cwd"] == "/ws"
        assert fake.calls[1]["args"] == ["git", "add", "README.md"]
        commit_args = fake.calls[2]["args"]
        assert commit_args[:2] == ["git", "-c"]
        assert "user.name=github-actions[bot]" in commit_args
        assert "commit" in commit_args

    def test_push_uses_token_url(self, monkeypatch):
        fake = self._run(monkeypatch)
        push = fake.calls[3]
        assert push["args"][0:2] == ["git", "push"]
        assert push["args"][2].startswith("https://x-access-token:tok@github.com/")
        assert push["args"][2].endswith("utku/docsentry.git")
        assert push["args"][3] == "docsentry/fix-x"

    def test_pr_created_with_reviewer(self, monkeypatch):
        fake = self._run(monkeypatch, reviewer="alice")
        pr = fake.calls[4]
        assert pr["args"][:2] == ["gh", "pr"]
        assert "--head" in pr["args"] and "docsentry/fix-x" in pr["args"]
        assert "--reviewer" in pr["args"] and "alice" in pr["args"]
        assert pr["env"]["GH_TOKEN"] == "tok"

    def test_no_reviewer_flag_when_empty(self, monkeypatch):
        fake = self._run(monkeypatch, reviewer="")
        pr = fake.calls[4]
        assert "--reviewer" not in pr["args"]
