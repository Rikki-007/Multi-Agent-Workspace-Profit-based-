"""Site template registry and the per-tier dependency policy.

`src/templates/registry.json` declares the templates and, per tier, which packages a site may
depend on (`allowed_deps`) and which must never appear anywhere in its install
(`forbidden_deps`, checked against the lockfile so transitive pulls are caught). This module is
the enforcer: the test suite runs it over every template, and the build node runs it on the job
workspace after every Claude Code build, outside the agent's reach.

    python -m agency.templates check [DIR ...]   # validate templates (default: all registered)
    python -m agency.templates sync              # copy _shared/ into every template
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "templates"  # repo root, outside backend/
REGISTRY_PATH = TEMPLATES_DIR / "registry.json"
SHARED_DIR = TEMPLATES_DIR / "_shared"
POLICY_FILE = Path(".agency") / "policy.json"  # written into each job workspace at seed time


@dataclass(frozen=True)
class Template:
    id: str
    tier: str
    styles: tuple[str, ...]
    description: str

    @property
    def path(self) -> Path:
        return TEMPLATES_DIR / self.id


@cache
def registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def templates() -> list[Template]:
    return [
        Template(t["id"], t["tier"], tuple(t["styles"]), t["description"]) for t in registry()["templates"]
    ]


def pick_template(tier: str, style: str | None = None) -> Template:
    """The template for a tier, preferring one built for the style."""
    in_tier = [t for t in templates() if t.tier == tier]
    if not in_tier:
        raise KeyError(f"no template registered for tier {tier}")
    return next((t for t in in_tier if style in t.styles), in_tier[0])


def policy(tier: str) -> dict[str, Any]:
    rules = registry()["tiers"][tier]
    return {
        "tier": tier,
        "name": rules["name"],
        "summary": rules["summary"],
        "allowed_deps": rules["allowed_deps"] + registry()["toolchain_deps"],
        "forbidden_deps": rules["forbidden_deps"],
    }


def _matches(name: str, patterns: list[str]) -> str | None:
    return next((p for p in patterns if fnmatchcase(name, p)), None)


def validate_package(site_dir: Path, tier: str) -> list[str]:
    """Policy violations for the site in `site_dir` under `tier`. Empty means compliant."""
    rules = policy(tier)
    problems: list[str] = []

    manifest = json.loads((site_dir / "package.json").read_text(encoding="utf-8"))
    direct = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
    for name in sorted(direct):
        if pattern := _matches(name, rules["forbidden_deps"]):
            problems.append(f'{tier} forbids "{name}" (matches {pattern}); remove it from package.json')
        elif not _matches(name, rules["allowed_deps"]):
            problems.append(f'"{name}" is not in the {tier} allowed_deps; remove it or move the site up a tier')

    lock_path = site_dir / "package-lock.json"
    if lock_path.exists():
        packages: dict[str, dict[str, Any]] = json.loads(lock_path.read_text(encoding="utf-8")).get("packages", {})
        for key in sorted(packages):
            if not key:
                continue  # the root project itself
            name = key.rsplit("node_modules/", 1)[-1]
            if name in direct:
                continue  # already reported above
            if pattern := _matches(name, rules["forbidden_deps"]):
                parents = sorted(
                    k.rsplit("node_modules/", 1)[-1]
                    for k, meta in packages.items()
                    if k and name in {**meta.get("dependencies", {}), **meta.get("peerDependencies", {})}
                )
                via = ", ".join(parents) or "unknown"
                problems.append(f'{tier} forbids "{name}" (matches {pattern}), pulled in transitively by {via}')
    return problems


def write_policy(site_dir: Path, tier: str) -> None:
    """Materialise the tier's rules inside a job workspace so the build agent can read them."""
    path = site_dir / POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy(tier), indent=2) + "\n", encoding="utf-8")


def shared_files() -> list[Path]:
    return sorted(p.relative_to(SHARED_DIR) for p in SHARED_DIR.rglob("*") if p.is_file())


def sync_shared() -> list[str]:
    """Copy _shared/ into every template (they're vendored so each template stands alone)."""
    changed = []
    for template in templates():
        for rel in shared_files():
            src, dst = SHARED_DIR / rel, template.path / rel
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                changed.append(f"{template.id}/{rel.as_posix()}")
    return changed


def _main(argv: list[str]) -> int:
    if argv[:1] == ["sync"]:
        for path in sync_shared() or ["(already in sync)"]:
            print(path)
        return 0
    if argv[:1] == ["check"]:
        by_dir = {t.path.resolve(): t for t in templates()}
        targets = [Path(a).resolve() for a in argv[1:]] or list(by_dir)
        failed = False
        for site in targets:
            template = by_dir.get(site)
            if template is None:
                print(f"{site}: not a registered template")
                failed = True
                continue
            problems = validate_package(site, template.tier)
            print(f"{template.id} ({template.tier}): {'ok' if not problems else f'{len(problems)} problem(s)'}")
            for problem in problems:
                print(f"  - {problem}")
            failed |= bool(problems)
        return 1 if failed else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
