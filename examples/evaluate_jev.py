"""Validate fixtures; --live opts into paid Jev calls via the selected provider, never GitHub writes."""

import argparse
import difflib
import json
from pathlib import Path

from src.jev import Assessment, PROVIDERS, RUBRICS, assess, assess_openrouter
from src.main import load_config
from src.review import build_context


def case_context(case):
    files, patches = [], []
    for file in case["files"]:
        path = file["path"]
        lines = list(difflib.unified_diff(
            file["before"].splitlines(keepends=True), file["after"].splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
        files.append({
            "path": path,
            "additions": sum(line.startswith("+") for line in lines[2:]),
            "deletions": sum(line.startswith("-") for line in lines[2:]),
        })
        patches.append(f"diff --git a/{path} b/{path}\n" + "".join(lines))
    return build_context({
        "title": case["title"], "body": case["description"], "files": files,
        "changedFiles": len(files), "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }, "".join(patches))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send fixtures to the configured Jev provider (requires its API key)")
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("jev_review_cases.json").read_text(encoding="utf-8"))
    contexts = [case_context(case) for case in cases]
    if not all(context.complete for context in contexts):
        raise ValueError("Evaluation fixtures must contain complete patches")
    if not args.live:
        print(f"Validated {len(cases)} fixtures offline. Use --live to evaluate Jev.")
        return 0
    config = load_config()
    provider = config["jev_provider"]
    if provider not in PROVIDERS:
        parser.error("DOCSENTRY_JEV_PROVIDER must be typesafe or openrouter")
    provider_name, key_name = PROVIDERS[provider]
    if not config[key_name]:
        parser.error(f"Set DOCSENTRY_{key_name.upper()} before using --live")
    assess_pr = assess_openrouter if provider == "openrouter" else assess
    mismatches = evaluated = false_positives = unknowns = 0
    for case, context in zip(cases, contexts):
        try:
            actual = assess_pr(context.state, config[key_name], config["jev_model"])
        except Exception:
            print(f"{case['id']}: {provider_name} unavailable; evaluation incomplete")
            return 1
        if not case["description"].strip():
            actual["scope"] = Assessment("unknown", None)
        unknowns += sum(answer.choice == "unknown" for answer in actual.values())
        for key, expected in case["expected"].items():
            evaluated += 1
            observed = actual[key].choice
            if observed != expected:
                mismatches += 1
                false_positives += observed == "attention" and expected in ("clear", "not_applicable")
                print(f"{case['id']}/{key}: expected={expected}, actual={observed}")
    print(f"provider={provider} model={config['jev_model']} mismatches={mismatches}/{evaluated} "
          f"false_attention={false_positives} unknown={unknowns}/{len(cases) * len(RUBRICS)}")
    return int(mismatches > 0)


if __name__ == "__main__":
    raise SystemExit(main())
