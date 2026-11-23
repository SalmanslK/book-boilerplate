#!/bin/sh
#
# The nightly full run of a branch: the safety net under the tiered
# local gate, which runs only the module a change maps to. Meant for cron on a Linux host with Docker:
#
#     BOILERPLATE_CI_DAILY_REF=dev sh <tools>/devtools/checks/ci-daily.sh <clone> <results>
#
# <clone> is a clone of this repository kept for this job and this branch alone; the run moves it to
# the tip of origin/<ref> (dev when BOILERPLATE_CI_DAILY_REF is unset), detached. The checks it runs come
# from this script's own folder, mounted read-only, never from the branch under test: a branch
# promoted before a check existed (qa) is still held to it. So run it from a checkout that only the
# job moves, never from <clone>, which the run rewrites.
#
# Everything runs in containers: a throwaway PostgreSQL with the same settings as the tests' own
# (TestDatabase.cs), reached through BOILERPLATE_TEST_DB as CI does, and the build and tests in the
# .NET SDK image with the same skip gates as ci-local.sh. It writes <results>/<date>.log, the run's
# trx files, and one line in <results>/latest: `PASS <sha> <when>` or `FAIL <sha> <when> <step>`,
# with `(filter: …)` after it for a narrowed run. A FAIL is a defects.md row the same day. Runs and logs older than 14 days are deleted.
#
# BOILERPLATE_CI_DAILY_FILTER narrows the integration run, for trying the plumbing by hand; the nightly job
# leaves it unset.
#
# The host's crontab holds this file's path: moving or
# renaming it stops the nightly runs, and only `latest` going stale would show it.
set -eu

ref="${BOILERPLATE_CI_DAILY_REF:-dev}"
keep_days=14

sdk_image="mcr.microsoft.com/dotnet/sdk:10.0"
postgres_image="postgres:18-alpine"

if [ "${1:-}" = "--inside" ]; then
    # Inside the SDK container: /src is the clone, /results the results folder, /tools this folder.
    export DOTNET_CLI_TELEMETRY_OPTOUT=1
    export DOTNET_NOLOGO=1
    cd /src
    failed() { echo "$1" > /results/failed-step; exit 1; }
    apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends postgresql-client python3 \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libdbus-1-3 libexpat1 libgbm1 \
        libglib2.0-0t64 libnspr4 libnss3 libudev1 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 \
        libxfixes3 libxkbcommon0 libxrandr2 fonts-dejavu-core >/dev/null \
        || failed "install psql and python3"
    dotnet restore Boilerplate.slnx || failed "dotnet restore"
    dotnet build Boilerplate.slnx --configuration Release --no-restore || failed "dotnet build"
    dotnet test Boilerplate.slnx --configuration Release --no-build \
        --logger "trx;LogFilePrefix=unit-arch" --results-directory /results/unit-arch \
        --filter "FullyQualifiedName!~Boilerplate.IntegrationTests" || failed "unit and architecture tests"
    sh /tools/check-test-results.sh unit-arch /results/unit-arch || failed "unit and architecture skip gate"
    dotnet test tests/Boilerplate.IntegrationTests/Boilerplate.IntegrationTests.csproj --configuration Release --no-build \
        --logger "trx;LogFileName=/results/integration.trx" \
        --filter "${BOILERPLATE_CI_DAILY_FILTER:-Category!=Smoke&Category!=S3}" || failed "integration tests"
    sh /tools/check-test-results.sh integration /results/integration.trx || failed "integration skip gate"
    # The five slowest classes into the log and slowest.txt. It
    # never fails the run.
    python3 /tools/trx-slowest.py /results/integration.trx | tee /results/slowest.txt
    dotnet list Boilerplate.slnx package --vulnerable --include-transitive --format json > /results/vulnerable.json \
        || failed "dotnet list package --vulnerable"
    python3 /tools/check-vulnerable-packages.py /results/vulnerable.json || failed "vulnerable packages"
    exit 0
fi

clone="${1:?Usage: sh devtools/checks/ci-daily.sh <clone> <results>}"
results_root="${2:?Usage: sh devtools/checks/ci-daily.sh <clone> <results>}"
tools="$(cd "$(dirname "$0")" && pwd)"
# The throwaway database's name: `[ci_daily] test_database` in the settings (the dotnet defaults: app_tests).
test_db="$(cd "$tools/../.." && { python3 devtools/book.py config ci_daily.test_database 2>/dev/null     || python devtools/book.py config ci_daily.test_database 2>/dev/null; })" || test_db=""
test_db="${test_db:-app_tests}"
case "$test_db" in
    *[!a-z0-9_]* | "") echo "ci-daily: [ci_daily] test_database '$test_db' is not a lower-case name" >&2; exit 2 ;;
    *test*) ;;
    *) echo "ci-daily: [ci_daily] test_database '$test_db' does not say it is a test database" >&2; exit 2 ;;
esac
mkdir -p "$results_root"
results_root="$(cd "$results_root" && pwd)"
day="$(date -u +%Y-%m-%d)"
results="$results_root/$day"
log="$results_root/$day.log"
# Until it finishes, latest says so — written before anything else can fail: a run stopped by cron's
# timeout, one that never ends, or one whose setup fails must not leave last night's PASS standing.
echo "RUNNING $ref $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$results_root/latest"

name="boilerplate-ci-daily-$$"
sdk_started=""
# The path git and Docker are given: as is on Linux; D:/… from Git Bash, for trying it by hand
# on Windows (run it there with MSYS_NO_PATHCONV=1, or Git Bash rewrites /src and /results).
host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
cleanup() {
    docker rm -f "$name-sdk" "$name-pg" >/dev/null 2>&1 || true
    docker network rm "$name" >/dev/null 2>&1 || true
    # The SDK container runs as root. Its files go back to the job's user here, not inside it, so a
    # run killed mid-test still leaves nothing root-owned: the next fetch, clean and 14-day prune
    # could not delete it.
    if [ -n "$sdk_started" ]; then
        docker run --rm -v "$(host_path "$clone")":/src -v "$(host_path "$results")":/results "$sdk_image"             chown -R "$(id -u):$(id -g)" /src /results >/dev/null 2>&1 || echo "chown of the run's files failed" >> "$log"
    fi
}
trap cleanup EXIT
finish() { # finish PASS|FAIL [step] — writes latest; the caller sets the exit code
    # A narrowed run says so, so it is never mistaken for a full nightly PASS.
    line="$1 ${sha:-unknown} $(date -u +%Y-%m-%dT%H:%M:%SZ)${2:+ $2}${BOILERPLATE_CI_DAILY_FILTER:+ (filter: $BOILERPLATE_CI_DAILY_FILTER)}"
    echo "$line" > "$results_root/latest"
    echo "$line"
}

{
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ci-daily on $(hostname)"
    sha=""
    trap 'finish FAIL "stopped before it finished (timeout or signal)"; exit 143' INT TERM
    clone="$(cd "$clone" && pwd)" || { finish FAIL "no clone at $clone"; exit 1; }
    { rm -rf "$results" && mkdir -p "$results"; } || { finish FAIL "prepare $results"; exit 1; }
    find "$results_root" -mindepth 1 -maxdepth 1 -name '20*' -mtime +"$keep_days" -exec rm -rf {} +         || echo "pruning runs older than $keep_days days failed; carrying on"
    git -C "$(host_path "$clone")" fetch --quiet origin "+refs/heads/$ref:refs/remotes/origin/$ref" || { finish FAIL "git fetch origin $ref"; exit 1; }
    git -C "$(host_path "$clone")" checkout --quiet --detach --force "origin/$ref" || { finish FAIL "git checkout origin/$ref"; exit 1; }
    git -C "$(host_path "$clone")" clean -qfdx || { finish FAIL "git clean"; exit 1; }
    sha="$(git -C "$(host_path "$clone")" rev-parse HEAD)"
    echo "$ref at $sha; checks from $tools"

    docker network create "$name" >/dev/null || { finish FAIL "docker network create"; exit 1; }
    docker run -d --name "$name-pg" --network "$name" --tmpfs /var/lib/postgresql         -e POSTGRES_DB=$test_db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres         "$postgres_image" -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=300         >/dev/null || { finish FAIL "start PostgreSQL"; exit 1; }
    ready=""
    for attempt in $(seq 1 30); do
        if docker exec "$name-pg" pg_isready -U postgres -d $test_db >/dev/null 2>&1; then ready=yes; break; fi
        sleep 2
    done
    [ -n "$ready" ] || { finish FAIL "PostgreSQL not ready"; exit 1; }

    # Password=postgres is the local convention and not a secret (CLAUDE.md, Security). The files the
    # container writes go back to the job's user in cleanup.
    # In the background and waited for: a shell runs its INT/TERM trap only once a foreground command
    # returns, which a hung test never does, while `wait` is cut short at once. So cron's timeout
    # reaches the trap, and the EXIT trap removes both containers before flock lets the next run in.
    status=0
    sdk_started=yes
    docker run --rm --name "$name-sdk" --network "$name" -w /src         -v "$(host_path "$clone")":/src -v "$(host_path "$results")":/results -v "$(host_path "$tools")":/tools:ro         -v boilerplate-ci-daily-nuget:/root/.nuget/packages         -e BOILERPLATE_TEST_DB="Host=$name-pg;Port=5432;Database=$test_db;Username=postgres;Password=postgres"         -e BOILERPLATE_CI_DAILY_FILTER="${BOILERPLATE_CI_DAILY_FILTER:-}"         "$sdk_image" sh /tools/ci-daily.sh --inside &
    wait $! || status=$?
    if [ "$status" -eq 0 ]; then
        finish PASS
    else
        finish FAIL "$(cat "$results/failed-step" 2>/dev/null || echo "the test container (exit $status)")"
        exit 1
    fi
} > "$log" 2>&1
