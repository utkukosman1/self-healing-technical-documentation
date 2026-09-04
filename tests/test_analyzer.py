import json
from types import SimpleNamespace

from src.analyzer import Verdict, analyze
from src.prompts import SYSTEM_PROMPT, build_analysis_prompt


class FakeCompletionsEndpoint:
    """Offline stand-in for client.chat.completions."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, model, response_format, messages):
        self.calls.append(
            {"model": model, "response_format": response_format, "messages": messages}
        )
        content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeOpenAI:
    def __init__(self, responses: list[str]):
        self.chat = SimpleNamespace(completions=FakeCompletionsEndpoint(responses))


def _stale_json():
    return json.dumps(
        {
            "status": "STALE",
            "evidence": "login() now takes timeout_seconds, not timeout_ms.",
            "suggested_rewrite": "## Login\nNow uses timeout_seconds.",
        }
    )


def _ok_json():
    return json.dumps(
        {"status": "OK", "evidence": "", "suggested_rewrite": ""}
    )


class TestAnalyze:
    def test_stale_verdict_parsed(self):
        client = FakeOpenAI([_stale_json()])
        verdict = analyze("## Login\n...", "code", "diff", client)
        assert verdict == Verdict(
            status="STALE",
            evidence="login() now takes timeout_seconds, not timeout_ms.",
            suggested_rewrite="## Login\nNow uses timeout_seconds.",
        )

    def test_ok_verdict_parsed(self):
        client = FakeOpenAI([_ok_json()])
        verdict = analyze("## Login\n...", "code", "diff", client)
        assert verdict == Verdict(status="OK", evidence="", suggested_rewrite="")

    def test_request_uses_json_mode_and_model(self):
        client = FakeOpenAI([_ok_json()])
        analyze("section", "code", "diff", client, chat_model="gpt-4o")
        call = client.chat.completions.calls[0]
        assert call["model"] == "gpt-4o"
        assert call["response_format"] == {"type": "json_object"}

    def test_prompt_contains_all_inputs(self):
        client = FakeOpenAI([_ok_json()])
        analyze("## Auth docs", "def login():", "-def old()", client)
        user_message = client.chat.completions.calls[0]["messages"][1]["content"]
        assert "## Auth docs" in user_message
        assert "def login():" in user_message
        assert "-def old()" in user_message

    def test_malformed_json_degrades_to_ok(self):
        client = FakeOpenAI(["not json at all"])
        assert analyze("s", "c", "d", client) == Verdict("OK", "", "")

    def test_empty_response_degrades_to_ok(self):
        client = FakeOpenAI([""])
        assert analyze("s", "c", "d", client) == Verdict("OK", "", "")

    def test_unknown_status_degrades_to_ok(self):
        client = FakeOpenAI(['{"status": "MAYBE", "evidence": "x", "suggested_rewrite": "y"}'])
        assert analyze("s", "c", "d", client) == Verdict("OK", "", "")

    def test_null_fields_become_empty_strings(self):
        client = FakeOpenAI(['{"status": "STALE", "evidence": null, "suggested_rewrite": null}'])
        assert analyze("s", "c", "d", client) == Verdict("STALE", "", "")


class TestBuildAnalysisPrompt:
    def test_contains_section_code_diff(self):
        prompt = build_analysis_prompt("## S\nbody", "def f()", "@@ -1 +1 @@")
        assert "## S" in prompt
        assert "def f()" in prompt
        assert "@@ -1 +1 @@" in prompt

    def test_system_prompt_covers_cosmetic_diffs(self):
        assert "cosmetic" in SYSTEM_PROMPT
        assert '"status", "evidence", "suggested_rewrite"' in SYSTEM_PROMPT
        assert "verbatim" in SYSTEM_PROMPT
        assert "never delete content to avoid updating it" in SYSTEM_PROMPT
