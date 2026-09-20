"""Bounded, advisory PR triage. Contributor content is data, never executable code."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .differ import parse_diff
from .github_client import REVIEW_MARKER, ReviewGitHub, ReviewGitHubError, same_pr_revision
from .jev import Assessment, PROVIDERS, RUBRICS, assess, assess_openrouter, overall_verdict

MAX_FILES = 30
MAX_CONTEXT_CHARS = 100_000
VERDICTS = {
    "no_concerns_detected": "No concerns detected",
    "needs_attention": "Needs attention",
    "insufficient_context": "Insufficient context",
    "unavailable": "Review unavailable",
    "skipped": "Review skipped",
}
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


@dataclass
class ReviewContext:
    state: dict
    total: int
    included: int
    limitations: list[str]

    @property
    def complete(self) -> bool:
        return self.included > 0 and not self.limitations


def _patch_complete(file_diff, metadata: dict, patch: str) -> bool:
    if "\nBinary files " in patch or "\nGIT binary patch" in patch:
        return False
    added = deleted = 0
    for hunk in file_diff.hunks:
        lines = hunk.splitlines()
        match = _HUNK.match(lines[0])
        old_count = new_count = 0
        for line in lines[1:]:
            if line.startswith("+"):
                added += 1
                new_count += 1
            elif line.startswith("-"):
                deleted += 1
                old_count += 1
            elif line.startswith(" "):
                old_count += 1
                new_count += 1
            elif not line.startswith("\\ No newline at end of file"):
                return False
        if not match or (old_count, new_count) != tuple(
            int(n) if n is not None else 1 for n in match.groups()
        ):
            return False
    if (added, deleted) != (metadata["additions"], metadata["deletions"]):
        return False
    return bool(file_diff.hunks) or any(
        marker in patch for marker in ("\nrename from ", "\nold mode ", "\nnew file mode ", "\ndeleted file mode ")
    )


def build_context(metadata: dict, diff: str) -> ReviewContext:
    limitations = []
    files = {f["path"]: f for f in metadata["files"]}
    total = metadata["changedFiles"]
    state = {
        "title": metadata["title"][:500],
        "description": metadata["body"][:10_000],
        "total_changed_files": total,
        "files": [],
        "coverage_complete": False,
    }
    while len(json.dumps(state, ensure_ascii=True)) > MAX_CONTEXT_CHARS:
        state["description"] = state["description"][:len(state["description"]) // 2]
    if state["title"] != metadata["title"] or state["description"] != metadata["body"]:
        limitations.append("PR title or description exceeded the context limit.")
    if (len(files) != total or len(files) != len(metadata["files"])
            or sum(f["additions"] for f in files.values()) != metadata["additions"]
            or sum(f["deletions"] for f in files.values()) != metadata["deletions"]):
        limitations.append("Changed-file metadata is incomplete or inconsistent.")

    patches = {}
    for patch in re.split(r"(?m)(?=^diff --git )", diff):
        if not patch.strip():
            continue
        parsed = parse_diff(patch)
        if len(parsed) != 1 or not parsed[0].path or parsed[0].path in patches:
            limitations.append("Some diff sections could not be identified reliably.")
            continue
        patches[parsed[0].path] = (parsed[0], patch)
    if set(patches) != set(files):
        limitations.append("Some changed files have no matching patch or metadata.")

    for path in sorted(files):
        if path not in patches:
            continue
        file_diff, patch = patches[path]
        if not _patch_complete(file_diff, files[path], patch):
            limitations.append("Binary, unavailable, or incomplete patches were omitted.")
            continue
        if len(state["files"]) >= MAX_FILES:
            limitations.append("The 30-file context limit omitted additional files.")
            break
        item = {
            "path": path, "status": file_diff.status,
            "additions": files[path]["additions"], "deletions": files[path]["deletions"],
            "patch": patch,
        }
        state["files"].append(item)
        if len(json.dumps(state, ensure_ascii=True)) > MAX_CONTEXT_CHARS:
            state["files"].pop()
            limitations.append("The 100,000-character context limit omitted whole patches.")
    if not state["files"]:
        limitations.append("No complete text patches were available for assessment.")
    context = ReviewContext(state, total, len(state["files"]), list(dict.fromkeys(limitations)))
    state["coverage_complete"] = context.complete
    return context


def render_report(repo: str, number: int, head: str, verdict: str,
                  context: ReviewContext | None, assessments: dict[str, Assessment],
                  reason: str = "") -> str:
    lines = [
        REVIEW_MARKER, "## DocSentry PR triage", "",
        f"**{VERDICTS[verdict]}**", "",
        f"Reviewed commit: `{head}` · [PR diff](https://github.com/{repo}/pull/{number}/files)",
        "",
    ]
    if reason:
        lines += [reason, ""]
    if context:
        lines += [f"Coverage: {context.included}/{context.total} changed files supplied as complete text patches.", ""]
    if assessments:
        lines += ["| Assessment | Result | Model confidence |", "|---|---|---|"]
        for key, (label, *_rest) in RUBRICS.items():
            answer = assessments[key]
            confidence = "unavailable" if answer.confidence is None else f"{answer.confidence:.2f}"
            lines.append(f"| {label} | {answer.choice.replace('_', ' ')} | {confidence} |")
        lines.append("")
        for key, answer in assessments.items():
            if answer.choice == "attention":
                lines += [f"- **{RUBRICS[key][0]}:** {RUBRICS[key][-1]}"]
            elif answer.choice == "unknown":
                lines += [f"- **{RUBRICS[key][0]}:** Insufficient evidence or model confidence; inspect manually."]
        lines.append("")
    if context and context.limitations:
        lines += ["Coverage limits:", "", *[f"- {item}" for item in context.limitations], ""]
    lines += [
        "Advisory triage of the supplied diff only. Tests were not run; existing tests and "
        "unchanged source files were not inspected. No concerns detected is not merge approval.",
        "Model confidence describes its answer distribution, not the probability that this PR is correct.",
    ]
    return "\n".join(lines)


def _write_result(env, verdict: str, head: str, summary: str) -> None:
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"review-verdict={verdict}\nreview-head-sha={head}\n")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(summary + "\n")
    print(f"DocSentry: {VERDICTS[verdict]}")


def run_review(config, env, github_factory=ReviewGitHub, assess_pr=None) -> int:
    head = ""
    provider = config["jev_provider"]
    if provider not in PROVIDERS:
        _write_result(env, "unavailable", head, "DocSentry: jev-provider must be typesafe or openrouter.")
        return 1
    provider_name, key_name = PROVIDERS[provider]
    assess_pr = assess_pr or (assess_openrouter if provider == "openrouter" else assess)
    try:
        if env.get("GITHUB_EVENT_NAME") not in ("pull_request", "pull_request_target"):
            raise ValueError("Review requires a pull request event")
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as event_file:
            event = json.load(event_file)
        repo = env["GITHUB_REPOSITORY"]
        number = event["number"]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
                or event["repository"]["full_name"] != repo
                or event["pull_request"]["base"]["repo"]["full_name"] != repo
                or type(number) is not int or number <= 0 or not config["github_token"]):
            raise ValueError("Invalid PR identity or missing token")
        github = github_factory(repo, number, config["github_token"])
        metadata = github.metadata()
        head = metadata["headRefOid"]
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            head = ""
            raise ValueError("Invalid head SHA")
        if metadata["state"] != "OPEN" or metadata["isDraft"]:
            _write_result(env, "skipped", head, "DocSentry: closed or draft PR; review skipped.")
            return 0

        context = None
        assessments = {}
        reason = ""
        exit_code = 0
        if not config[key_name]:
            verdict = "unavailable"
            reason = f"The {provider_name} API key is not configured. Set the {key_name.upper()} repository secret."
            exit_code = 1
        else:
            try:
                context = build_context(metadata, github.diff())
            except ReviewGitHubError:
                verdict = "unavailable"
                reason = "GitHub could not supply the PR diff; no assessment was made."
                exit_code = 1
            else:
                if not same_pr_revision(metadata, github.metadata()):
                    _write_result(env, "skipped", head, "DocSentry: PR changed during collection; result discarded.")
                    return 0
                if context.included:
                    try:
                        assessments = assess_pr(context.state, config[key_name], config["jev_model"])
                    except Exception:
                        # Provider errors may contain source text or secrets. Never render them.
                        verdict = "unavailable"
                        reason = f"{provider_name} assessment failed; no review verdict is available. Rerun the workflow."
                        exit_code = 1
                    else:
                        if not metadata["body"].strip():
                            assessments["scope"] = Assessment("unknown", None)
                        verdict = overall_verdict(assessments, context.complete)
                else:
                    verdict = "insufficient_context"
        report = render_report(repo, number, head, verdict, context, assessments, reason)
        try:
            published = github.upsert_comment(report, metadata)
        except ReviewGitHubError:
            _write_result(env, "unavailable", head, report + "\n\nPR comment publication failed; "
                          "check token permissions and rerun the workflow.")
            return 1
        if not published:
            _write_result(env, "skipped", head, "DocSentry: PR changed before publication; result discarded.")
            return 0
        _write_result(env, verdict, head, report)
        return exit_code
    except (ReviewGitHubError, OSError, ValueError, KeyError, TypeError):
        _write_result(env, "unavailable", head,
                      "DocSentry: review unavailable. Check GitHub event context, token permissions, "
                      "and connectivity. The PR comment may not have been updated.")
        return 1
