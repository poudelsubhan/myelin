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

Function calling, native async and effort updates are now live-verified below.
WebSocket steering, hosted shell and apply_patch remain required later gates.

## Core live integrations

Standard Responses function calling is verified by the real browser recorder.
JSON-output compilation is verified by pipeline
c12d306a-c546-47eb-93e2-d11979d93b55: 8 recording responses and one compiler response.
That pipeline's measured model usage totals USD 0.9567075 under the dated Standard
configuration (model usage only). The emitted program passed its independent gate.

The official async, steering, reasoning, shell and apply_patch guides were fetched
for the extension implementations. Reading those guides does not establish live
feature support; each native capability requires its own successful live probe.

## Native async and reasoning effort

The [async tool guide](https://developers.openai.com/api/docs/guides/async-tool-calling)
and [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning) informed
these adapters. Successful live probe:
`phase5-native-5df7bb29-c615-4314-a5ee-07ac2061a1fd`.

- Native async call: `call_OpNzZHfyHNEGJSDNHv0i7idW`, one original-ID terminal output.
- Gate: `1ee95c6f-8643-45e4-98d7-6f3a7c626008`, 8/8 program oracles and 8/8 fresh
  model references, all business projections matching.
- Independent dependency analysis completed while the gate was still pending:
  `resp_0f585bdc729d5dd4006aa070f3cf1487d08f675b30ce8a0311`.
- That continuation inserted `configuration_update` selecting high effort before a
  user message; request-level medium effort remained unchanged. No automatic
  compaction or truncation was enabled. Effective effort is recorded locally.
- Final receipt: `resp_0f585bdc729d5dd4006aa071c60f9487d0b3e1a8a65eda696e`.

The earlier full gate failed three reference calls with API server errors. It is
retained as a failed gate; the successful probe used a fresh complete suite.
