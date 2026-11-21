# <Module> — front-end integration guide

**Base:** `/api/v1/<module>` · **Last checked against the code:** YYYY-MM-DD (<commit>)

<!-- Copy this file to `<module>.md` for a **built** module and fill it from the code, never from intent.
     Ownership: check-fe-guides.py matches a changed route in docs/api/api-contracts.md to this guide by its
     title or its Base. Move the Last checked line whenever the guide is checked against a new commit.
     /check-guides reads the endpoint table below: keep its four columns. Delete this comment. -->

What the module does for the front end, in two or three sentences. What every module does the same way
(authentication, errors, paging) is in [api-conventions.md](api-conventions.md); do not repeat it here.

## Endpoints

| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/` | `<module>.view` | 200 list |
| POST | `/` | `<module>.create` | 201, 400 |

A path is under the Base unless it starts with `/` from the root of the API. *anonymous* means no sign-in.

## Requests

One subsection per request body: each field, its type, whether it is required, and its limits.

## Responses

The shape the front end reads, with an example. Every key in an example is a real field.

## Errors

The error codes a route here returns that the front end branches on, and what to show for each.

## Rules

The behaviour the front end must follow, each with where it was decided (a *Resolved* question, the
plan). A question still open says so beside it: *open with the BA — may change*.

## Deviations

Where the code knowingly differs from the FSD, each with its defect or question id. Not checked against
the contracts.
