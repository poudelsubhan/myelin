# Live evidence — September 8, 2026

The private **Myelin Sales Operations** Trello board contains the learned sales card,
two UI validation cards, and five batch cards with independently verified outcomes.
Every sales card has the requested brief and stable task marker, the Qualified list,
September 9 at 9 AM America/Los_Angeles, reminders disabled, and exactly three
unchecked Qualification items. All are labelled examples, not real customer leads.

[Open the private hosted board](https://trello.com/b/AHOr24kc/myelin-sales-operations).
The board remains private; these links do not grant access.

| Check | Evidence |
|---|---|
| L1: connected account | Everyday signed-in Chrome; account, workspace and board checked before writes |
| L2: model learning | Card A verified; resumed recording `927a12b5-c3ef-41e3-98c3-4a90eb76f395` |
| L3: two fresh UI canaries | Tasks 002 and 003, zero model calls, candidate `7a7912e4c53f70818dcf57360a3fd157dcff95a63f5c9ca203d2f271c7b4d98b` |
| L4: durable effects | An applied checklist item initially had delayed read-back; it was reconciled without duplicate dispatch, then remaining work resumed |
| L5: five-row batch | Tasks 004–008 verified; execution and resume used zero model calls |
| L6: observed HTTP | Final checklist item lifted from successful browser traffic; tasks 009/010 verified with fresh same-session cookie authentication and zero model calls |
| L7: localized repair | One induced pre-write locator mismatch; one Astra repair call; tasks 011/012 passed with zero execution calls |
| L8: second task | Customer onboarding learned and verified in New Leads through configuration only |
| L9: second origin | Python.org page title and the observed first news headline/link verified through the same runtime |
| L10: compatibility | Existing tests/lint and all 54 legacy immutable program hashes checked |

The UI batch needed two fixes: rich-editor drafts must be filled immediately before
Save, and resumed editors must wait until their visible opener is ready. Three rows
completed under the first UI candidate; two finished under the validated hybrid
candidate. The final batch evidence therefore spans two versions, not one flawless
first attempt. Unknown and failed runs remain in private history.

The HTTP candidate is
`b43659424518297acf99dd95ec60e33b445c6ce0ed7ed2e5471bd87a24b8a396`.
Only the terminal checklist write became HTTP; preceding work remained UI. Its
request was matched to a recorded action and fingerprint, acquired the current
same-origin `dsc` cookie, committed its effect identity before dispatch, and passed
independent authenticated GET read-back. No fake idempotency header, demo oracle,
production reference replay or direct public-API credential assumption was used.

[Onboarding result](https://trello.com/c/7ZmXYbzP/12-sam-ortiz-example-evergreen-team-onboarding-demo-001)
uses an Onboarding checklist with Confirm goals, Schedule kickoff, and Share next
steps. Learning took 15 model calls and 90.424 seconds. This proves a second configured
task; it does not establish universal workflow compatibility.

The public check read **Welcome to Python.org** and **The 2026 PSF Board Election is
Open!**, the first news entry observed on the [Python homepage](https://www.python.org/).
It returned the [news URL](https://pyfound.blogspot.com/2026/09/the-2026-psf-board-election-is-open.html)
without following it or writing anything. Its frozen public scope pins the page URL,
not an authenticated user identity. The recorded check used three model calls;
the contract and extraction themselves are deterministic.

All learning, failed learning attempts, onboarding, public extraction and the one
repair call total **38 metered model calls, $13.468795**. Sales execution, canaries,
batches, compilation and business verification used zero model calls. The successful
resumed sales recording alone used 13 calls, $4.451960 and 78.180 seconds; its earlier
attempts are included in the total. Onboarding cost $7.881145. These figures exclude
unknown hosting/service costs and make no throughput multiplier claim.

Raw screenshots, credentials, private contracts, request payloads and detailed run
history are excluded from the repository. Reproduce the saved sales audit with
`uv run python scripts/live_demo.py --assert --workflow sales-follow-up-v3`.

The repaired candidate is
`08a7bad0fba04cf0a1dc5a4020071e9ade628b2775c82ff9de3b5e1bea0d440e`.
Its two canaries passed before promotion. The induced failure occurred while locating
the title field, before a card write; unaffected steps and required outcomes stayed
unchanged. This was induced program drift, not a claimed Trello outage.

The final filmed fresh execution, `40f02417-46a5-4b43-a2b0-3de74c32538b`, completed in
**20.765 seconds**, with **zero model calls** and **16/16 assertions passing**.
[Open its labelled stage example](https://trello.com/c/S2z09g0b/15-stage-example-myelin-demo-stage-demo-001).
Fresh read-back verified all eight original sales tasks before filming. A subsequent
console resubmission returned all five batch URLs without additional cards, UI
execution or model calls. The console recording reported no JavaScript errors.

[Watch the final 60-second product video](media/live-demo.mp4). Its manifest records
sources and narration. The live execution is accelerated and labelled; saved Astra
learning is labelled separately. Narration was generated locally with macOS Samantha,
with no additional model or media API calls. The original demo video remains intact.
