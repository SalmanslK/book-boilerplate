## Step: integrate

*The referee did this step itself, and its check did not pass — its verdict is at the end. You are here to fix what it found, then check again.*

Run the check. The referee runs the suites `[commands]` in `devtools/factory/factory.toml`
always runs, and the integration tests of the touched modules — or all of them when the
change is cross-cutting — and fails on any failure, any skip, or a suite that ran
nothing.

This step writes nothing. If a test fails because of the change, the fix belongs to the
build step: run `python3 devtools/factory/factory.py reopen build --reason "<what failed>"`,
fix the code, commit, and check again from build. If it fails for a reason the change
did not cause (a test already failing on the base branch, a test database or service
not running), write that to the Escalation section and stop.
