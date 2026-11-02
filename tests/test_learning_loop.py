"""End to end: a QA failure gets fixed, the fix is banked, and the next job reuses it."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agency import graph as G
from agency.lessons import LessonStore
from agency.schemas import JobState

CONSOLE_FP_SIGNATURE = "console:TypeError:cannot read properties of undefined"


@pytest.fixture
def ctx(tmp_path):
    return G.AgencyContext(lessons=LessonStore(tmp_path / "lessons.sqlite"))


def _script(monkeypatch, values):
    """Drive the stub's random choices: build ok?, QA fail?, lint ok? ..."""
    it = iter(values)
    monkeypatch.setattr(G._rng, "random", lambda: next(it))
    monkeypatch.setattr(G._rng, "randint", lambda a, b: 95)  # lighthouse stays green
    monkeypatch.setattr(G.STUB, "failure_rate", 1.0)


def _run_job(graph, ctx, job_id):
    config = G.job_config(job_id)
    graph.invoke(JobState(job_id=job_id), config, context=ctx)
    graph.invoke(Command(resume={"approved": True}), config, context=ctx)
    return JobState.model_validate(graph.get_state(config).values)


def test_fix_is_banked_then_reused(monkeypatch, ctx):
    # Record what each build node actually receives in its state.
    seen_contexts: list[str | None] = []
    real_build = G.build

    def spy_build(state, runtime):
        seen_contexts.append(state.lessons_context)
        return real_build(state, runtime)

    monkeypatch.setattr(G, "build", spy_build)
    graph = G.build_graph().compile(checkpointer=InMemorySaver(serde=G._serde()))

    # Job 1: attempt 1 builds but QA hits a console error (lint ok); attempt 2 passes.
    _script(monkeypatch, [0.9, 0.1, 0.1, 0.9, 1.0])
    job1 = _run_job(graph, ctx, "job1")
    assert job1.build_attempts == 2 and job1.qa_report.passes()
    assert job1.lessons_recorded == 1
    optimizer_msg = next(e.message for e in job1.audit_log if e.node == "optimizer")
    assert "no past lessons match" in optimizer_msg
    [lesson] = ctx.lessons.top()
    assert lesson["signature"] == CONSOLE_FP_SIGNATURE and lesson["verified_count"] == 1

    # Job 2: same console error -> the banked fix is injected into the next build's context.
    seen_contexts.clear()
    _script(monkeypatch, [0.9, 0.1, 0.1, 0.9, 1.0])
    job2 = _run_job(graph, ctx, "job2")

    assert seen_contexts[0] is None  # first build: nothing to inject yet
    assert "Lessons from past verified fixes" in seen_contexts[1]
    assert "exact match" in seen_contexts[1]
    assert "Testimonials.tsx" in seen_contexts[1]
    assert job2.lessons_context is None and job2.pending_fix is None  # cleared once QA passes

    [row] = ctx.lessons.top()
    assert row["helped"] == 1 and row["failed"] == 0  # injected, and the error went away
    assert row["verified_count"] == 2  # job2's passing diff verified the same patch again
    assert ctx.lessons.stats()["helped"] == 1


def test_lessons_are_not_checkpointed_resources(ctx):
    """The context carries live resources; the state only carries JSON-safe data."""
    graph = G.build_graph().compile(checkpointer=InMemorySaver(serde=G._serde()))
    graph.invoke(JobState(job_id="j"), G.job_config("j"), context=ctx)
    values = graph.get_state(G.job_config("j")).values
    assert "lessons" not in values and "workspaces" not in values
