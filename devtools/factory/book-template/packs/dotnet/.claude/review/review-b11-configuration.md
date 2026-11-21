# B11 · Configuration & Dependencies — checks 156–160

Part of the review checklist; its method is `review-method.md`, in the book plugin (`book:peer-review/references/`).

| # | Check | Apply it here |
|---|---|---|
| 156 | Hardcoded environment-specific configuration | `ASPNETCORE_ENVIRONMENT` values are exact-case (`Development`/`QA`/`UAT`/`Production`); `IsDevelopment()` is not a safety check on its own — a convenience gated only on environment, not also on an explicit flag, is a finding since that gate has failed before |
| 157 | Unsafe/default configuration | Placeholder settings must start with `REPLACE` — anything else passes startup validation silently and could boot with a real-looking but wrong value. A change to `bitbucket-pipelines.yml` states its effect on build minutes in the PR description, and never moves a step off our runner or adds a trigger on every push — cloud steps bill the whole workspace (`check-pipeline.py` holds `runs-on` and `max-time`; the cost statement is the reviewer's) |
| 158 | Configuration not validated at startup/use | Settings bound to typed options with `ValidateOnStart`; a setting read by magic string bypasses this entirely |
| 159 | Unnecessary or inappropriate dependency | Reinventing is a last resort; conversely, a new dependency for something the codebase already does in ten lines is also a finding. Every licence-sensitive dependency needs a row in the architecture guide with its licence and exit |
| 160 | Dependency/version introduces known compatibility or security risk | Package versions live in `Directory.Packages.props`, never an inline `Version=`. CI already runs `dotnet list package --vulnerable --include-transitive` on every PR — a vulnerability CI already caught is surfaced there; only flag this here if the PR is what *introduced* the vulnerable version |
