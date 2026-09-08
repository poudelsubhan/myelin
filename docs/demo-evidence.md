# Demonstration evidence

All IDs below identify real saved artifacts. Raw runs are private; selected
sanitized audits are in [Phase 5](evidence/phase5.json), [Phase 6](evidence/phase6.json)
and the final rehearsal export. Model references were performed during live
integration. The final rehearsals reuse them to conserve the participant’s API
budget and do not label them as newly generated responses.

| Criterion | Verified behavior | Actual run or gate |
|---|---|---|
| C1–C2 | Model-led task and observed HTTP compilation | `276280a8-4088-4cd5-8e30-5585eae7f875` |
| C3 | Eight locked cases; wrong/missing/duplicate candidates rejected | `2141231e-1ee3-49b5-93f9-bcac4beeadf3` |
| C4 | New input, zero model calls | `53c5dac8-8ef5-4182-880f-89694b339645` |
| C5 | Presentation resilience and scoped field repair | `cb0b040f-5cbe-4265-948f-3842d079cbfe` |
| C6 | Changed-revision pass and lost-response reconciliation | `5948c2f0-4fe5-4f49-8ebf-e8c643a98545` |
| C7 | Incorrect and costlier optimizations rejected | `demo-7424fc04-3224-40a1-ac56-5d595b946fe4` |
| C8 | Core rehearsal with independent artifact audit | `demo-7424fc04-3224-40a1-ac56-5d595b946fe4` |
| E1 | Expense model compilation and fresh bearer replay | `d935e5ad-76ce-40a2-b3f0-95a5ef107792` |
| E2 | Eight successful paired program/model comparisons | `52d0ac9b-5271-4a76-bc63-a0875816b662` |
| E3 | Native async terminal result plus useful pending analysis | `50cf2b09-9330-477b-beca-474b4148b629` |
| E4 | Real accepted steering becomes exact threshold | `dbceeebf-2466-43d4-8959-7e995b58d53e` |
| E5 | Actual hosted shell, staged patch, verified adoption | `f7f20c09-8715-4a44-b3c2-011085c35277` |

Both extension repair gates have 8/8 program and 8/8 model-reference passes:

- expense: `f6ebd8f0-eaf2-4e34-94bd-893dd7ef71dc`; restored candidate `a5666231ea156c45da4fc2fd62cfcad56d325803535b5414ad528b9164d08fd1`.
- crm: `74ab8db8-9bcf-475d-8e9d-52967e1f6367`; restored candidate `5b4fd2134fdd039f0927d4219b3be4cbd0275a2a5f673fd715512880b36cb9c3`.

The audit checks actual call IDs, trace outcomes, usage response IDs, matching
app/seed/policy configurations, explicit steer provenance, immutable hashes and
structurally preserved repair scope. A failed gate stays failed even if the model
completed the original business task.

The [one-minute video](media/demo.mp4) is labelled recorded evidence. Its
[manifest](media/manifest.json) maps every scene to a source ID and records the
measured 60-second duration and SHA-256. [Development evidence](development-evidence.md)
shows a model-assisted fix and the test that guards it.

## E6 final rehearsals

Both final rehearsals passed all criteria with explicitly reused live model evidence:

- `full-demo-20b4aeea-d930-4b08-b0bf-bfec164d1dfa`
- `full-demo-b5b366f4-3c71-490b-a349-8fd3d344d43f`

Each performed three fresh headless program tasks, all successful with zero model
calls. [Phase 7 export](evidence/phase7.json) records hashes, measurements and
the distinction between prior model proofs and these new deterministic runs.
