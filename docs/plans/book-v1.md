# Book v1.0.0 — Design

**Status:** `Built` · **Owner:** Boilerplate owner · **FSD:** §1–§6 · **Checked against:** FSD V2 · **FE guide:** — · **Depends on:** nothing

The first release of the Book as a boilerplate: the template, its packs, and this repository's own Book.
Its row is in [`../project.md`](../project.md#part-1--features-and-phasing). Why each part is built the
way it is: [`../architecture.md`](../architecture.md).

## 1. What the FSD says

V2 (`requirements/fsd/Book Boilerplate - V2.md`) §1–§6: a stack-free Book a project copies or installs;
every project fact in `factory.toml`; packs for a stack or a host; `book verify` saying READY, READY WITH
GAPS or NOT READY; no other project's names left; two agents.

## 2. Design

- The Book CLI, guard and checks come from the most complete source Book, with every project value moved
  into `factory.toml` (`[book]`, `[guard]`, `[commands]`, `[checks]`, `base_ref`).
- Everything only a stack or host needs is a pack under `devtools/factory/book-template/packs/`.
- The self-tests run against `fixture_project.py` and an embedded guard fixture, not this repository.

## 9. Tasks

All built and tagged `v1.0.0` (2026-11-18), merged locally with no pull request. Later releases
(v1.1.0 to v1.3.1) are fixes and additions recorded in their commits and tags.

| # | Task | FSD | Role | Status | Jira | PR | Merged |
|---|---|---|---|---|---|---|---|
| 1 | Record the baseline | §1 | BE | Built | — | — | 2026-11-17 |
| 2 | Generic core: the Book CLI, the guard and the checks read `factory.toml` | §2 | BE | Built | — | — | 2026-11-17 |
| 3 | Additions to the checks and the template from a second Book | §2 | BE | Built | — | — | 2026-11-17 |
| 4 | The skills, de-projected, with the stack-free review dimension | §3 | BE | Built | — | — | 2026-11-17 |
| 5 | The template: manifest, skeletons and the five packs; init per stack and pack | §4 | BE | Built | — | — | 2026-11-17 |
| 6 | This repository's own Book: instructions, owner documents, requirements | §5 | BE | Built | — | — | 2026-11-17 |
| 7 | Greenfield and brownfield setup, proven in a scratch repository | §4 | BE | Built | — | — | 2026-11-18 |
| 8 | The two Book agents, checked by `check-skills.py` | §6 | BE | Built | — | — | 2026-11-17 |
| 9 | Acceptance on a fresh copy, and the v1.0.0 tag | §5 | BE | Built | — | — | 2026-11-18 |
