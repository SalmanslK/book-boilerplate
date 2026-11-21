#!/bin/sh
#
# The pull request pipeline's checks, run on this machine. While there is no CI on your own runner,
# this is the gate before a pull request ([local_ci] required in devtools/factory/factory.toml).
#
#     sh devtools/checks/ci-local.sh           # as much as the change needs
#     sh devtools/checks/ci-local.sh --full    # everything, whatever changed
#
# Stack-free: the build and test commands are `[commands]` in factory.toml (build, test, integration),
# and an empty command is reported N/A, never OK. A pack's own checks run where the pack installed
# them (each guarded by `[ -f ]`). How much runs follows what the branch changed against the base
# (`base_ref`): `book impact --ci` names the tier - checks, build, modules or full.
#
# Stops at the first failure. On a pass over a clean tree it records PASS for the commit, with its tier,
# inside .git, where `book pr open` looks for it, and prints a "Local CI" section to paste into the
# pull request. A pass over uncommitted work, or one during which HEAD moved, records nothing and exits 3.
set -eu

full=""
for argument in "$@"; do
    case "$argument" in
        --full) full=yes ;;
        *) echo "Usage: sh devtools/checks/ci-local.sh [--full]"; exit 2 ;;
    esac
done

root="$(git rev-parse --show-toplevel)"
cd "$root"
work="$(git rev-parse --git-path ci-local)"
marker="$(git rev-parse --git-path factory-local-ci)"
counts="$marker.summary"
summary="$work/summary.txt"
rm -rf "$work.previous"
if [ -d "$work" ]; then mv "$work" "$work.previous"; fi
mkdir -p "$work"
: > "$summary"

started=$(date +%s)
commit="$(git rev-parse HEAD)"
short="${commit%"${commit#????????????}"}"
dirty_at_start="$(git status --porcelain)"
fail() {
    echo ""
    echo "LOCAL CI FAILED at: $1"
    echo "Nothing is recorded. Fix it and run again."
    rm -f "$marker" "$counts"
    exit 1
}
step() {
    echo ""
    echo "=== $1"
}

# The same Python discovery as .githooks/pre-commit.
py=""
for candidate in python3 python "py -3"; do
    if $candidate -c pass >/dev/null 2>&1; then py="$candidate"; break; fi
done
[ -n "$py" ] || fail "no working Python (python3, python or py -3)"
setting() { # setting <table.key>: one value from factory.toml, empty when absent
    $py devtools/book.py config "$1" 2>/dev/null || true
}
base="$(setting base_ref)"
[ -n "$base" ] || base="origin/main"
branch="${base#origin/}"

step "Prerequisites"
echo "$($py --version 2>&1)"

step "Fetch $base, the base every branch check compares with"
fetched=""
for attempt in 1 2 3; do
    if git fetch --quiet origin "+refs/heads/$branch:refs/remotes/$base"; then fetched=yes; break; fi
    echo "fetch attempt $attempt failed; retrying in 10 s"
    sleep 10
done
[ -n "$fetched" ] || fail "git fetch origin $branch - could not check against $base"

step "Tier - how much this change needs"
carried=""
delta=""
previous="$(cat "$marker" 2>/dev/null || true)"
set -f
set -- $previous
if [ -z "$full" ] && [ "${1:-}" = PASS ] && [ -n "${2:-}" ] && [ "$2" != "$commit" ] && [ -s "$counts" ] \
   && git merge-base --is-ancestor "$2" "$commit" 2>/dev/null; then
    delta="$(git diff --name-only "$2" "$commit")"
    if [ -n "$delta" ] && $py devtools/book.py impact --ci $delta | grep -qx 'tier=checks'; then
        carried="$2"
        tier="${4:-full}"
        reason="carried from ${carried%"${carried#????????????}"}: only checks-tier files changed since"
    fi
fi
set +f
if [ -z "$carried" ]; then
    tiering="$($py devtools/book.py impact --ci)" || fail "book impact --ci"
    tier="$(printf '%s\n' "$tiering" | sed -n 's/^tier=//p')"
    filter="$(printf '%s\n' "$tiering" | sed -n 's/^filter=//p')"
    reason="$(printf '%s\n' "$tiering" | sed -n 's/^reason=//p')"
    if [ -n "$full" ]; then tier=full; filter="$(setting commands.integration_filter)"; reason="--full"; fi
fi
case "$tier" in
    checks|build|modules|full) ;;
    *) fail "book impact --ci named no tier (got '$tier')" ;;
esac
echo "Tier: $tier ($reason)"
echo "Tier: $tier ($reason)" >> "$summary"
if [ -n "$carried" ]; then
    grep -vE '^(Tier|Repository checks|Self-tests):' "$counts" \
        | sed -e 's/ (carried from [0-9a-f]*)$//' -e "s/\$/ (carried from ${carried%"${carried#????????????}"})/" >> "$summary"
fi
runs() { # runs <step's lowest tier>: whether this run reaches that step
    [ -z "$carried" ] || return 1
    case "$1:$tier" in
        build:build|build:modules|build:full|integration:modules|integration:full) return 0 ;;
        *) return 1 ;;
    esac
}

step "The book, skills, private data, protected paths, commit messages, task roll-up, rules"
$py devtools/checks/check-book.py || fail "check-book.py"
$py devtools/checks/check-skills.py || fail "check-skills.py"
$py devtools/checks/check-private-data.py --tracked || fail "check-private-data.py --tracked"
$py devtools/checks/check-protected-paths.py --base "$base" || fail "check-protected-paths.py"
$py devtools/checks/check-commit-msg.py --commits "$base..HEAD" || fail "check-commit-msg.py --commits"
$py devtools/checks/check-private-data.py --commits "$base..HEAD" --accepted-from "$base" || fail "check-private-data.py --commits"
$py devtools/checks/gen-task-rollup.py --check || fail "gen-task-rollup.py --check"
$py devtools/checks/check-rules.py --check || fail "check-rules.py --check"
# Every tracked file against [[code_rules]]: the allow-lists counted both ways, the required patterns present.
$py devtools/checks/check-code-rules.py || fail "check-code-rules.py"
# The packs' checks, where installed.
if [ -f devtools/checks/check-appsettings-secrets.sh ]; then
    sh devtools/checks/check-appsettings-secrets.sh --tracked || fail "check-appsettings-secrets.sh --tracked"
fi
if [ -f devtools/checks/gen-api-contracts.py ]; then
    $py devtools/checks/gen-api-contracts.py --check || fail "gen-api-contracts.py --check"
fi
if [ -f devtools/checks/gen-database-erd.py ]; then
    $py devtools/checks/gen-database-erd.py --check || fail "gen-database-erd.py --check"
fi
if [ -f devtools/checks/check-fe-guides.py ]; then
    $py devtools/checks/check-fe-guides.py --base "$base" || fail "check-fe-guides.py"
fi
if [ -f devtools/checks/check-test-setup.py ]; then
    $py devtools/checks/check-test-setup.py || fail "check-test-setup.py"
fi
if [ -f devtools/checks/check-dotnet.py ]; then
    $py devtools/checks/check-dotnet.py || fail "check-dotnet.py"
fi
if [ -f devtools/checks/check-pipeline.py ]; then
    $py devtools/checks/check-pipeline.py || fail "check-pipeline.py"
fi
if [ -f devtools/checks/check-compose.py ]; then
    $py devtools/checks/check-compose.py || fail "check-compose.py"
fi
echo "Repository checks: passed" >> "$summary"
# Which files that steer the AI the branch changed: `book pr open` refuses a description without
# this line when there are any, so an owner's approval is asked for before the merge.
ai_line="$($py devtools/book.py ai-files --list)" || fail "book ai-files --list ($ai_line)"
echo "$ai_line" >> "$summary"

# The Book's own self-tests: devtools/checks/run-self-tests.py over each [commands] self_tests folder,
# side by side. run-self-tests.py checks the count against discovery and fails on "Ran 0 tests" or a skip.
if [ -z "$carried" ] || printf '%s\n' "$delta" | grep -qE '^(devtools|\.githooks)/'; then
    step "Self-tests"
    for folder in devtools/factory/hooks devtools/factory devtools/checks; do
        name="$(printf '%s' "$folder" | tr '/' '-')"
        if $py devtools/checks/run-self-tests.py "$folder" > "$work/self-tests-$name.txt" 2>&1; then
            tail -n 3 "$work/self-tests-$name.txt"
            echo "Self-tests $folder: $(grep -E '^Ran [0-9]+' "$work/self-tests-$name.txt" | tail -n 1)" >> "$summary"
        else
            cat "$work/self-tests-$name.txt"
            fail "self-tests in $folder"
        fi
    done
fi

# The stack's commands. Empty = N/A (the stack has none), said so in the summary, never "passed".
command_step() { # command_step <tier> <label> <table.key> [filter]
    command="$(setting "$3")"
    if ! runs "$1"; then return 0; fi
    step "$2"
    if [ -z "$command" ]; then
        echo "N/A - [$3] is empty in factory.toml"
        echo "$2: N/A (no command)" >> "$summary"
        return 0
    fi
    if [ -n "${4:-}" ]; then
        FILTER="$4" sh -c "$command" || fail "$2 ($command)"
    else
        sh -c "$command" || fail "$2 ($command)"
    fi
    echo "$2: passed ($command)" >> "$summary"
}
command_step build "Build" commands.build
command_step build "Tests" commands.test
command_step integration "Integration tests" commands.integration "${filter:-}"

minutes=$(( ($(date +%s) - started + 59) / 60 ))
when="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""
if [ "$(git rev-parse HEAD)" != "$commit" ]; then
    rm -f "$marker"
    echo "Checks passed, but HEAD moved during the run - NOTHING RECORDED. Run again on the commit you mean."
    exit 3
fi
if [ -n "$dirty_at_start" ] || [ -n "$(git status --porcelain)" ]; then
    rm -f "$marker"
    echo "Checks passed over uncommitted changes - NOTHING RECORDED. Commit, then run again for the PR."
    exit 3
fi
echo "PASS $commit $when $tier" > "$marker"
cp "$summary" "$counts"
echo "LOCAL CI PASSED for $short in ${minutes} min, tier $tier. Paste this into the pull request:"
echo ""
echo "## Local CI"
echo ""
echo "PASS on \`$short\` ($when, ${minutes} min, tier $tier - $reason, \`sh devtools/checks/ci-local.sh\`)"
echo ""
sed 's/^/- /' "$summary"
