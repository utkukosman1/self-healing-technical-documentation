import subprocess
from types import SimpleNamespace

import numpy as np

from src.embeddings import CodeChunk
from src.main import (
    _as_bool,
    _as_int,
    _select_candidates,
    build_check_comment,
    build_fix_pr_body,
    load_config,
    resolve_refs,
    run,
)


class TestConfig:
    def test_defaults(self):
        config = load_config({})
        assert config["mode"] == "check"
        assert config["docs_glob"] == "README.md,docs/**/*.md"
        assert config["chat_model"] == "gpt-4.1-mini"
        assert config["fail_on_stale"] == "false"

    def test_overrides(self):
        config = load_config({"DOCSENTRY_MODE": "fix", "DOCSENTRY_MAX_SECTIONS": "5"})
        assert config["mode"] == "fix"
        assert config["max_sections"] == "5"


class TestHelpers:
    def test_resolve_refs_pr_event(self):
        assert resolve_refs({"GITHUB_BASE_REF": "main"}) == ("origin/main", "HEAD")

    def test_resolve_refs_no_pr(self):
        assert resolve_refs({}) == ("HEAD~1", "HEAD")

    def test_as_int(self):
        assert _as_int("5", 20) == 5
        assert _as_int("nope", 20) == 20

    def test_as_bool(self):
        assert _as_bool("true")
        assert _as_bool("1")
        assert not _as_bool("false")
        assert not _as_bool("")

    def test_select_candidates_keeps_sections_linked_to_changed(self):
        chunks = [
            CodeChunk("a.py", 1, "x"),
            CodeChunk("b.py", 1, "y"),
            CodeChunk("c.py", 1, "z"),
        ]
        ranked = [[(2, 0.9), (0, 0.8)], [(0, 0.5), (1, 0.4)]]
        candidates = _select_candidates(ranked, chunks, {"b.py"})
        assert candidates == [(1, 1)]

    def test_select_candidates_no_match(self):
        chunks = [CodeChunk("a.py", 1, "x")]
        assert _select_candidates([[(0, 1.0)]], chunks, {"b.py"}) == []


class TestBuildCheckComment:
    def test_full_report(self):
        stale = [{
            "path": "README.md",
            "title": "Login timeout",
            "evidence": "login() now takes timeout_seconds.",
            "suggested_rewrite": "## Login timeout\n\nFixed.",
        }]
        comment = build_check_comment(3, stale)
        assert "Analyzed 3 doc section(s)" in comment
        assert "found 1 stale" in comment
        assert 'README.md — "Login timeout"' in comment
        assert "**Evidence:** login() now takes timeout_seconds." in comment
        assert "## Login timeout" in comment

    def test_empty_stale(self):
        comment = build_check_comment(2, [])
        assert "found 0 stale" in comment
        assert "### " not in comment


class OnesEmbedder:
    def embed(self, texts):
        return np.ones((len(texts), 1), dtype=np.float32)


class StaleChatCompletions:
    def create(self, model, response_format, messages):
        content = (
            '{"status": "STALE", '
            '"evidence": "login() now takes timeout_seconds.", '
            '"suggested_rewrite": "## Login timeout\\n\\nLogin takes `timeout_seconds`."}'
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeChatClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=StaleChatCompletions())


def _make_repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "README.md").write_text(
        "# Login timeout\n\nLogin requests use `timeout_ms`.\n", encoding="utf-8"
    )
    (tmp_path / "a.py").write_text(
        "def login(timeout_ms=30000):\n    pass\n", encoding="utf-8"
    )
    git("add", ".")
    git("commit", "-m", "base")
    (tmp_path / "a.py").write_text(
        "def login(timeout_seconds=5):\n    pass\n", encoding="utf-8"
    )
    git("add", ".")
    git("commit", "-m", "change login signature")
    monkeypatch.chdir(tmp_path)


class TestRunEndToEnd:
    def _config(self, **overrides):
        config = load_config({})
        config["openai_api_key"] = "fake"
        config.update(overrides)
        return config

    def test_detects_stale_section_offline(self, tmp_path, monkeypatch, capsys):
        _make_repo(tmp_path, monkeypatch)
        code = run(
            self._config(),
            {},
            make_embed_client=lambda: OnesEmbedder(),
            make_chat_client=lambda: FakeChatClient(),
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "STALE  README.md :: Login timeout" in out
        assert "found 1 stale" in out
        assert "Suggested rewrite" in out

    def test_fail_on_stale_exits_1(self, tmp_path, monkeypatch):
        _make_repo(tmp_path, monkeypatch)
        code = run(
            self._config(fail_on_stale="true"),
            {},
            make_embed_client=lambda: OnesEmbedder(),
            make_chat_client=lambda: FakeChatClient(),
        )
        assert code == 1

    def test_only_docs_changed_exits_early(self, tmp_path, monkeypatch, capsys):
        _make_repo(tmp_path, monkeypatch)
        (tmp_path / "README.md").write_text(
            "# Login timeout\n\nUpdated wording.\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "."], cwd=tmp_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "docs only"], cwd=tmp_path, capture_output=True, check=True
        )
        code = run(self._config(), {}, make_embed_client=OnesEmbedder)
        assert code == 0
        assert "only documentation changed" in capsys.readouterr().out


class TestFixModeEndToEnd:
    def _config(self):
        config = load_config({})
        config["openai_api_key"] = "fake"
        config["mode"] = "fix"
        config["reviewer"] = "utkukosman1"
        return config

    def test_applies_rewrite_and_opens_pr(self, tmp_path, monkeypatch):
        _make_repo(tmp_path, monkeypatch)
        calls = []

        def fake_open_pr(branch, files, title, body, token, reviewer="", workspace="."):
            calls.append({
                "branch": branch,
                "files": files,
                "title": title,
                "body": body,
                "token": token,
                "reviewer": reviewer,
            })

        code = run(
            self._config(),
            {},
            make_embed_client=lambda: OnesEmbedder(),
            make_chat_client=lambda: FakeChatClient(),
            open_pr=fake_open_pr,
        )
        assert code == 0
        updated = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "timeout_seconds" in updated
        assert "timeout_ms" not in updated
        assert len(calls) == 1
        assert calls[0]["files"] == ["README.md"]
        assert calls[0]["branch"].startswith("docsentry/fix-stale-docs-")
        assert calls[0]["reviewer"] == "utkukosman1"
        assert "README.md" in calls[0]["body"]

    def test_rewrite_location_failure_flags_and_skips(self, tmp_path, monkeypatch, capsys):
        _make_repo(tmp_path, monkeypatch)
        (tmp_path / "README.md").write_text(
            "## Login timeout\n\nFirst mention.\n\n## Login timeout\n\nSecond mention.\n",
            encoding="utf-8",
        )
        code = run(
            self._config(),
            {},
            make_embed_client=lambda: OnesEmbedder(),
            make_chat_client=lambda: FakeChatClient(),
            open_pr=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not open PR")),
        )
        assert code == 0
        assert "skipping rewrite" in capsys.readouterr().out
        assert "First mention" in (tmp_path / "README.md").read_text(encoding="utf-8")


class TestBuildFixPrBody:
    def test_lists_fixed_sections(self):
        from src.analyzer import Verdict
        from src.docs_parser import DocSection
        from src.main import StaleFinding

        finding = StaleFinding(
            path="README.md",
            section=DocSection(title="Login timeout", level=1, content="old"),
            verdict=Verdict(status="STALE", evidence="e", suggested_rewrite="r"),
        )
        body = build_fix_pr_body([finding])
        assert "`README.md` — \"Login timeout\"" in body
