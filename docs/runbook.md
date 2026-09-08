# Myelin demonstration runbook

## Start and inspect

```sh
uv sync --locked
cp .env.example .env
uv run playwright install chromium
```

Set the event API key and MYELIN_LIVE=1 only when running model-powered tasks.
Leave OPENAI_BASE_URL blank for the official API. MYELIN_HEADLESS=1 keeps isolated
validation browsers out of your desktop; their screenshots still reach the console.
Start `make crm`, `make expense` and `make serve` in separate terminals. Open
http://localhost:8100; 8101 and 8102 are the owned target apps.

The console can learn a task or execute a promoted program. Recording/compilation
and full model comparisons consume API budget; program execution does not.
Completed run history and event replay survive server restarts. The CRM's default
console action executes the current program to avoid accidental new model work.

## Reproduce the engineering gates

```sh
uv run pytest -q
uv run ruff check .
uv run python scripts/demo.py --assert --core
uv run python scripts/demo.py --assert --full
```

Core mode records, compiles, validates, repairs and rejects bad/costlier candidates.
Full mode runs core and both architectures, native async, steering, hosted patches,
and full eight-reference gates. A fresh full run is deliberately model intensive;
start it only when you intend that API expenditure. `--stage` pauses engineering
beats, not a promise that a complete validation run fits a short presentation.
Each run resets only its synthetic tenant. Demo pointer resets preserve immutable
programs, historical ledger decisions and earlier artifacts.

The final budget-conserving rehearsals instead use:

```sh
uv run python scripts/demo.py --assert --full --reuse-evidence runs/full-source.json
```

This audits prior real C1–C8/E1–E5 artifacts, then executes three new tasks with
zero model calls: repaired CRM field contract, retained CRM UI consent path and
repaired expense rule. It does **not** rerun model-reference suites or imply their
responses are new. The source bundle is private; its published audit/IDs and video
are inspectable in the repository. Fresh full mode creates its own source bundle
inside the full-demo evidence result and has no dependency on historical run IDs.

## Short presentation

The one-minute video uses edited, clearly labelled evidence. For a finalist slot,
confirm its length with the event organizer; three minutes plus Q&A was an earlier
unverified assumption. A concise live sequence is: explain the task and saved
program, execute a new input, inspect the real repair/promotion evidence, then show
the expense branch and equality coverage. Keep the full gate artifacts available
for inspection instead of rerunning dozens of model calls on stage.

If an API fails, play [the recorded fallback](media/demo.mp4) with its replay label.
Never describe archived model responses as live. Work units are HTTP requests plus
ten times UI actions; they are not dollars. Model cost uses dated response-level
pricing; unknown cost remains unknown. Hosted/container charges are not included
in model-token totals.

## Recovery and limits

Reconcile an unknown write via its normal authenticated operation lookup. Applied
writes resume after the completed effect; a definite pre-write rejection can be
repaired. Unknown or in-flight effects stop explicitly. WebSocket recovery also
requires a confirmed checkpoint and never blindly repeats accepted steering.

The two supported workflows are owned local applications. A new online service
needs authentication/session integration, explicit workflow scope and independent
outcome checks. This implementation does not establish arbitrary SaaS compatibility.
No production data, enrollment or event submission is performed automatically.
