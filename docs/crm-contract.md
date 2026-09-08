# CRM HTTP contract — Phase 0

Run `make crm`; all data is synthetic. Login accepts `email`, `password`, `tenant`
and returns 303 `/customers` with an HttpOnly SameSite=Strict cookie. Login is
CSRF-exempt. A tenant must first be provisioned by the internal test controller.
New-invoice and invoice GETs issue a fresh session-bound hidden `csrf` token.
A subsequent form GET invalidates that session's earlier token. A fresh login
requires fresh tokens and a tenant reset generates fresh customer IDs.

`GET /customers/{id}/invoices/new` displays the form. Its POST action is
`/customers/{id}/invoices`, accepting description, quantity, unit_price_cents,
total_cents, due_date, csrf and operation_id. Successful POST returns 303 and
Location `/invoices/{fresh-id}`. `POST /invoices/{id}/pay` accepts csrf and
operation_id and returns 303 to the invoice. All non-login writes reject invalid
CSRF. Integers and canonical ISO dates are checked before writing.

`X-Myelin-Operation-ID` overrides the hidden operation_id. A write and its stored
result commit in one SQLite transaction. Same identity and same business payload
returns the prior result; conflicting payload returns 409. A new pay identity on
an already paid invoice returns 409. Payload hashes exclude CSRF/session identity.
`GET /api/operations/{id}` uses normal cookie auth and is scoped to that tenant.
The lookup acquires the write lock, so it waits for any current write transaction;
callers must still know their original handler finished before treating absent as
definitive (a request not yet received by the server cannot be detected).

`/__reset`, `/__state` and `/__chaos` require X-Myelin-Demo-Token and are solely
for test administration. Never expose that credential to model/program tools.
Reset accepts an exact seed and EnvironmentSpec. Mutations produce deterministic
revision IDs; unknown or mismatched revision/seed inputs return 422.

Presentation mutations reorder fields or move the payment form without changing
HTTP contracts. The amount rename changes both label and accepted field name to
amount_due_cents and rejects the old key before any write. Consent is a UI-only
modal; HTTP bypass remains valid and must be described as resilience. Phase 6
must exercise an actual UI-dependent program for its consent repair demonstration.

This is a loopback-only owned demo app, not a production authentication service.
