"""Where a job's site code lives, and how the optimizer gets diffs out of it."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Never part of a lesson: generated, vendored, secret, or huge files.
DIFF_EXCLUDES = [
    ":(exclude,glob)**/package-lock.json",
    ":(exclude,glob)**/pnpm-lock.yaml",
    ":(exclude,glob)**/yarn.lock",
    ":(exclude,glob)**/.env*",
    ":(exclude,glob)**/*.pem",
    ":(exclude,glob)**/*.key",
    ":(exclude,glob)**/*.min.js",
    ":(exclude,glob)**/*.map",
    ":(exclude,glob)**/node_modules/**",
    ":(exclude,glob)**/dist/**",
    ":(exclude,glob)**/build/**",
    ":(exclude,glob)**/.next/**",
]
BOT_NAME, BOT_EMAIL = "agency-bot", "agency-bot@localhost"
# Build output and installs that may exist in a template checkout but must never be seeded.
SEED_IGNORE = shutil.ignore_patterns("node_modules", ".next", "out", "dist", ".git", "*.tsbuildinfo", "next-env.d.ts")


class Workspace(Protocol):
    path: Path | None

    def head(self) -> str | None: ...
    def commit_all(self, message: str) -> str | None: ...
    def diff(self, base: str | None, head: str | None) -> str: ...


@dataclass
class GitWorkspace:
    """A real git checkout of the client site."""

    path: Path

    def _git(self, *args: str, check: bool = True) -> str:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, env=env, check=check,
        )
        return result.stdout

    def ensure(self) -> GitWorkspace:
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            self._git("init", "-q")
        return self

    def head(self) -> str | None:
        out = self._git("rev-parse", "--verify", "-q", "HEAD", check=False).strip()
        return out or None

    def seed(self, template: Path, *, install: bool = False) -> str | None:
        """Copy the site template in and commit it as the baseline, so build diffs show only the agent's work."""
        shutil.copytree(template, self.path, dirs_exist_ok=True, ignore=SEED_IGNORE)
        if install:  # the build agent runs `npm run lint` / `npm run build`, which need dependencies
            npm = shutil.which("npm") or "npm"
            subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=self.path, check=True, timeout=900)
        return self.commit_all("agency: site template baseline")

    def commit_all(self, message: str) -> str | None:
        self._git("add", "-A")
        if not self._git("status", "--porcelain").strip():
            return self.head()
        identity: list[str] = []
        if not self._git("config", "user.email", check=False).strip():
            identity = ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}"]
        self._git(*identity, "commit", "-q", "-m", message)
        return self.head()

    def diff(self, base: str | None, head: str | None) -> str:
        if not head:
            return ""
        common = ["--no-color", "--no-ext-diff", "-M", "--unified=3"]
        if base:
            return self._git("diff", *common, base, head, "--", ".", *DIFF_EXCLUDES)
        return self._git("show", *common, "--format=", head, "--", ".", *DIFF_EXCLUDES)


_STUB_DIFF = """\
diff --git a/src/components/Hero.tsx b/src/components/Hero.tsx
--- a/src/components/Hero.tsx
+++ b/src/components/Hero.tsx
@@ -11,7 +11,7 @@ export function Hero(props: HeroProps) {
   const { title, subtitle } = props;
-  const columns: number = props.columns;
+  const columns = Number(props.columns ?? 1);
   return (
diff --git a/src/components/Testimonials.tsx b/src/components/Testimonials.tsx
--- a/src/components/Testimonials.tsx
+++ b/src/components/Testimonials.tsx
@@ -19,9 +19,9 @@ export function Testimonials({ items }: Props) {
-  return items.map((t, idx) => (
+  return (items ?? []).map((t) => (
     <blockquote key={t.id}>{t.quote}</blockquote>
   ));
"""


@dataclass
class StubWorkspace:
    """No real repo: commits are fake and every diff is the same canned patch."""

    path: Path | None = None

    def head(self) -> str | None:
        return None

    def commit_all(self, message: str) -> str | None:
        return None

    def diff(self, base: str | None, head: str | None) -> str:
        return _STUB_DIFF if head else ""
