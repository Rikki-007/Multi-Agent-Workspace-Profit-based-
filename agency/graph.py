"""LangGraph state machine for a single website job.

Flow::

    lead_discovery ──(qualified)──> compliance_check ──> approval_gate ──(approved)──> strategy
          │                                                   │                          │
          └──(disqualified)──> END              (rejected)──> END                        v
                                                                      ┌──────────────> build
                                                                      │                  │
                                                                  optimizer <──(fail)── qa ──(out of attempts)──> END
                                                                                         │
                                                                                      (pass)
                                                                                         v
                                                                  preview_deploy ──> closing ──> END

``approval_gate`` and ``closing`` call ``interrupt()``. The run is checkpointed to
SQLite under ``thread_id = job_id`` and can be resumed days later, from a new
process, with ``Command(resume=...)``.

The optimizer loop learns: ``optimizer`` looks up past verified fixes for the current
errors and hands them to the next ``build`` (Claude Code's system prompt), and ``qa``
records the diff that made a failing build pass as a new verified fix. Resources (lesson
store, workspaces, Claude runner) arrive through LangGraph's runtime context, never state.

Nodes other than ``build`` (when a Claude runner and git workspaces are configured) are
stubs that produce valid outputs so the wiring can be exercised end to end.
"""

from __future__ import annotations

import random
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agency import schemas
from agency.claude_code import ClaudeCodeRunner, build_prompt
from agency.lessons import DEFAULT_LESSONS_PATH, LessonStore, extract_errors, render_context
from agency.schemas import (
    AcceptanceCriterion,
    AuditEvent,
    BuildResult,
    ColorPalette,
    ComplianceDecision,
    EntityType,
    JobStage,
    JobState,
    LayoutType,
    LeadRecord,
    LeadStatus,
    PendingFix,
    ProductSpec,
    QAReport,
    SectionType,
    SiteStyle,
    TemplateTier,
)
from agency.templates import pick_template, validate_package, write_policy
from agency.workspace import GitWorkspace, StubWorkspace, Workspace

DEFAULT_DB_PATH = Path("data") / "checkpoints.sqlite"
MIN_LIGHTHOUSE_SCORE = 90


class Node:
    LEAD_DISCOVERY = "lead_discovery"
    COMPLIANCE_CHECK = "compliance_check"
    APPROVAL_GATE = "approval_gate"
    STRATEGY = "strategy"
    BUILD = "build"
    QA = "qa"
    OPTIMIZER = "optimizer"
    PREVIEW_DEPLOY = "preview_deploy"
    CLOSING = "closing"


@dataclass
class StubSettings:
    """Knobs that make the stub pipeline look like real work, for exercising the dashboard."""

    delay: float = 0.0  # seconds each working node sleeps
    failure_rate: float = 0.0  # chance a build or QA run fails, to exercise the optimizer loop


STUB = StubSettings()
_rng = random.Random()  # stub randomness only; seed it for reproducible runs


def stub_workspaces(job_id: str, spec: ProductSpec | None = None) -> Workspace:
    return StubWorkspace()


@dataclass
class GitWorkspaces:
    """One git repo per job under ``root``, seeded on first use from the registry template that
    matches the spec's tier and style, with that tier's dependency policy written alongside."""

    root: Path
    install: bool = False  # `npm ci` after seeding; needed before a real Claude Code build

    def __call__(self, job_id: str, spec: ProductSpec | None = None) -> Workspace:
        workspace = GitWorkspace(self.root / job_id).ensure()
        if workspace.head() is None:  # no commits yet: (re)seed
            tier, style = (spec.tier.value, spec.style.value) if spec else ("T1", None)
            write_policy(workspace.path, tier)
            workspace.seed(pick_template(tier, style).path, install=self.install)
        return workspace


@dataclass
class AgencyContext:
    """Per-run resources injected by LangGraph (``invoke(..., context=...)``); never checkpointed."""

    lessons: LessonStore
    workspaces: Callable[..., Workspace] = field(default=stub_workspaces)  # (job_id, spec=None)
    claude: ClaudeCodeRunner | None = None  # None: build node uses the stub


@cache
def default_context() -> AgencyContext:
    return AgencyContext(lessons=LessonStore(DEFAULT_LESSONS_PATH))


def _ctx(runtime: Runtime[AgencyContext] | None) -> AgencyContext:
    return runtime.context if runtime is not None and runtime.context is not None else default_context()


def _stub_work() -> None:
    if STUB.delay:
        time.sleep(STUB.delay * _rng.uniform(0.6, 1.4))


def _event(node: str, message: str, level: str = "info", **data: Any) -> list[AuditEvent]:
    return [AuditEvent(node=node, message=message, level=level, data=data)]


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #


def lead_discovery(state: JobState) -> dict[str, Any]:
    """Find a lead (if the job wasn't seeded with one) and qualify it."""
    # TODO: replace with a real discovery agent (maps/directory scraping, enrichment).
    _stub_work()
    lead = state.lead or LeadRecord(
        business_name="Example Plumbing Ltd",
        address="1 High Street, Exampletown",
        entity_type=EntityType.COMPANY,
        email="hello@example.com",
        source="stub",
    )

    qualified = bool(lead.email or lead.phone) and not lead.website
    status = LeadStatus.QUALIFIED if qualified else LeadStatus.DISQUALIFIED
    return {
        "lead": lead.model_copy(update={"status": status}),
        "stage": JobStage.LEAD_DISCOVERY if qualified else JobStage.ABANDONED,
        "audit_log": _event(Node.LEAD_DISCOVERY, f"lead {status.value}", business=lead.business_name),
    }


def compliance_check(state: JobState) -> dict[str, Any]:
    """Deterministic pre-screen. Kept separate from the gate so it never re-runs on resume."""
    assert state.lead is not None
    _stub_work()
    flags: list[str] = []
    if state.lead.entity_type is EntityType.SOLE_TRADER:
        flags.append("sole_trader: individual subscriber under PECR; cold email needs prior consent")
    elif state.lead.entity_type is EntityType.UNKNOWN:
        flags.append("entity_type unknown: verify against the company register before outreach")
    if not state.lead.email:
        flags.append("no email address: outreach channel limited")

    return {
        "compliance_flags": flags,
        "stage": JobStage.COMPLIANCE_CHECK,
        "audit_log": _event(
            Node.COMPLIANCE_CHECK,
            f"{len(flags)} flag(s) raised" + (": " + "; ".join(flags) if flags else ""),
            "warn" if flags else "info",
            flags=flags,
        ),
    }


def approval_gate(state: JobState) -> dict[str, Any]:
    """Pause until a human approves or rejects outreach to this lead.

    Resume with ``Command(resume={"approved": bool, "notes": str | None})``.
    Everything before ``interrupt()`` re-executes on resume, so keep it side-effect free.
    """
    assert state.lead is not None
    reply = interrupt(
        {
            "kind": "compliance_approval",
            "job_id": state.job_id,
            "lead": state.lead.model_dump(mode="json"),
            "flags": state.compliance_flags,
        }
    )
    approved, notes = _parse_approval(reply)
    decision = ComplianceDecision.APPROVED if approved else ComplianceDecision.REJECTED
    return {
        "compliance_decision": decision,
        "reviewer_notes": notes,
        "lead": state.lead.model_copy(
            update={"status": LeadStatus.APPROVED if approved else LeadStatus.REJECTED}
        ),
        "stage": JobStage.STRATEGY if approved else JobStage.ABANDONED,
        "audit_log": _event(Node.APPROVAL_GATE, f"human {decision.value}", notes=notes),
    }


def strategy(state: JobState) -> dict[str, Any]:
    """Turn the lead into a constrained ProductSpec. A client's brief fixes the tier and style."""
    # TODO: LLM strategist with structured output bound to ProductSpec.
    _stub_work()
    brief = state.brief
    spec = ProductSpec(
        layout=LayoutType.SINGLE_PAGE,
        color_palette=ColorPalette.TRUST_BLUE,
        tier=brief.tier if brief else TemplateTier.T1,
        style=brief.style if brief else SiteStyle.MINIMALISM,
        sections=[
            SectionType.HERO,
            SectionType.SERVICES,
            SectionType.TESTIMONIALS,
            SectionType.CONTACT,
            SectionType.FOOTER,
        ],
        acceptance_criteria=[
            AcceptanceCriterion.BUILD_PASSES,
            AcceptanceCriterion.LINT_PASSES,
            AcceptanceCriterion.NO_CONSOLE_ERRORS,
            AcceptanceCriterion.LIGHTHOUSE_PERF_90,
            AcceptanceCriterion.MOBILE_RESPONSIVE,
        ],
        headline=f"{state.lead.business_name if state.lead else 'Your business'}, online in days",
    )
    return {
        "spec": spec,
        "stage": JobStage.BUILD,
        "audit_log": _event(
            Node.STRATEGY,
            f"spec produced: {spec.tier.value} {spec.style.value}" + (f" from brief {brief.code}" if brief else ""),
            layout=spec.layout.value,
        ),
    }


def build(state: JobState, runtime: Runtime[AgencyContext]) -> dict[str, Any]:
    """Generate and commit the site from the spec, with any injected lessons in context."""
    ctx = _ctx(runtime)
    attempt = state.build_attempts + 1
    workspace = ctx.workspaces(state.job_id, state.spec)
    if ctx.claude is not None and workspace.path is not None:
        result = _claude_build(state, ctx.claude, workspace, attempt)
    else:
        result = _stub_build(attempt)
    outcome = f"committed {result.commit_hash}" if result.success else f"failed: {result.error_logs[0]}"
    lessons = f" (with {len(state.injected_lesson_ids)} past lesson(s) in context)" if state.lessons_context else ""
    message = f"build attempt {attempt}/{state.max_build_attempts} {outcome}{lessons}"
    return {
        "build_result": result,
        "build_attempts": attempt,
        "stage": JobStage.QA,
        "audit_log": _event(Node.BUILD, message, "info" if result.success else "error", success=result.success),
    }


def _stub_build(attempt: int) -> BuildResult:
    _stub_work()
    if _rng.random() < STUB.failure_rate / 2:
        return BuildResult(
            success=False,
            error_logs=["src/components/Hero.tsx(14,7): error TS2322: Type 'string' is not assignable to type 'number'."],
        )
    return BuildResult(success=True, commit_hash=f"{attempt:07x}", summary="stub build")


def _claude_build(state: JobState, claude: ClaudeCodeRunner, workspace: Workspace, attempt: int) -> BuildResult:
    assert workspace.path is not None
    spec = state.spec.model_dump_json(indent=2) if state.spec else "{}"
    lead = state.lead.model_dump_json(indent=2) if state.lead else "{}"
    prompt = build_prompt(spec, lead, state.optimizer_feedback, attempt, state.max_build_attempts)
    run = claude.run(workspace.path, prompt, state.lessons_context)
    summary = (run.result.strip().splitlines() or [""])[-1][:300] or None
    commit = workspace.commit_all(f"agency: build attempt {attempt} for {state.job_id}\n\n{summary or ''}")
    if not run.ok:
        return BuildResult(success=False, commit_hash=commit, error_logs=[f"claude run failed: {run.result[:500]}"])
    if commit is None:
        return BuildResult(success=False, error_logs=["claude produced no changes and the repo has no commits"])
    # The tier's dependency policy is checked here, outside the agent's reach: a T1 site that
    # pulled in Three.js fails the build and the optimizer feeds the violation back.
    tier = state.spec.tier.value if state.spec else "T1"
    if violations := validate_package(workspace.path, tier):
        return BuildResult(success=False, commit_hash=commit, error_logs=[f"dependency policy: {v}" for v in violations])
    return BuildResult(success=True, commit_hash=commit, summary=summary)


def qa(state: JobState, runtime: Runtime[AgencyContext]) -> dict[str, Any]:
    """Run build, lint, Lighthouse, and console checks; score injected lessons; record verified fixes."""
    # TODO: run real tooling against the built preview.
    ctx = _ctx(runtime)
    _stub_work()
    built = bool(state.build_result and state.build_result.success)
    if built and _rng.random() < STUB.failure_rate:
        lint_ok = _rng.random() < 0.5
        report = QAReport(
            build_pass=True,
            linter_pass=lint_ok,
            lighthouse_score=_rng.randint(55, 89),
            console_errors=["Uncaught TypeError: Cannot read properties of undefined (reading 'map') at Testimonials.tsx:22"],
            lint_errors=[] if lint_ok else ["src/components/Testimonials.tsx 19:30 error 'idx' is defined but never used no-unused-vars"],
        )
    else:
        report = QAReport(build_pass=built, linter_pass=built, lighthouse_score=95 if built else 0)

    passed = report.passes(MIN_LIGHTHOUSE_SCORE)
    exhausted = not passed and state.build_attempts >= state.max_build_attempts
    stage = JobStage.PREVIEW_DEPLOYED if passed else JobStage.FAILED if exhausted else JobStage.OPTIMIZING

    # Learning: score the lessons injected into this build, then bank the fix if QA passed.
    errors = extract_errors(state.build_result, report, MIN_LIGHTHOUSE_SCORE)
    ctx.lessons.observe_errors(state.job_id, state.build_attempts, errors)
    helped, failed = ctx.lessons.resolve_uses(
        state.job_id, {e.fingerprint for e in errors}, can_judge_resolved=report.build_pass
    )
    recorded: list[int] = []
    pending = state.pending_fix
    if passed and pending and pending.errors and state.build_result:
        head = state.build_result.commit_hash
        diff = ctx.workspaces(state.job_id).diff(pending.base_commit, head)
        recorded = ctx.lessons.record_fix(
            pending.errors, diff, job_id=state.job_id,
            commit=head or f"attempt-{state.build_attempts}", summary=state.build_result.summary,
        )

    learning = []
    if helped or failed:
        learning.append(f"lessons: {helped} helped, {failed} didn't")
    if recorded:
        learning.append(f"banked {len(recorded)} verified fix(es)")
    updates: dict[str, Any] = {
        "qa_report": report,
        "stage": stage,
        "lessons_recorded": state.lessons_recorded + len(recorded),
        "audit_log": _event(
            Node.QA,
            _qa_message(report, passed, exhausted) + ("; " + "; ".join(learning) if learning else ""),
            "info" if passed else "error" if exhausted else "warn",
            score=report.lighthouse_score,
        ),
    }
    if passed:
        updates |= {"pending_fix": None, "lessons_context": None, "injected_lesson_ids": []}
    return updates


def optimizer(state: JobState, runtime: Runtime[AgencyContext]) -> dict[str, Any]:
    """Turn a failed QA run into feedback plus relevant past fixes for the next build."""
    # TODO: LLM optimizer that diagnoses failures and prioritises fixes.
    ctx = _ctx(runtime)
    _stub_work()
    report, build_result = state.qa_report, state.build_result
    feedback: list[str] = []
    if report:
        if not report.build_pass:
            feedback.append("fix build errors: " + "; ".join(build_result.error_logs if build_result else []))
        if not report.linter_pass:
            feedback.append("fix lint errors" + (": " + "; ".join(report.lint_errors) if report.lint_errors else ""))
        if report.build_pass and report.lighthouse_score < MIN_LIGHTHOUSE_SCORE:
            feedback.append(f"raise Lighthouse score from {report.lighthouse_score} to {MIN_LIGHTHOUSE_SCORE}+")
        feedback.extend(f"console error: {e}" for e in report.console_errors)

    errors = extract_errors(build_result, report, MIN_LIGHTHOUSE_SCORE)
    lessons = ctx.lessons.find(errors)
    ctx.lessons.record_uses(state.job_id, state.build_attempts, lessons)
    injected = ", ".join(f"#{l.fix_id} {l.signature} ({l.match_kind})" for l in lessons)
    message = f"{len(feedback)} fix(es) queued: " + "; ".join(feedback)
    message += f" | injected {len(lessons)} past lesson(s): {injected}" if lessons else " | no past lessons match"
    return {
        "optimizer_feedback": feedback,
        "pending_fix": PendingFix(base_commit=build_result.commit_hash if build_result else None, errors=errors),
        "lessons_context": render_context(lessons),
        "injected_lesson_ids": [l.fix_id for l in lessons],
        "stage": JobStage.BUILD,
        "audit_log": _event(Node.OPTIMIZER, message),
    }


def preview_deploy(state: JobState) -> dict[str, Any]:
    """Publish the passing build to a preview URL."""
    # TODO: deploy to the preview host and return its URL.
    _stub_work()
    url = f"https://preview.example.invalid/{state.job_id}"
    return {
        "preview_url": url,
        "stage": JobStage.PREVIEW_DEPLOYED,
        "audit_log": _event(Node.PREVIEW_DEPLOY, "preview deployed", url=url),
    }


def closing(state: JobState) -> dict[str, Any]:
    """Send the preview to the client and wait (possibly days) for their reply.

    Resume with ``Command(resume={"accepted": bool, "message": str | None})``.
    """
    # TODO: send the outreach email before interrupting, guarded so it isn't re-sent on resume.
    assert state.lead is not None
    reply = interrupt(
        {
            "kind": "client_reply",
            "job_id": state.job_id,
            "preview_url": state.preview_url,
            "to": state.lead.email,
        }
    )
    accepted, message = _parse_client_reply(reply)
    return {
        "client_response": message,
        "lead": state.lead.model_copy(update={"status": LeadStatus.WON if accepted else LeadStatus.LOST}),
        "stage": JobStage.COMPLETED,
        "audit_log": _event(Node.CLOSING, "client accepted" if accepted else "client declined"),
    }


def _qa_message(report: QAReport, passed: bool, exhausted: bool) -> str:
    detail = (
        f"build={'ok' if report.build_pass else 'FAIL'} lint={'ok' if report.linter_pass else 'FAIL'}"
        f" lighthouse={report.lighthouse_score} console_errors={len(report.console_errors)}"
    )
    if passed:
        return f"QA passed ({detail})"
    return f"QA failed ({detail})" + ("; out of build attempts" if exhausted else "")


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def _after_lead(state: JobState) -> str:
    return Node.COMPLIANCE_CHECK if state.lead and state.lead.status is LeadStatus.QUALIFIED else END


def _after_approval(state: JobState) -> str:
    return Node.STRATEGY if state.compliance_decision is ComplianceDecision.APPROVED else END


def _after_qa(state: JobState) -> str:
    if state.stage is JobStage.PREVIEW_DEPLOYED:
        return Node.PREVIEW_DEPLOY
    if state.stage is JobStage.FAILED:
        return END
    return Node.OPTIMIZER


def _parse_approval(reply: Any) -> tuple[bool, str | None]:
    if isinstance(reply, dict):
        return bool(reply.get("approved")), reply.get("notes")
    return bool(reply), None


def _parse_client_reply(reply: Any) -> tuple[bool, str | None]:
    if isinstance(reply, dict):
        return bool(reply.get("accepted")), reply.get("message")
    return bool(reply), None


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_graph() -> StateGraph:
    g = StateGraph(JobState, context_schema=AgencyContext)
    g.add_node(Node.LEAD_DISCOVERY, lead_discovery)
    g.add_node(Node.COMPLIANCE_CHECK, compliance_check)
    g.add_node(Node.APPROVAL_GATE, approval_gate)
    g.add_node(Node.STRATEGY, strategy)
    g.add_node(Node.BUILD, build)
    g.add_node(Node.QA, qa)
    g.add_node(Node.OPTIMIZER, optimizer)
    g.add_node(Node.PREVIEW_DEPLOY, preview_deploy)
    g.add_node(Node.CLOSING, closing)

    g.add_edge(START, Node.LEAD_DISCOVERY)
    g.add_conditional_edges(Node.LEAD_DISCOVERY, _after_lead, [Node.COMPLIANCE_CHECK, END])
    g.add_edge(Node.COMPLIANCE_CHECK, Node.APPROVAL_GATE)
    g.add_conditional_edges(Node.APPROVAL_GATE, _after_approval, [Node.STRATEGY, END])
    g.add_edge(Node.STRATEGY, Node.BUILD)
    g.add_edge(Node.BUILD, Node.QA)
    g.add_conditional_edges(Node.QA, _after_qa, [Node.PREVIEW_DEPLOY, Node.OPTIMIZER, END])
    g.add_edge(Node.OPTIMIZER, Node.BUILD)
    g.add_edge(Node.PREVIEW_DEPLOY, Node.CLOSING)
    g.add_edge(Node.CLOSING, END)
    return g


# Every Pydantic model / enum that ends up inside a checkpoint must be allowlisted
# so the msgpack serializer will reconstruct it on load.
_CHECKPOINT_TYPES: list[type] = [
    obj
    for obj in vars(schemas).values()
    if isinstance(obj, type) and obj.__module__ == schemas.__name__
]


def _serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_TYPES)


@contextmanager
def open_checkpointer(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[SqliteSaver]:
    """Durable SQLite checkpointer. Safe to open from a fresh process to resume a job."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        saver = SqliteSaver(conn, serde=_serde())
        saver.setup()
        yield saver
    finally:
        conn.close()


@asynccontextmanager
async def open_async_checkpointer(db_path: str | Path = DEFAULT_DB_PATH) -> AsyncIterator[AsyncSqliteSaver]:
    """Async variant for the long-running worker; same file and format as ``open_checkpointer``."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path, timeout=30) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        saver = AsyncSqliteSaver(conn, serde=_serde())
        await saver.setup()
        yield saver


def compile_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    return build_graph().compile(checkpointer=checkpointer)


def job_config(job_id: str) -> RunnableConfig:
    """The checkpoint thread is keyed by job_id, so a job resumes exactly where it paused."""
    return {"configurable": {"thread_id": job_id}}
