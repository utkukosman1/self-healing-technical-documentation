from pathlib import Path

import yaml

from src.main import load_config

ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    # BaseLoader preserves GitHub's YAML 1.2 'on' key as text.
    return yaml.load((ROOT / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_fork_workflow_only_executes_trusted_action_code():
    workflow = read_yaml(".github/workflows/review.yml")
    assert set(workflow["on"]) == {"pull_request_target"}
    assert set(workflow["on"]["pull_request_target"]["types"]) == {
        "opened", "reopened", "synchronize", "edited", "ready_for_review",
    }
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "github.event.pull_request.number" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "true"
    job = workflow["jobs"]["review"]
    assert job["if"] == "github.event.pull_request.draft == false"
    checkout, action = job["steps"]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] == "false"
    assert action["uses"] == "./"
    assert action["with"]["mode"] == "review"
    assert action["with"]["jev-provider"] == "openrouter"
    assert action["with"]["openrouter-api-key"] == "${{ secrets.OPENROUTER_API_KEY }}"
    assert "openai-api-key" not in action["with"]
    assert not any("run" in step for step in job["steps"])


def test_review_action_skips_checkout_and_uses_trusted_module_path():
    action = read_yaml("action.yml")
    steps = action["runs"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["if"] == "inputs.mode != 'review'"
    run = next(step for step in steps if step.get("id") == "run")
    assert run["run"] == "python -P -m src.main"
    assert run["env"]["PYTHONPATH"] == "${{ github.action_path }}"
    assert run["env"]["DOCSENTRY_TYPESAFE_API_KEY"] == "${{ inputs.typesafe-api-key }}"
    assert run["env"]["DOCSENTRY_OPENROUTER_API_KEY"] == "${{ inputs.openrouter-api-key }}"
    assert run["env"]["DOCSENTRY_JEV_PROVIDER"] == "${{ inputs.jev-provider }}"
    assert run["env"]["DOCSENTRY_JEV_MODEL"] == "${{ inputs.jev-model }}"
    assert action["inputs"]["openai-api-key"]["required"] == "false"
    defaults = load_config({"DOCSENTRY_JEV_MODEL": action["inputs"]["jev-model"]["default"],
                            "DOCSENTRY_JEV_PROVIDER": action["inputs"]["jev-provider"]["default"]})
    assert defaults["jev_model"] == load_config({})["jev_model"]
    assert {"review-verdict", "review-head-sha", "result"} <= set(action["outputs"])
