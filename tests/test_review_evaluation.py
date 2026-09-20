import json
import sys
from pathlib import Path

from examples import evaluate_jev
from src.jev import CHOICES, RUBRICS


def test_hand_labeled_cases_are_complete_and_cover_the_rubric():
    path = Path(evaluate_jev.__file__).with_name("jev_review_cases.json")
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len({case["id"] for case in cases}) == len(cases)
    covered = set()
    for case in cases:
        assert evaluate_jev.case_context(case).complete
        assert set(case["expected"]) <= set(RUBRICS)
        assert set(case["expected"].values()) <= CHOICES
        covered.update(case["expected"])
    assert covered == set(RUBRICS)


def test_default_evaluation_is_offline(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evaluate_jev"])
    def forbidden(*args):
        raise AssertionError("provider must not be called without --live")
    monkeypatch.setattr(evaluate_jev, "assess", forbidden)
    assert evaluate_jev.main() == 0
    assert "Validated 8 fixtures offline" in capsys.readouterr().out
