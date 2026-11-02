# Agency engine

This is an autonomous website agency. A Python engine (LangGraph, Pydantic v2, SQLite)
discovers leads, gets human compliance sign-off, has Claude Code build a site from a tiered
template in `src/templates/`, QA-checks it, and waits for the client. See `README.md` for architecture
and commands.

```bash
cd backend
python -m pytest                     # engine tests
python main.py                       # dashboard + API + worker on http://127.0.0.1:8000
python -m agency.templates check     # every template obeys its tier's dependency policy

cd ../src/templates/<id> && npm run lint -- --allow-demo && npm run build   # one template
```

## Layout

- `backend/` is the Python side and `src/templates/` the Next.js side. They stay decoupled: templates
  never import or depend on the backend, and the backend only reads `src/templates/` to seed jobs.
- `backend/agency/` contains the engine. `graph.py` holds the state machine, `schemas.py` the contracts,
  `lessons.py` the optimizer memory, `claude_code.py` the build agent, and `workspace.py` the
  per-job git repos.
- `src/templates/registry.json` lists the site templates and each tier's `allowed_deps` and
  `forbidden_deps` (T1 Editorial, T2 Scroll Story, T3 Flagship 3D). `backend/agency/templates.py` enforces
  it, in tests and after every Claude Code build.
- `src/templates/<id>/` holds a template (Next.js static export). The one matching the spec's tier
  and style is copied into `backend/data/workspaces/<job>/` along with `.agency/policy.json`. Its
  `CLAUDE.md` (tier rules) and `RULES.md` (shared rules) are the build agent's rulebook.
- `src/templates/_shared/` holds `RULES.md` and the rules checker. They're vendored into each
  template; edit them there and run `python -m agency.templates sync`.
- `backend/templates/index.html` is the operator dashboard, not a site template.

## Rules for all code here

- **Lean and direct.** Write the smallest change that solves the problem. Add no abstraction
  without a second caller, and no wrappers, registries, config layers or one-use helpers. Code
  should read top to bottom and explain itself. Comments say why.
- **Debug by tracing, not guessing.** Reproduce the failure, read the real error, and follow the
  exact values and state changes step by step to the cause. Fix that cause and prove it with a
  test or a re-run. Don't stack speculative changes or swallow exceptions.
- **Match the surrounding code.** Keep its naming, its comment density and its idioms.

## Engine rules (`backend/agency/`)

- Every state crossing a node boundary is a strict Pydantic model (`extra="forbid"`). Add fields
  with defaults so old checkpoints still load. New model types need no serializer registration:
  `graph._CHECKPOINT_TYPES` picks up everything in `schemas.py`.
- Live resources (stores, workspaces, runners) reach nodes through the LangGraph runtime context
  (`AgencyContext`), never through state.
- Nodes can re-execute after a crash or resume, so side effects must be idempotent. Everything
  before an `interrupt()` re-runs on resume.
- Resume answers are only delivered to the interrupt they target (the `expect` field).
- Run `python -m pytest` before you call engine work done.

## Generated sites

Any site built from this repo follows its template's `CLAUDE.md` and the shared `RULES.md`
(typography, layout, motion, performance, accessibility and content honesty). Its `npm run lint`
and `npm run build` must pass, and its dependencies must satisfy its tier in `registry.json`.
Never loosen a tier to make a build pass: move the site up a tier instead. Inside a job workspace
(`backend/data/workspaces/<job>/`) only the site rules apply; the engine rules above don't.
