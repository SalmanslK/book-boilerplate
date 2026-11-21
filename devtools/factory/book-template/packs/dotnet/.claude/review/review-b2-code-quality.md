# B2 · Code Quality & Maintainability — checks 34–48

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 34 | Duplicated code | Especially business rules re-typed rather than shared |
| 35 | Unnecessary complexity | A simpler shape already exists elsewhere in the codebase for the same problem |
| 36 | Excessively complex method | Deep branching, many responsibilities in one handler |
| 37 | Excessively complex class | Same, at class scope |
| 38 | Excessive nesting | Prefer early returns / guard clauses over pyramids |
| 39 | Poor naming | Names must say what the type is: `CreateWidgetCommand`, `IWidgetReadRepository`, `WidgetDto` |
| 40 | Misleading naming | Name promises one thing, code does another |
| 41 | Unclear control flow | Hard to trace what runs when, especially across async/event boundaries |
| 42 | Magic numbers or strings | CLAUDE.md: no magic strings or numbers. Config read by typed options, never a raw key string |
| 43 | Dead code | Unreachable branches, unused parameters, leftover scaffolding |
| 44 | Unnecessary variables | Intermediate locals that add no clarity |
| 45 | Unnecessary abstraction | An interface, wrapper, or layer that exists but adds no substitutability or testability |
| 46 | Inconsistent coding patterns | LINQ is method syntax only, never query syntax; `Single`/`SingleOrDefault` over `First`/`FirstOrDefault` unless more than one match is legitimate and the query is explicitly ordered |
| 47 | Poor separation of concerns | **`Never Helper`, `Manager`, `Processor`, `Utility`, `DataService`, `CommonService`, no `Common/` folder** — these names are a symptom of unassigned responsibility |
| 48 | Code difficult to safely modify or extend | Would a reasonable next change require touching many unrelated places? |
