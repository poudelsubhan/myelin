# Myelin demonstration runbook

## Start

1. `uv sync --locked` and `uv run playwright install chromium`.
2. Fill the ignored `.env`; use the official API default unless given an event gateway.
3. `make crm` in one terminal and `make serve` in another.
4. Open http://localhost:8100. All displayed events come from the orchestrator.
5. `uv run python scripts/demo.py --assert --core` runs the complete core sequence.
   `--stage` pauses between beats. It is separate from full-scope validation.

The demo controller resets the current synthetic demonstration pointer while
preserving candidate files, ledger history and prior runs. Each case receives an
isolated tenant, fresh authentication, exact revision/mutation profile and seed.
Do not remove a promoted candidate to make a new candidate look correct.

## Story

Give Myelin a task. Astra operates the browser once, then authors a program from
its successful trace. Fresh execution uses typed inputs and fresh session values.
Show the eight independent business checks and zero model comparisons in core
mode. Show a new input using zero model calls. Move fields/buttons: HTTP survives.
Rename the request field: the write fails before applying. Resume from the
checkpoint, let Astra complete the task, and validate a local patch before
restoration promotion. Show the new input working without AI and rejection of
incorrect or unnecessarily expensive candidates.

Open program steps to inspect their source action and screenshot. Work units are
HTTP requests + 10 × UI actions, not dollars. Dollars use recorded model usage and
the dated Standard pricing configuration; unknown pricing stays unknown. Recording
business-result timing and complete pipeline timing are separate measurements.

## Recovery

On an unknown write effect, reconcile via the authenticated operation lookup.
An absent operation alone does not prove an in-transit handler cannot still write.
The system stops rather than retrying such an unresolved effect. Applied writes
resume at the suffix; pre-write contract rejection can resume visual repair.

API outages: use an explicitly labelled recorded fallback. Never describe a replay
as a live model response. Core mode remains independently runnable as later phases
add expense, reference runs, async tools, steering and hosted compiler tooling.

## Scope

These are two owned local app workflows, not a demonstrated connector to arbitrary
CRMs. No real customer data or production authentication is involved. The oracle
is deliberately outside the model/program tool surface and validates a finite,
locked suite. Native async, steering and hosted-tool evidence require real live
checks in later phases, not mocks. No submission or enrollment is automated.
