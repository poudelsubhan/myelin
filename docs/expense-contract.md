# Owned expense application

The expense example is a JSON SPA, served by `make expense` on port 8102. Each
run gets a fresh tenant and bearer token. The browser stores that token under
`myelin_expense_token`; the execution adapter copies a fresh HTTP login token into
that declared key before resuming a UI step. Tokens are excluded from persisted
trace values. This bridge is specific to this app's authentication contract.

`POST /api/login` accepts email/password/tenant. Normal bearer-authenticated routes
list/create expenses, read one expense, submit it, and query an operation ID.
Creating an expense accepts merchant, integer amount_cents, category, canonical
ISO date and receipt_text. Its response provides the created ID and resume URL.
Submission accepts an optional manager_note. Operations are transactionally
idempotent per tenant; conflicting reuse and a second submission identity fail.

The default UI asks for confirmation above 50000 cents. Its default business
policy allows an empty note. The separate manager policy requires exactly
`approved by demo` above 50000 cents and an empty note at or below it. An independent
oracle checks one expense, one related submission, every input value, status,
note and unchanged unrelated rows. The model cannot change these cases.

The private admin token controls only local test setup. Mutations are tenant
scoped: `rename_field:amount` requires cost_cents and rejects the old key before a
write; `add_step:confirm_submit` requires a fresh confirmation token; `throttle:list`
returns 429 once, with Retry-After. Only safe reads retry (at most twice, at most one
second per delay), and every HTTP attempt counts toward work. An unknown write
outcome must be reconciled rather than blindly repeated.
