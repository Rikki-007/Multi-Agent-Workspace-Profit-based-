"""The Claude Code build path, with a fake `claude` that records what it was given."""

from __future__ import annotations

import json
import shutil
import sys
import textwrap

import pytest

from agency import graph as G
from agency.claude_code import ClaudeCodeRunner
from agency.lessons import LessonStore
from agency.schemas import JobState, LeadRecord
from agency.workspace import GitWorkspace

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

FAKE_CLAUDE = textwrap.dedent(
    """
    import json, sys, pathlib
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    appended = None
    if "--append-system-prompt-file" in args:
        appended = pathlib.Path(args[args.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    pathlib.Path("record.json").write_text(json.dumps({"args": args, "prompt": prompt, "appended": appended}))
    page = pathlib.Path("src/App.tsx")
    page.parent.mkdir(exist_ok=True)
    page.write_text("export const App = () => <main>fixed</main>;\\n")
    pathlib.Path(".env").write_text("SECRET_TOKEN=should-never-reach-a-lesson\\n")
    print(json.dumps({"type": "result", "is_error": False, "result": "Guarded the testimonials map.", "session_id": "s1", "total_cost_usd": 0.01}))
    """
)


@pytest.fixture
def setup(tmp_path):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE)
    runner = ClaudeCodeRunner(command_prefix=[sys.executable, str(fake)], max_budget_usd=1.5)
    ctx = G.AgencyContext(
        lessons=LessonStore(tmp_path / "lessons.sqlite"),
        workspaces=G.GitWorkspaces(tmp_path / "ws"),
        claude=runner,
    )
    return ctx, tmp_path / "ws" / "job1"


def test_command_passes_lessons_by_file_and_prompt_on_stdin():
    cmd = ClaudeCodeRunner(model="opus").command(None)
    assert cmd[:2] == ["claude", "-p"]
    assert "--append-system-prompt-file" not in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_claude_build_injects_lessons_commits_and_diffs(setup):
    ctx, repo = setup
    lessons_md = "# Lessons from past verified fixes\n\nUse a guard before .map()."
    state = JobState(
        job_id="job1",
        lead=LeadRecord(business_name="Acme"),
        optimizer_feedback=["console error: Cannot read properties of undefined (reading 'map')"],
        lessons_context=lessons_md,
        injected_lesson_ids=[7],
    )

    update = _build_with(ctx, state)
    result = update["build_result"]
    assert result.success and len(result.commit_hash) == 40
    assert result.summary == "Guarded the testimonials map."
    assert "with 1 past lesson(s) in context" in update["audit_log"][0].message

    record = json.loads((repo / "record.json").read_text())
    assert record["appended"] == lessons_md  # lessons went in via the system prompt file...
    assert lessons_md not in record["prompt"]  # ...not the task prompt
    assert "Cannot read properties of undefined" in record["prompt"]
    assert record["args"][record["args"].index("--max-budget-usd") + 1] == "1.5"
    assert not any(p.name == "lessons.md" for p in repo.rglob("*"))  # nothing leaked into the repo

    assert "Acme" in record["prompt"]  # the lead's facts reach the agent

    ws = GitWorkspace(repo)
    diff = ws.diff(None, result.commit_hash)
    assert "src/App.tsx" in diff
    assert ".env" not in diff and "should-never-reach" not in diff  # excluded paths
    assert "check-rules.mjs" not in diff  # the template baseline is its own commit


def test_workspace_is_seeded_from_template_once(tmp_path):
    factory = G.GitWorkspaces(tmp_path / "ws")
    ws = factory("job9")  # no spec: T1 editorial
    baseline = ws.head()
    assert baseline is not None
    assert (ws.path / "CLAUDE.md").exists() and (ws.path / "RULES.md").exists() and (ws.path / "src" / "content.ts").exists()
    assert json.loads((ws.path / ".agency" / "policy.json").read_text())["tier"] == "T1"
    for build_output in ("node_modules", ".next", "out"):  # installed or built later, never copied
        assert not (ws.path / build_output).exists()

    (ws.path / "src" / "content.ts").write_text("// edited by the agent\n")
    ws.commit_all("agent edit")
    assert factory("job9").head() != baseline  # a second lookup doesn't re-seed over the work
    assert (ws.path / "src" / "content.ts").read_text() == "// edited by the agent\n"


def _build_with(ctx, state):
    class _Runtime:  # minimal stand-in for langgraph.runtime.Runtime
        context = ctx

    return G.build(state, _Runtime())
