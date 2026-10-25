"""
A stand-in for `claude -p` in the referee's self-tests: it plays the working session
of `factory drive` without a model, a token or a network.

It reads the prompt on stdin, finds the run file and the step, and — as
FAKE_CLAUDE_MODE says — does the step well ("good"), does nothing ("stuck"), or
writes an Escalation ("escalate"). It answers the JSON `claude -p --output-format
json` answers, with a running cost per session kept beside the run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import factory  # noqa: E402

SECTIONS = {
    "intake": ("Intent", "**Problem.** Labs have no type.\n\n**Outcome.** A lab has a type.\n\n"
                         "**Touches.** `docs/plans/labs.md`"),
    "context": ("Context", "**Modules** — Labs.\n\n**Read with judgement:** no FSD angle."),
    "spec": ("Spec", "- **{run}.AC-1** — Given a lab, when it is saved without a type, then it is refused."),
}


def plan_step(root: Path, run: factory.Run, step: str, prompt: str) -> bool:
    """
    Plays a plan run's own steps from the referee's `{brief}`: names the sources, drafts the plan the
    brief points at and links it from the tracker, and writes the run back and commits at package. False for a
    step it does not play.
    """
    if factory.record_kind(run.record) != factory.PLAN or step not in ("sources", "draft-plan", "package"):
        return False
    # The section's id only: how the CLI's stdin is decoded differs by platform, and "§" with it.
    fsd = re.search(r"- FSD: \S*?(\d+(?:\.\d+)*)", prompt)
    cited = f"§{fsd.group(1)}" if fsd else "§1"
    target = re.search(r"Write the plan at `([^`]+)`", prompt)
    if step == "sources":
        run.sections["Sources"] = (f"**FSD:** V1 {cited}\n**Decisions:** none — the FSD answers it\n"
                                   "**Live check:** could not check — no Drive access in an unattended session")
    elif step == "draft-plan" and target:
        rel = target.group(1)
        (root / rel).write_text(
            f"# {run.title} — Design\n\n**Status:** `Not started` · **Owner:** Test Person · **FSD:** {cited} · "
            "**Checked against:** FSD V1 · **Depends on:** nothing\n\n"
            f"## 1. What the FSD says\n\n{cited}: what the feature must do.\n\n"
            "## 9. Tasks\n\n| # | Task | FSD | Role | Status | Jira | PR | Merged |\n|---|---|---|---|---|---|---|---|\n"
            f"| 1 | Add | {cited} | BE | Not started | — | — | — |\n", encoding="utf-8")
        tracker = root / "docs" / "project.md"
        name = Path(rel).name
        tracker.write_text(re.sub(r"(\| " + re.escape(run.title.split(" — ")[0]) + r" \| [^|]*\| )\s*\|",
                                  rf"\1[`{name}`](plans/{name}) |", tracker.read_text(encoding="utf-8")),
                           encoding="utf-8")
        run.sections["Design"] = f"**Plan:** [{Path(rel).stem}](../../plans/{name})"
    elif step == "package":
        tracker = root / "docs" / "project.md"
        tracker.write_text(tracker.read_text(encoding="utf-8").replace(
            "| `Not started` |", f"| `Not started` — plan by [{run.id}](factory/runs/{run.id}.md) |", 1), encoding="utf-8")
        subprocess.run(["git", "add", "docs"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "The plan"], cwd=root, capture_output=True)
        run.sections["Package"] = "The plan, and the feature's row naming the run."
    return True


def book_step(root: Path, run: factory.Run, step: str) -> bool:
    """
    Plays a book run's working steps from files the test names: FAKE_BOOK_INVENTORY is the Inventory
    section, FAKE_BOOK_PROJECT the project.md the features step writes; package commits and fills its
    section. False for a step it does not play.
    """
    if factory.record_kind(run.record) != factory.BOOK or step not in ("inventory", "features", "package"):
        return False
    if step == "inventory" and os.environ.get("FAKE_BOOK_INVENTORY"):
        run.sections["Inventory"] = Path(os.environ["FAKE_BOOK_INVENTORY"]).read_text(encoding="utf-8").strip()
    elif step == "features" and os.environ.get("FAKE_BOOK_PROJECT"):
        (root / "docs" / "project.md").write_text(Path(os.environ["FAKE_BOOK_PROJECT"]).read_text(encoding="utf-8"),
                                                  encoding="utf-8")
        # FAKE_BOOK_OVERVIEW: the system overview the first book writes where it is still the template.
        if os.environ.get("FAKE_BOOK_OVERVIEW"):
            (root / "docs" / "system-overview.md").write_text(
                Path(os.environ["FAKE_BOOK_OVERVIEW"]).read_text(encoding="utf-8"), encoding="utf-8")
        run.sections["Features"] = "The phases and rows of docs/project.md, from the Inventory."
    elif step == "package":
        subprocess.run(["git", "add", "docs"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "The book from the FSD"], cwd=root, capture_output=True)
        run.sections["Package"] = "project.md committed; the person merges the branch."
    return True


def revision_step(root: Path, run: factory.Run, step: str) -> bool:
    """
    Plays a revision run's working steps: FAKE_REVISION is a JSON file of the triage — the book's files
    to write and the run file's sections — committed as a session would; package commits and fills its
    section. False for a step it does not play.
    """
    if factory.record_kind(run.record) != factory.REVISION or step not in ("triage", "package"):
        return False
    if step == "triage" and os.environ.get("FAKE_REVISION"):
        triage = json.loads(Path(os.environ["FAKE_REVISION"]).read_text(encoding="utf-8"))
        for rel, text in triage["files"].items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "docs"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "Take in the new FSD version"], cwd=root, capture_output=True)
        run.sections.update(triage["sections"])
    elif step == "package":
        subprocess.run(["git", "add", "docs"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "The revision"], cwd=root, capture_output=True)
        run.sections["Package"] = "The book brought up to the new version; the person merges the branch."
    return True


def review(agent: str) -> int:
    """Plays a judging agent: FAKE_REVIEW is the reviewer's report, FAKE_AUDIT the requirements auditor's."""
    if agent == "factory-spec-auditor":
        report = os.environ.get("FAKE_AUDIT", "Verdict: CONSISTENT\nRequirements:\nnone")
    elif agent == "book-auditor":
        # FAKE_BOOK_AUDIT: one report, or several separated by a line of "=====", one per call in turn
        # (the last repeats) — a NOT READY that a rewrite answers, then READY.
        reports = os.environ.get("FAKE_BOOK_AUDIT", "Verdict: READY\nFindings:\n- none").split("\n=====\n")
        ledger = Path.cwd() / ".factory" / "fake-book-audits"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        calls = int(ledger.read_text()) if ledger.exists() else 0
        ledger.write_text(str(calls + 1))
        report = reports[min(calls, len(reports) - 1)]
    else:
        report = os.environ.get("FAKE_REVIEW", "Verdict: READY\nFindings:\nnone\nWhat is sound: all of it.")
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": report,
                      "num_turns": 4, "total_cost_usd": 0.4, "usage": {},
                      "modelUsage": {"fake": {"inputTokens": 5, "outputTokens": 300, "cacheReadInputTokens": 20000,
                                              "cacheCreationInputTokens": 4000, "costUSD": 0.4}}}))
    return 0


LIMIT = {"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "total_cost_usd": 0,
         "result": "You've hit your session limit · resets 12:20am (Asia/Karachi)"}


def main() -> int:
    prompt = sys.stdin.read()
    args = sys.argv[1:]
    # FAKE_CLAUDE_MODE=limit: the account's usage limit answers, for the session and the agents alike.
    if os.environ.get("FAKE_CLAUDE_MODE") == "limit":
        print(json.dumps(LIMIT))
        return 1
    if "--agent" in args:
        return review(args[args.index("--agent") + 1])
    session = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
    root = Path.cwd()
    if prompt.strip() == "/compact":
        ledger = root / ".factory" / f"fake-cost-{session}"
        spent = (float(ledger.read_text()) if ledger.exists() else 0.0) + 0.05
        ledger.write_text(str(spent))
        (root / ".factory" / "fake-compactions").open("a", encoding="utf-8").write(session + "\n")
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": session,
                          "result": "Compacted.", "num_turns": 1, "total_cost_usd": spent, "usage": {}}))
        return 0
    run_file = root / re.search(r"The run file is (\S+)\.", prompt).group(1)
    step = re.search(r"## Step: (\S+)", prompt)
    mode = os.environ.get("FAKE_CLAUDE_MODE", "good")

    run = factory.parse_run(run_file.read_text(encoding="utf-8"), run_file)
    if "stream-json" in args:
        # Like the CLI streaming: what it does, as it does it, before the result.
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": f"Working on {step.group(1) if step else 'the run'}."},
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(run_file)}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(run_file)}}]}}), flush=True)
    if step and mode == "good" and plan_step(root, run, step.group(1), prompt):
        pass
    elif step and mode == "good" and book_step(root, run, step.group(1)):
        pass
    elif step and mode == "good" and revision_step(root, run, step.group(1)):
        pass
    elif step and mode == "good" and step.group(1) in SECTIONS:
        name, text = SECTIONS[step.group(1)]
        run.sections[name] = text.replace("{run}", run.id)
    elif step and mode == "escalate":
        run.sections["Escalation"] = "The defect row does not say which labs are affected; a person decides."
    run_file.write_text(factory.render_run(run, factory.load_config(root)), encoding="utf-8")

    ledger = root / ".factory" / f"fake-cost-{session}"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    spent = (float(ledger.read_text()) if ledger.exists() else 0.0) + 0.25
    ledger.write_text(str(spent))
    calls = round(spent / 0.25)
    # Like the CLI: `usage` is this call's, `modelUsage` and `total_cost_usd` the session's running totals.
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": session,
                      "result": f"Did {step.group(1) if step else 'nothing'} ({mode}).", "num_turns": 3,
                      "total_cost_usd": spent,
                      "usage": {"input_tokens": 10, "cache_read_input_tokens": 40000,
                                "cache_creation_input_tokens": 0, "output_tokens": 500},
                      "modelUsage": {"fake": {"inputTokens": 10 * calls, "outputTokens": 500 * calls,
                                              "cacheReadInputTokens": 40000 * calls,
                                              "cacheCreationInputTokens": 0, "costUSD": spent}}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
