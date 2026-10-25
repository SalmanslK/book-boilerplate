---
name: api-contract-draft
description: >
  Use when a feature needs an HTTP/RPC interface designed before code: drafts the contract
  (routes, request and response shapes, errors, auth) from the plan and requirements, as a
  reviewable document. Writes the contract only; never implements it.
---

# Draft an API contract

Run before the first task that touches an interface, after the plan exists.

1. **Read** the feature's plan (`docs/plans/<feature>.md`), the requirement sections it cites, and the
   architecture guide's API conventions. If the plan or a convention is missing, stop and ask.
2. **List the operations** the requirements imply: one row per operation with its purpose and the
   requirement id it serves. An operation with no requirement is a question for the person, not a row.
3. **For each operation write**: method and route, who may call it, request fields (type, required,
   limits), success response, and every error with its status and the condition that causes it.
4. **Check consistency** with the contracts already in the book: naming, pagination, error shape,
   idempotency, versioning. Differences are listed, not silently chosen.
5. **Write** the draft to the plan's *Contract* section (or `docs/api/<feature>.md` if the architecture
   guide says contracts live there). Mark it `DRAFT` until a person approves.
6. **Raise open questions** in `docs/open-questions.md` and link them from the draft.

Never edit code, tests or requirement snapshots. Output: the path written and the questions raised.
