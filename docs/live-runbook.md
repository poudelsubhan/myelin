# Live operations

Run `make serve`, then open `http://localhost:8100/live`. The live runtime does not
require the local CRM or expense servers. The existing demos remain at `/`.
The hackathon session currently uses port 8104.

## Connect and register

Use **Use current Chrome** to connect an explicitly approved everyday profile, or
**Open login window** for a private dedicated profile. Sign in in the browser;
passwords and verification codes do not belong in task JSON. Chrome on macOS exposes
its endpoint after remote debugging is enabled. Myelin only opens/closes its own
tabs and disconnects without closing the everyday browser.

An optional local connection bridge survives server restarts for at most an hour:

```sh
uv run python -m myelin.live.chrome_bridge --state .local/myelin/chrome-bridge.json --minutes 60
```

Start it only with the profile owner's permission. It binds a random loopback port,
requires a private random path, rejects browser Origin headers, permits one Myelin
client at a time, refuses `Browser.close`, and disconnects automatically. Chrome or
bridge restarts may still require approval. Keep the bridge alive while restarting
the server; do not run a second browser client concurrently with the server.

Register a `SiteProfile` and `WorkflowSpec` through the console or `/live/sites` and
`/live/workflows`. Registered IDs are immutable. New contracts get new IDs. Site
configuration defines allowed origins, expected identity, evidence recipes and
capabilities. Task configuration defines structured inputs, derived values, exact
outcomes and logical effects. `scripts/configure_trello_live.py` builds the initial
Trello configuration from a private observed setup probe and authenticated GET
samples; it contains no account credentials. `definitions/onboarding.py` demonstrates
a different task without an engine task-name branch.

All live contracts, authentication, traces, candidates, manifests and journals stay
under ignored `.local/myelin/`. Its directories use mode 0700; auth/registry files
use 0600. Do not publish that folder or private phase plans.

## Learn, validate and run

1. Load the learning example or enter one structured task. **Learn** records Astra's
   browser actions, declares an effect key before each write, and compiles only after
   independent read-back passes.
2. Supply exactly two distinct new tasks and the candidate hash. **Validate** runs
   them serially with zero model calls. Both must pass before promotion.
3. **Execute** uses the current candidate. **Run batch** accepts one to five fixed
   rows. **Show saved results** displays persisted verified results after restart.
4. Retry with the same task keys and inputs. Completed tasks return their saved URLs.
   Changed inputs under an old task key are rejected. Unknown writes are reconciled
   by real read-back before any more writes. A lookup with no match never proves that
   a dispatched request failed to apply.

CSV intake uses `POST /live/batches/csv` with `workflow_id`, `profile_id`, and `csv`.
Headers must exactly match the task input schema; quoted commas are preserved and
batches remain bounded to five rows. Goal-to-contract discovery, frames, uploads and
popup switching remain explicit unsupported capabilities.

The live HTTP optimizer currently lifts an observed terminal write, preserving UI
steps before it. Every body field comes from source traffic or frozen input/resource
bindings. Credentials are resolved from current same-origin browser cookies, never
replayed from a trace. An HTTP status is not proof of a business outcome.

`POST /live/repairs/{failed_run_id}` supports one localized pre-write locator failure.
It asks Astra for one locator against the real failed observation, preserves all
other steps and outcomes, and produces a new candidate requiring two canaries.
Unknown writes block repair. This is not a universal site repair system.

## Rehearse without creating more records

```sh
uv run python scripts/live_demo.py --assert --workflow sales-follow-up-v3
uv run pytest -q
uv run ruff check .
```

The audit reads saved independently verified evidence and makes no model calls or
remote writes. `--readback` performs fresh read-only verification of the same eight
manifest tasks using the connected browser. Stop the console first if it owns the
single bridge connection. `--execute --manifest .local/myelin/manifest.json` is an
explicit bounded batch run; existing keys return existing results. There is no
production reset/delete cleanup command.

For the stage: open the hosted board, show the five completed follow-ups and their
fields, then show zero-call batch evidence. Explain the initial learning cost, the
observed terminal HTTP write, and the induced locator repair. Show onboarding in
New Leads and the public-page extraction as tested configuration reuse. Do not run
new learning or an unbounded benchmark during the presentation.
