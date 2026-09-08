# API capability evidence

Checked official documentation on 2026-09-08:
[model](https://developers.openai.com/api/docs/models/gpt-6-astra) and
[model guidance](https://developers.openai.com/api/docs/guides/latest-model).
The initial probe uses Responses text/image input with low reasoning effort.
Advanced capability support will be established by real phase-specific probes;
model-name recognition is not evidence that a transport/tool works on this key.

## Phase 0 live result

- Run: `smoke-d147ba69-620a-4dc3-9227-2b8d03306b93`
- Timestamp: 1788894703.335645 (UTC Unix seconds, 2026-09-08)
- Model: gpt-6-astra; response status: completed
- Response: `resp_028441fae9764f0c006aa05df1d07c87d08ed7c12daba8e398`
- Chromium visible login-page screenshot: passed
- Model read the heading from the image: passed
- Usage: 1121 input tokens, 1118 cache-write tokens, 0 cached-read tokens,
  6 output tokens, 0 reasoning-output tokens. No dollar claim made by this probe.
- Private raw evidence: `runs/<run-id>/capabilities.json` and `browser.png`.

An earlier attempt failed locally because the SDK consulted an empty optional
OPENAI_BASE_URL environment variable. Explicit official fallback fixed the bug;
`tests/test_config.py` protects the empty/custom gateway cases. The failed attempt
is retained privately, not counted as a successful model response.

Function calling, native async, WebSocket steering, effort updates, hosted shell
and apply_patch have not yet been live-verified. They remain required later gates.

## Core live integrations

Standard Responses function calling is verified by the real browser recorder.
JSON-output compilation is verified by pipeline
c12d306a-c546-47eb-93e2-d11979d93b55: 8 recording responses and one compiler response.
That pipeline's measured model usage totals USD 0.9567075 under the dated Standard
configuration (model usage only). The emitted program passed its independent gate.

The official async, steering, reasoning, shell and apply_patch guides were fetched
for the extension implementations. Reading those guides does not establish live
feature support; native capability evidence remains pending its respective phase.
