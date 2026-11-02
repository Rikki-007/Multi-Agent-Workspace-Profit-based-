"""Run Claude Code headlessly (``claude -p``) as the build agent.

Lessons from the optimizer are passed with ``--append-system-prompt-file``: they extend
Claude Code's system prompt for this one run only, nothing is written into the client's
repo, and long context doesn't hit command-line length limits. The task itself goes in
on stdin.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(npm run build:*)", "Bash(npm run lint:*)", "Bash(npx tsc:*)",
]


@dataclass
class ClaudeRunResult:
    ok: bool
    result: str
    session_id: str | None = None
    cost_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""


@dataclass
class ClaudeCodeRunner:
    command_prefix: list[str] = field(default_factory=lambda: ["claude"])
    model: str | None = None
    permission_mode: str = "acceptEdits"
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))
    max_budget_usd: float | None = 2.0
    timeout_seconds: float = 1800

    def command(self, system_append_file: Path | None) -> list[str]:
        cmd = [
            *self.command_prefix, "-p",
            "--output-format", "json",
            "--permission-mode", self.permission_mode,
            "--no-session-persistence",
        ]
        if self.allowed_tools:
            cmd += ["--allowedTools", *self.allowed_tools]
        if self.model:
            cmd += ["--model", self.model]
        if self.max_budget_usd is not None:
            cmd += ["--max-budget-usd", f"{self.max_budget_usd:g}"]
        if system_append_file is not None:
            cmd += ["--append-system-prompt-file", str(system_append_file)]
        return cmd

    def run(self, workspace: Path, prompt: str, system_append: str | None = None) -> ClaudeRunResult:
        with tempfile.TemporaryDirectory(prefix="agency-claude-") as tmp:
            append_file = None
            if system_append:
                append_file = Path(tmp) / "lessons.md"
                append_file.write_text(system_append, encoding="utf-8")
            try:
                proc = subprocess.run(
                    self.command(append_file), cwd=workspace, input=prompt, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return ClaudeRunResult(ok=False, result=f"claude timed out after {self.timeout_seconds:.0f}s")
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            data = {}
        ok = proc.returncode == 0 and not data.get("is_error", False)
        return ClaudeRunResult(
            ok=ok,
            result=str(data.get("result") or proc.stdout[-2000:] or proc.stderr[-2000:]),
            session_id=data.get("session_id"),
            cost_usd=data.get("total_cost_usd"),
            raw=data,
            stderr=proc.stderr[-4000:],
        )


def build_prompt(spec_json: str, lead_json: str, feedback: list[str], attempt: int, max_attempts: int) -> str:
    """Task for the build agent. Past lessons are *not* here; they go in the system prompt.

    The workspace is a copy of a registry template (src/templates/<id>), whose CLAUDE.md and
    RULES.md hold the design rules and .agency/policy.json the tier's dependency policy.
    """
    lines = [
        f"You are the build agent for a small-business website (attempt {attempt} of {max_attempts}).",
        "The current directory is an agency site template. Follow its CLAUDE.md and RULES.md exactly,",
        "and stay inside the dependency policy in .agency/policy.json.",
        "Fill src/content.ts from the business details below (set demo: false) and compose the spec's",
        "sections in src/app/page.tsx.",
        "",
        "Product spec:",
        spec_json,
        "",
        "Business (the only source of facts; omit anything it doesn't provide):",
        lead_json,
        "",
    ]
    if feedback:
        lines += ["The previous attempt failed QA. Fix these problems first:", *[f"- {f}" for f in feedback], ""]
    lines += [
        "Run `npm run lint` and `npm run build` before finishing and fix anything they report.",
        "Do not commit; the harness commits your changes and runs QA.",
        "End with a one-line summary of what you changed.",
    ]
    return "\n".join(lines)
