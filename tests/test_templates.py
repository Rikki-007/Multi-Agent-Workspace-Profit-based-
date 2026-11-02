"""Template registry and tier dependency policy."""

from __future__ import annotations

import json
import shutil
import sys
import textwrap

import pytest

from agency import graph as G
from agency import templates as T
from agency.claude_code import ClaudeCodeRunner
from agency.lessons import LessonStore
from agency.schemas import ColorPalette, JobState, LayoutType, ProductSpec, SiteStyle, TemplateTier

SPEC = dict(layout=LayoutType.SINGLE_PAGE, color_palette=ColorPalette.BOLD_DARK, sections=["hero", "contact"], acceptance_criteria=["build_passes"])


def _site(tmp_path, deps=None, dev=None, lock_packages=None):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": deps or {}, "devDependencies": dev or {}}))
    if lock_packages is not None:
        (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {"": {}, **lock_packages}}))
    return tmp_path


# ---------------------------------------------------------------- the real templates


@pytest.mark.parametrize("template", T.templates(), ids=lambda t: t.id)
def test_every_registered_template_obeys_its_tier(template):
    assert template.path.is_dir(), f"{template.id} is registered but missing"
    assert T.validate_package(template.path, template.tier) == []


def test_every_tier_has_a_template_and_styles_resolve():
    assert {t.tier for t in T.templates()} == {"T1", "T2", "T3"}
    assert T.pick_template("T1", "minimalism").id == "t1-minimal-editorial"
    assert T.pick_template("T2", "glassmorphism").id == "t2-glass-scroll-story"
    assert T.pick_template("T3", "liquid_glass").id == "t3-liquid-glass-flagship"
    assert T.pick_template("T1", "neo_brutalism").id == "t1-minimal-editorial"  # style without its own template
    assert T.pick_template("T2", "minimalism").tier == "T2"  # tier wins over style


def test_registry_styles_match_the_schema_enum():
    registered = {s for t in T.templates() for s in t.styles}
    assert registered <= {s.value for s in SiteStyle}


def test_vendored_shared_files_are_in_sync():
    """Templates carry copies of _shared/ (they must stand alone); run `python -m agency.templates sync` after edits."""
    for template in T.templates():
        for rel in T.shared_files():
            assert (template.path / rel).read_bytes() == (T.SHARED_DIR / rel).read_bytes(), f"{template.id}/{rel} is stale"


def test_lower_tiers_forbid_three_and_t1_forbids_motion_libraries():
    for tier in ("T1", "T2"):
        assert "three" in T.policy(tier)["forbidden_deps"]
    assert {"gsap", "lenis", "framer-motion"} <= set(T.policy("T1")["forbidden_deps"])
    assert "three" in T.policy("T3")["allowed_deps"]


# ---------------------------------------------------------------- the validator


def test_direct_forbidden_and_unlisted_dependencies_are_reported(tmp_path):
    site = _site(tmp_path, deps={"next": "16", "react": "19", "three": "0.186", "lodash": "4"}, dev={"typescript": "5", "@types/three": "0"})
    problems = T.validate_package(site, "T1")
    assert any('forbids "three"' in p for p in problems)
    assert any('"lodash" is not in the T1 allowed_deps' in p for p in problems)
    assert not any("typescript" in p or "@types/three" in p for p in problems)  # toolchain is always allowed


def test_scoped_globs_match(tmp_path):
    problems = T.validate_package(_site(tmp_path, deps={"@react-three/fiber": "9"}), "T2")
    assert problems == ['T2 forbids "@react-three/fiber" (matches @react-three/*); remove it from package.json']


def test_transitive_pull_is_caught_and_attributed(tmp_path):
    lock = {
        "node_modules/next": {"dependencies": {"react": "19"}},
        "node_modules/fancy-hero": {"dependencies": {"three": "^0.180"}},
        "node_modules/three": {},
    }
    site = _site(tmp_path, deps={"next": "16"}, lock_packages=lock)
    assert T.validate_package(site, "T1") == [
        'T1 forbids "three" (matches three), pulled in transitively by fancy-hero'
    ]


def test_t3_may_ship_three(tmp_path):
    site = _site(tmp_path, deps={"next": "16", "three": "0.186"}, lock_packages={"node_modules/three": {}})
    assert T.validate_package(site, "T3") == []


# ---------------------------------------------------------------- the pipeline


def test_seeding_follows_the_spec_tier(tmp_path):
    factory = G.GitWorkspaces(tmp_path / "ws")
    ws = factory("flagship", ProductSpec(**SPEC, tier=TemplateTier.T3, style=SiteStyle.LIQUID_GLASS))
    assert "three" in json.loads((ws.path / "package.json").read_text())["dependencies"]
    assert json.loads((ws.path / ".agency" / "policy.json").read_text())["tier"] == "T3"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_build_fails_when_the_agent_adds_a_forbidden_dependency(tmp_path):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(textwrap.dedent(
        """
        import json, pathlib, sys
        sys.stdin.read()
        pkg = json.loads(pathlib.Path("package.json").read_text())
        pkg["dependencies"]["three"] = "0.186.1"          # the agent reaches for 3D on a T1 site
        pathlib.Path("package.json").write_text(json.dumps(pkg, indent=2))
        print(json.dumps({"is_error": False, "result": "Added a 3D hero."}))
        """
    ))
    ctx = G.AgencyContext(
        lessons=LessonStore(tmp_path / "lessons.sqlite"),
        workspaces=G.GitWorkspaces(tmp_path / "ws"),
        claude=ClaudeCodeRunner(command_prefix=[sys.executable, str(fake)]),
    )

    class _Runtime:
        context = ctx

    state = JobState(job_id="t1job", spec=ProductSpec(**SPEC, tier=TemplateTier.T1))
    result = G.build(state, _Runtime())["build_result"]
    assert not result.success
    assert result.commit_hash  # the offending change is committed, so the optimizer can diff it
    assert result.error_logs == ['dependency policy: T1 forbids "three" (matches three); remove it from package.json']
