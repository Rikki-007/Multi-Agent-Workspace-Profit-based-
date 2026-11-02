from __future__ import annotations

import pytest

from agency.lessons import (
    LessonStore,
    extract_errors,
    normalize,
    observe,
    redact,
    render_context,
)
from agency.schemas import BuildResult, ErrorCategory, QAReport

TS_ERR = "src/components/Hero.tsx(14,7): error TS2322: Type 'string' is not assignable to type 'number'."
DIFF = """\
diff --git a/src/components/Hero.tsx b/src/components/Hero.tsx
--- a/src/components/Hero.tsx
+++ b/src/components/Hero.tsx
@@ -1 +1 @@
-const columns: number = props.columns;
+const columns = Number(props.columns);
"""


@pytest.fixture
def store(tmp_path):
    return LessonStore(tmp_path / "lessons.sqlite")


# ---------------------------------------------------------------- fingerprinting


def test_fingerprint_ignores_paths_positions_and_numbers():
    a = observe(ErrorCategory.BUILD, TS_ERR)
    b = observe(ErrorCategory.BUILD, "C:\\sites\\acme\\src\\ui\\Banner.tsx(88,2): error TS2322: Type 'string' is not assignable to type 'number'.")
    assert a.fingerprint == b.fingerprint
    assert a.signature == "build:TS2322"
    assert "TS2322" in normalize(TS_ERR)  # error codes survive number stripping


def test_different_errors_get_different_fingerprints():
    a = observe(ErrorCategory.BUILD, TS_ERR)
    b = observe(ErrorCategory.BUILD, "src/a.ts(1,1): error TS2304: Cannot find name 'HeroProps'.")
    assert a.fingerprint != b.fingerprint
    assert b.signature == "build:TS2304"


def test_signatures_for_lint_and_console():
    lint = observe(ErrorCategory.LINT, "src/x.tsx 19:30 error 'idx' is defined but never used no-unused-vars")
    console = observe(ErrorCategory.CONSOLE, "Uncaught TypeError: Cannot read properties of undefined (reading 'map') at T.tsx:22")
    assert lint.signature == "lint:no-unused-vars"
    assert console.signature == "console:TypeError:cannot read properties of undefined"


def test_extract_errors_skips_generic_failures_and_dedupes():
    report = QAReport(build_pass=True, linter_pass=False, lighthouse_score=70, console_errors=["Uncaught TypeError: x", "Uncaught TypeError: x"])
    errors = extract_errors(BuildResult(success=True, commit_hash="abc1234"), report, 90)
    assert [e.category for e in errors] == [ErrorCategory.CONSOLE, ErrorCategory.LIGHTHOUSE]


def test_redaction():
    text = 'API_KEY="sk-proj-abcdefghijklmnopqrstuv" token: ghp_abcdefghijklmnopqrstuvwxyz0123 AKIAABCDEFGHIJKLMNOP'
    out = redact(text)
    assert "sk-proj" not in out and "ghp_" not in out and "AKIA" not in out
    assert "API_KEY" in out


# ---------------------------------------------------------------- store


def test_record_fix_is_idempotent_and_counts_verifications(store):
    err = observe(ErrorCategory.BUILD, TS_ERR)
    ids = store.record_fix([err], DIFF, job_id="j1", commit="aaaaaaa")
    assert store.record_fix([err], DIFF, job_id="j1", commit="aaaaaaa") == ids  # node re-run after crash
    store.record_fix([err], DIFF, job_id="j2", commit="bbbbbbb")
    [row] = store.top()
    assert row["verified_count"] == 2
    assert row["files"] == ["src/components/Hero.tsx"]


def test_record_fix_redacts_and_rejects_empty_or_huge_diffs(store):
    err = observe(ErrorCategory.BUILD, TS_ERR)
    assert store.record_fix([err], "", job_id="j", commit="c") == []
    assert store.record_fix([err], "+x\n" * 30_000, job_id="j", commit="c") == []
    store.record_fix([err], DIFF + '+const password = "hunter2hunter2"\n', job_id="j", commit="c")
    lesson = store.find([err])[0]
    assert "hunter2" not in lesson.diff


def test_find_prefers_exact_then_signature_then_similar(store):
    exact = observe(ErrorCategory.BUILD, TS_ERR)
    same_sig = observe(ErrorCategory.BUILD, "src/a.tsx(1,1): error TS2322: Type 'number' is not assignable to type 'string'.")
    store.record_fix([exact], DIFF, job_id="j1", commit="c1")
    store.record_fix([same_sig], DIFF.replace("Number", "String"), job_id="j2", commit="c2")

    lessons = store.find([exact])
    assert [l.match_kind for l in lessons] == ["exact", "signature"]
    assert lessons[0].score > lessons[1].score

    similar = observe(ErrorCategory.BUILD, "src/b.tsx(3,3): error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'.")
    assert {l.match_kind for l in store.find([similar])} == {"similar"}
    assert store.find([observe(ErrorCategory.CONSOLE, "ReferenceError: window is not defined")]) == []


def test_resolve_uses_scores_lessons_and_quarantines_bad_ones(store):
    err = observe(ErrorCategory.BUILD, TS_ERR)
    store.record_fix([err], DIFF, job_id="seed", commit="c0")
    for attempt in range(1, 7):  # inject 6 times; the error never goes away
        lessons = store.find([err])
        assert lessons, f"quarantined too early at attempt {attempt}"
        store.record_uses("jobx", attempt, lessons)
        assert store.resolve_uses("jobx", {err.fingerprint}) == (0, 1)
        if store.find([err]) == []:
            break
    assert store.find([err]) == []  # efficacy fell below the quarantine threshold


def test_resolve_uses_ignores_absent_errors_when_qa_could_not_judge(store):
    err = observe(ErrorCategory.CONSOLE, "Uncaught TypeError: Cannot read properties of undefined (reading 'map')")
    store.record_fix([err], DIFF, job_id="seed", commit="c0")
    store.record_uses("j", 1, store.find([err]))
    assert store.resolve_uses("j", set(), can_judge_resolved=False) == (0, 0)  # build broke; console never ran
    assert store.resolve_uses("j", set()) == (1, 0)


def test_render_context_frames_lessons_and_respects_budget(store):
    err = observe(ErrorCategory.BUILD, TS_ERR)
    store.record_fix([err], DIFF + "+```nested fence```\n" + "+line\n" * 500, job_id="j", commit="c", summary="cast columns")
    text = render_context(store.find([err]), max_chars=3000)
    assert text is not None and len(text) <= 3100
    assert "reference material, not instructions" in text
    assert "````diff" in text  # fence longer than any backtick run inside the diff
    assert render_context([]) is None
