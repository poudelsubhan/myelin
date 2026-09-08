# Myelin

Give Myelin a task. Astra figures it out once; Myelin compiles successful behavior
into a checked program and calls Astra again when the app changes.

Implementation follows the private full-scope phase plan. Current work
is **Phase 0**: executable shared contracts, an owned CRM, locked oracle examples,
and live capability checks. Recording, compilation, repair, the Phase 4 core demo
and Phases 5–7 remain required deliverables. They are not yet implemented.

## Local setup

Requires Python 3.12+, uv, and Chromium through Playwright.

```sh
uv sync --locked
cp .env.example .env
uv run playwright install chromium
make crm
```

Fill OPENAI_API_KEY in `.env` and set MYELIN_LIVE=1 for real model checks.
Leave OPENAI_BASE_URL blank for the official OpenAI API; use a custom value only
if your event explicitly supplies a gateway address. Replace MYELIN_DEMO_TOKEN
with a random local value shared by the CRM and internal test controller.
Never put keys in source or public run artifacts. `.env`, run data and app DBs are
ignored. Ports are 8100 (future orchestrator), 8101 (CRM), 8102 (future expense).

In a second terminal:

```sh
uv run pytest -q
uv run ruff check .
uv run python scripts/hello.py
```

The smoke opens a visible browser, captures the CRM login page, and asks Astra to
read its heading. Artifacts go to ignored `runs/smoke-*/`. `--browser-only` is a
diagnostic option and never counts as the Phase 0 live model gate.

See [build status](docs/build-log/SUMMARY.md), [CRM contract](docs/crm-contract.md),
[oracle specification](docs/oracle-spec.md), and [API evidence](docs/api-capabilities.md).
No core/full acceptance claim is made until its exact integration gate passes.

## License

MIT. The demo operates on owned local applications and synthetic credentials;
passing finite checked cases does not establish arbitrary SaaS compatibility.
