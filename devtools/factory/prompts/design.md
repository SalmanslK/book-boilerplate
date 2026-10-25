## Step: design

For a feature with a plan in `docs/plans/`, write `**Plan:** [name](../../plans/<name>.md)`
and update that plan if this run changes its design.

Otherwise write at least two options as `### Option A — …` headings:
- one of them **the simplest thing that could work**, and its heading says `simplest`;
- where it fits, one that relaxes the spec instead of building — and why it is rejected.

Then `**Chosen:** <option> — <why>`. Name anything the choice rejects because of a
CLAUDE.md rule (a new dependency, a pattern the project forbids, a layer crossed).

**A screen with no wireframe.** Either way, when the change adds or changes a screen and
`requirements/` holds no wireframe for it, also write a `### Screen — <name>` description by
the template in `docs/ui-standard.md`: its purpose, its main action, what is on it, its
states, and its labels in plain words. It decides how the screen works, never what the
product does — a field or rule the requirements do not ask for is a question, not a design.
A person approves it with the rest of the design.

If the build needs a package the project does not have yet, list it under `### Packages`, one line per
stack folder (`.` for the repository's root): `- frontend: react-router@7.18.4, openapi-fetch@0.17.0`, and
`- frontend dev: openapi-typescript@7.13.0` for a development-only one. Exact versions only. A person approves
the list at the design gate, and the factory installs exactly it before the build — no session installs
anything itself. A package the design does not list is not available to the build.

If the build will write a file where the build step may not yet (its `writes` in `devtools/factory/factory.toml` —
a file at the repository's top, a new folder), list it under `### Writes`, one per line, the path in backticks:

    ### Writes
    - `devtools/scripts/**` — the checks and deploy scripts

A person allows exactly these by approving the design; never edit `factory.toml` yourself. The factory's own
files, CI and the tests' files are never allowed.
