# Locked CRM oracle specification — crm-policy-v1

Locked before compilation, on 2026-09-08. The eight cases are in
`src/myelin/workflows/crm-cases.json`; the held-out ninth input is in `crm.yaml`.
The independent verifier is implemented in Phase 1 against this specification.

Given a before and after tenant state, require exactly one additional invoice and
one additional payment. Resolve the requested customer by exact name in the seed;
the invoice must refer to that customer's ID. Assert the exact description,
integer quantity, integer unit-price cents, quantity × unit-price total cents,
canonical ISO due date, and paid status. The new payment must refer to the new
invoice and equal its total. Existing customers, invoices and payments must remain
identical. Duplicate invoices, missing payments, wrong amounts, altered unrelated
records or missing records fail independently of model/program postconditions.

For cross-tenant differential comparisons, project the new invoice's customer to
its seeded name and preserve the payment-to-invoice relationship. Generated IDs,
tenant labels and timestamps can differ; business fields cannot. No amount or
input normalization is allowed. UI/ARIA differences are diagnostic only.

Fixtures include five customer names, non-round prices, repeated values,
quotes/Unicode, zero-price line item, leap day and year boundaries. The compiler
receives permitted diagnostics but cannot modify this specification or its suite.
