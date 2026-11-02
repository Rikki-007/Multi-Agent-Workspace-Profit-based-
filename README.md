# Multi-Agent Website Agency

Autonomous pipeline that finds local businesses, gets human compliance sign-off, builds a
site, QA-checks it, deploys a preview, and waits for the client's reply. Built on
LangGraph with Pydantic v2 contracts and a durable SQLite checkpointer, with a live
dashboard for watching and steering jobs and an optimizer loop that learns from its own
verified fixes. Requires Python 3.12+.

## Layout

| Path | Purpose |
| --- | --- |
| `agency/schemas.py` | Pydantic contracts: `LeadRecord`, `ProductSpec`, `BuildResult`, `QAReport`, `JobState` |
| `agency/graph.py` | State machine, stub nodes, SQLite checkpointer (`thread_id = job_id`) |
| `agency/jobqueue.py` | Durable SQLite work queue (leased, FIFO per job) |
| `agency/worker.py` | 24/7 async worker: drains the queue, streams node events |
| `agency/events.py` | Event log + per-job card summaries that feed the dashboard |
| `agency/service.py` | Submit jobs / human answers (shared by API and CLI) |
| `agency/lessons.py` | Optimizer memory: error fingerprints → verified git-diff patches |
| `agency/workspace.py` | Git repo per job (commits, filtered diffs) and a stub for dev |
| `agency/claude_code.py` | Headless Claude Code build runner (`claude -p`) |
| `tests/` | pytest suite |
| `server.py` | FastAPI: REST + Server-Sent Events |
| `templates/index.html` | Dashboard (Tailwind, vanilla JS) |
| `main.py` | Entry point and CLI |
| `ecosystem.config.js` | PM2 process definition |

## Graph

`lead_discovery → compliance_check → approval_gate ⏸ → strategy → build → qa → preview_deploy → closing ⏸`

- `approval_gate` and `closing` call `interrupt()`; the job sits in `data/checkpoints.sqlite`
  until resumed, from any process.
- `qa` failing routes through `optimizer` back to `build`, up to `max_build_attempts`.
- Live resources (lesson store, workspaces, Claude runner) reach nodes through LangGraph's
  runtime context (`context_schema=AgencyContext`); checkpointed state stays plain data.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # .venv/bin/pip on Linux/macOS
python -m pytest                     # test suite
python main.py                       # dashboard + API + worker → http://127.0.0.1:8000/
python main.py --stub-delay 1 --failure-rate 0.5   # slow, flaky stubs: watch the optimizer loop
```

`python main.py` is shorthand for `python main.py serve`. Use `serve --no-worker` plus a
separate `python main.py run` to split the API and the worker into two processes; the
dashboard works either way because events go through SQLite.

## Optimizer memory (learning loop)

`data/lessons.sqlite` remembers which code changes fixed which errors:

1. **QA fails.** Each specific failure (build error, lint rule, console error, low Lighthouse)
   is fingerprinted. Paths, line numbers, hashes and numbers are stripped so the same mistake
   matches across sites (`build:TS2322`, `lint:no-unused-vars`, …).
2. **Optimizer looks up past fixes:** exact fingerprint, then same signature, then FTS5
   text similarity. Ranking combines match strength, track record (Laplace-smoothed) and
   recency. Lessons that keep failing are quarantined.
3. **Injected into Claude Code.** The top few are rendered as a bounded markdown block and
   passed with `--append-system-prompt-file`. That applies to that one run only; nothing is
   written into the client repo, and they're framed as reference material, not instructions.
4. **QA passes.** The diff from the failing commit to the passing one is stored as a
   *verified* patch for every error it resolved. Lockfiles, `.env*`, keys and build
   output are excluded, and secrets are redacted. Each injected lesson is scored helped/failed.

Tables: `error_fingerprints` (+ `error_fts`), `patches`, `fixes` (fingerprint × patch with
verified/helped/failed counts), `fix_uses`, `verifications`, `sightings`. Writes are
idempotent, so a node re-run after a crash can't double-count.

```bash
python main.py lessons                      # best verified fixes
python main.py lessons --search "TS2322"
curl http://127.0.0.1:8000/api/lessons
```

By default `build` is a stub. To build with Claude Code, use a git repo per job under
`data/workspaces/` (each repo should hold the site template), with a per-run budget cap:

```bash
python main.py serve --claude-code --claude-budget 2 [--claude-model opus]
```

## Site templates and tiers

`src/templates/registry.json` defines three build tiers and the templates for each. Every
template is a Next.js static export (prerendered HTML), seeded into the job workspace that matches
`ProductSpec.tier` and `ProductSpec.style`:

| Tier | Template | Stack | Dependency policy |
| --- | --- | --- | --- |
| T1 Editorial | `t1-minimal-editorial` | Next.js, Tailwind, CSS-only motion (scroll-driven where supported) | no animation or 3D libraries |
| T2 Scroll Story | `t2-glass-scroll-story` | + Lenis, GSAP ScrollTrigger (pinned horizontal story), Framer Motion (UI state) | no Three.js or other 3D |
| T3 Flagship 3D | `t3-liquid-glass-flagship` | + Three.js liquid-glass shader, lazy-loaded behind GPU tiering, static poster fallback | no second 3D engine or scene editor |

**Enforcement.** `allowed_deps` is an allowlist for direct dependencies. `forbidden_deps` is
checked against the whole lockfile, so a heavy package pulled in transitively is caught and
attributed ("three, pulled in by fancy-hero"). `agency/templates.py` runs in the test suite over
every template and after every Claude Code build. A violating build fails with the exact
violation, which the optimizer feeds back to the next attempt.

```bash
python -m agency.templates check          # all templates vs. their tiers
python -m agency.templates sync           # vendor _shared/ (RULES.md, rules checker) into templates
cd src/templates/t2-glass-scroll-story
npm install && npm run dev
npm run lint -- --allow-demo              # templates hold demo content until a job fills them
npm run build
```

Measured on the demo builds (headless Edge, 4× CPU and slow-4G throttling, medians):
T1 LCP ≈1.55 s, T2 ≈1.6 s, T3 ≈1.66 s, with CLS 0 on all three. T3's Three.js chunk
(≈128 KB gz) loads only after idle, and only on capable GPUs.

## Client intake

Clients configure a tier and style on the studio site, which generates a brief code like
`MZ-T2-GLASS-7F3K` (`src/lib/channels.ts` in the portfolio repo). They send it over WhatsApp, Gmail,
email, Instagram or Messenger, or post it to `POST /api/intake/web`. `agency/intake.py` normalises
any of these into one intake record: `intake_id`, `brief_code`, `tier`, `style`, `contact`,
`source`, `status` and `received_at`. It accepts snake or camel case, flat or nested contact
fields, style labels or codes, and a code pasted inside a chat message. A code that disagrees with
an explicit tier or style is rejected rather than guessed.

Intakes wait for a human. `route` creates the pipeline job, which still passes the compliance gate,
and the strategy node builds the spec at the brief's tier and style.

`/api/intake/web` is hardened for the open internet:
- CORS for `AGENCY_INTAKE_ORIGINS` only, on that route only
- a 16 KB body cap and 5 requests per minute per IP
- a honeypot field, and 24 h dedupe of the same brief from the same sender

**Before exposing it, publish only that route.** The operator endpoints (`/api/intakes*`,
`/api/jobs*`, the dashboard) have no authentication and must stay on localhost or behind a reverse
proxy with auth. Set `NEXT_PUBLIC_INTAKE_URL` when building the portfolio to show its web form;
without it the site offers the messaging channels only.

## Dashboard

Columns follow the state machine: **Discovered → Compliance Gate → Building → QA →
Preview Deployed → Closing**, plus a Finished list and a live activity feed.

- Cards at the gate show compliance flags, a notes box and **Approve / Reject**.
- Cards in Building/QA show the attempt counter, current errors and a live log tail of the
  build → QA → optimizer loop.
- Closing cards record the client's answer. Click any card for its full event log and queue history.
- Header pills show the live stream and worker heartbeat.

## API

| Method | Path | Body / notes |
| --- | --- | --- |
| `POST` | `/api/jobs` | `{"job_id"?: str, "lead"?: LeadRecord}` → 201 `{job_id}`; 409 if the id exists |
| `POST` | `/api/leads` | `[LeadRecord, …]` (1–200) → one job per lead |
| `POST` | `/api/jobs/{id}/approval` | `{"approved": bool, "notes"?: str}` → 202; 409 unless waiting at the gate |
| `POST` | `/api/jobs/{id}/client-reply` | `{"accepted": bool, "message"?: str}` → 202; 409 unless waiting at closing |
| `GET` | `/api/jobs` | cards for active + recently finished jobs |
| `GET` | `/api/jobs/{id}` | card, event history, queue items |
| `GET` | `/api/events` | SSE stream (`event: job`, `event: workers`); resumes from `Last-Event-ID` |
| `GET` | `/api/lessons` | optimizer memory stats + verified fixes (`?search=`) |
| `GET` | `/api/health` | worker liveness |
| `POST` | `/api/intake/web` | **public**: a client's project brief → intake record (`pending_review`) |
| `GET` | `/api/intakes` | intake review queue (`?status=pending_review`) |
| `POST` | `/api/intakes/{id}/route` | accept: creates job `job-<intake_id>` with the brief's tier/style |
| `POST` | `/api/intakes/{id}/reject` | decline, with optional `notes` |

Interactive docs at `/docs`. The API has **no authentication**: it binds to `127.0.0.1` by
default. Put it behind auth before exposing it on a network.

## CLI

```bash
python main.py run                      # headless worker
python main.py start --job-id acme      # enqueue a job
python main.py approve acme --notes ok  # or: reject acme
python main.py reply acme --accept      # client accepted the preview
python main.py status acme              # graph state + queue history
python main.py queue                    # recent queue items
python main.py demo                     # whole lifecycle in-process, no worker needed
```

Worker options (for `serve` and `run`): `--concurrency 2`, `--discover-every 3600`
(auto-enqueue a new job hourly), `--poll`/`--max-idle`, `--lease-minutes 10`,
`--stub-delay`, `--failure-rate`, `--claude-code`, `--workspaces`, `--claude-model`,
`--claude-budget`. Global: `--db`, `--queue-db`, `--lessons-db`. Logs go to `logs/agency.log`
(rotating) and stdout.

## Running 24/7

**Reliability model.** Every node is checkpointed, and queue items are leased. If the worker
is killed, its items are reclaimed when the lease expires and resume from the last
checkpoint. Failures retry with exponential backoff (5 attempts). A resume answer is only
delivered to the interrupt it was meant for, so a stale `approve` can't land on `closing`.
SIGINT/SIGTERM stop new claims and give in-flight jobs 30s to finish.

**PM2 (recommended)**

```bash
npm install -g pm2
pm2 start ecosystem.config.js   # runs `main.py serve` with .venv's python, auto-restarts
pm2 logs ai-agency-network
pm2 stop ai-agency-network
pm2 save                        # remember the process list
```

Boot persistence: on Linux/macOS run `pm2 startup`. On Windows, `pm2 startup` isn't
supported; use `pm2-installer` or a Task Scheduler task that runs `pm2 resurrect` at logon.

**Without PM2**

```bash
# Linux / macOS / WSL
nohup .venv/bin/python main.py > logs/runner.log 2>&1 &
pkill -f "main.py"              # graceful (SIGTERM)
```

```powershell
# Windows PowerShell
Start-Process .venv\Scripts\python.exe -ArgumentList "main.py" -WindowStyle Hidden `
  -RedirectStandardOutput logs\runner.out.log -RedirectStandardError logs\runner.err.log
Get-Content logs\agency.log -Wait -Tail 20
```

Without a supervisor nothing restarts the process if it dies; jobs are not lost, they wait
in the queue until the next start.
