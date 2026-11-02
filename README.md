# Multi-Agent Website Agency

Two decoupled halves that share no dependencies or build steps:

| Path | What it is | Runtime |
| --- | --- | --- |
| `src/templates/` | Next.js site templates (T1, T2, T3), plus the tier registry | Node, static export |
| `backend/` | The Python agent engine, API, operator dashboard and tests | Python 3.12+ |

The backend reads `src/templates/` to seed job workspaces. Nothing in `src/templates/` imports or
needs the backend. The public portfolio lives in its own repo, Monolyth-Z-Ro.

## Backend

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # .venv/bin/pip on Linux/macOS
python -m pytest
python main.py     # dashboard + API + worker on http://127.0.0.1:8000
```

See [backend/README.md](backend/README.md) for the architecture, API and operations.

## Deploying a template on Vercel

Each template is a standalone Next.js app. Create one Vercel project per template and set **Root
Directory** to that template, such as `src/templates/t1-minimal-editorial`. Vercel detects Next.js
from there and installs that template's own lockfile. `.vercelignore` keeps `backend/` out of any
deployment that uses the repo root.
