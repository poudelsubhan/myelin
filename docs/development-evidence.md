# Development evidence

Myelin was implemented in a Codex-assisted coding session on September 8, 2026.
This page is a selected, sanitized reconstruction from committed code and actual
verification artifacts. It is not the private planning document or raw transcript.
Runtime model settings and coding-session settings are separate; no unverified
claim is made about the coding session's reasoning effort or experimental flags.

| Commit | Concrete work | Acceptance evidence |
|---|---|---|
| `c4b5ce2` | Scoped repair, effect reconciliation, core checkpoint | Two core rehearsals; C1–C8 audit |
| `431a476` | Expense app and mutations, typed branches, native async validation | Full CRM eight-program/eight-reference gate |
| `6d98fad` | WebSocket rule compilation, hosted staged patches, two repair adapters | Five audited full gates; 86 offline tests; core regression |

The model-assisted design separates the business oracle from the model's browser
and compiler surfaces. This matters because a convincing screen or a successful
HTTP response cannot establish that the correct amount was paid exactly once.
The owned app and its failure mutations were created during this implementation;
the tests exercise normal auth/session behavior and private, isolated fixtures.

## A discovered bug, proposed fix and rejecting test

Expense repair run `c2899fe6-ac9c-4f09-91ed-ba21aa12abc4` submitted the wrong
category and manager note. The independently authored oracle rejected it. The
repair prompt had overwritten the original policy-bearing goal with continuation
instructions. The coding agent changed the prompt to retain both:

```python
prompt = {
    "goal": goal,
    "continuation": remaining_goal,
    "inputs": inputs,
    # workflow, permitted app URL and observed browser state also included
}
```

`tests/test_recorder.py::test_repair_retains_original_policy_and_inputs` requires
the original over-50000 approval rule and Supplies input to survive a repair
checkpoint. Fresh live repair `f335b3f7-26b1-4d73-8e83-6c0415fe325c` then
passed eight program and eight model-reference cases. This is a tested change,
not a claim that a prompt alone guarantees correct execution.

## Another rejected candidate

CRM gate `7ae1627f-b15c-4abb-9774-04048bb3dd17` failed because the executor
rejected an explicit response-bound navigation after HTTP. The fix recognizes
that declared navigation, resolves its URL against the allowed app origin, and
performs it once. `tests/test_branches.py` checks this behavior. A new CRM repair
passed gate `74ab8db8-9bcf-475d-8e9d-52967e1f6367`; the failed gate remains in
history. Unaffected steps and final assertions are structurally protected.

## Runtime Astra contributions

Astra navigates, compiles observed requests, repairs changed operations, works on
trace dependencies while native async validation is pending, accepts live steering,
and analyzes an uploaded sanitized bundle in hosted shell. Its staged patch is
validated before adoption. [Phase 5](evidence/phase5.json) and
[Phase 6](evidence/phase6.json) retain actual response/call IDs and paired case IDs.
The [API evidence](api-capabilities.md) distinguishes live proofs from mocked
transport tests and documents the real effective-effort update.

Selected coding-session update, September 8:

> The field-change repair check caught a real bug: the repair prompt dropped the
> approval-note rule. I’m fixing that before rerunning it; the verifier correctly
> prevented promotion.

The corresponding committed fix and rejecting/passing run IDs are shown above.
This excerpt omits credentials, private planning content and unrelated conversation.
