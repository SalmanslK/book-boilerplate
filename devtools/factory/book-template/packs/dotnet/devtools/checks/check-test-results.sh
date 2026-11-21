#!/bin/sh
#
# The skip gates: a test run that skipped, ran nothing, or left no results is a failure, not a pass.
# Written once, called by bitbucket-pipelines.yml and by devtools/checks/ci-local.sh, so CI and a
# local run hold a change to the same gate.
#
#   sh devtools/checks/check-test-results.sh unit-arch <results-dir>
#       Every test project but the integration one has exactly one unit-arch*.trx in <results-dir>
#       that executed tests and skipped none. `dotnet test` with LogFilePrefix=unit-arch
#       writes one trx per project, so each is found by the path of the assembly it ran.
#
#   sh devtools/checks/check-test-results.sh integration <trx>
#       The integration run's trx exists, executed tests, and skipped none.
#
# Three ways a run can lie, and all three are checked: a missing trx, a run that executed nothing (a
# filter matching no tests exits 0 with "No test matches" and an empty Counters block), and a skipped
# test (no database, or no psql). A skip is counted from the results, never from Counters: VSTest leaves
# notExecuted at 0 for an xUnit skip, which only shows as a result with outcome="NotExecuted". The
# Counters match is case-sensitive, so it reads `executed`, never `notExecuted`.
set -eu

mode="${1:-}"
target="${2:-}"
[ -n "$mode" ] && [ -n "$target" ] || { echo "Usage: check-test-results.sh unit-arch <results-dir> | integration <trx>"; exit 2; }

case "$mode" in
  unit-arch)
    for project in $(ls -d tests/*/ | xargs -n1 basename | grep -vx 'Boilerplate.IntegrationTests'); do
      MATCHES=$(grep -lE "[/\\]$project\.dll\"" "$target"/unit-arch*.trx 2>/dev/null || true)
      COUNT=$(printf '%s' "$MATCHES" | grep -c . || true)
      if [ "$COUNT" -ne 1 ]; then
        echo "Expected one trx for $project, found $COUNT - the project did not run, or its results are ambiguous."
        ls "$target" 2>/dev/null || true
        exit 1
      fi
      RUN=$(grep -o '<Counters[^>]*executed="[0-9]*"' "$MATCHES" | grep -o 'executed="[0-9]*"' | grep -o '[0-9]*' || true)
      SKIPPED=$(grep -o '<UnitTestResult [^>]*outcome="NotExecuted"' "$MATCHES" | wc -l)
      if [ -z "$RUN" ] || [ "$RUN" -eq 0 ]; then echo "No tests executed in $project - the filter matched nothing."; exit 1; fi
      if [ "$SKIPPED" -ne 0 ]; then echo "$SKIPPED test(s) skipped in $project - nothing in it may skip."; exit 1; fi
      echo "$project: $RUN executed, 0 skipped."
    done
    ;;
  integration)
    if [ ! -f "$target" ]; then
      echo "No trx at $target - the run did not finish."
      exit 1
    fi
    EXECUTED=$(grep -o '<Counters[^>]*executed="[0-9]*"' "$target" | grep -o 'executed="[0-9]*"' | grep -o '[0-9]*' || true)
    if [ -z "$EXECUTED" ] || [ "$EXECUTED" -eq 0 ]; then echo "No integration tests executed - the filter matched nothing, or the run never started."; exit 1; fi
    if grep -q 'outcome="NotExecuted"' "$target"; then echo "Integration tests were skipped - PostgreSQL was not reachable, or psql is missing."; exit 1; fi
    echo "Boilerplate.IntegrationTests: $EXECUTED executed, 0 skipped."
    ;;
  *)
    echo "Unknown mode '$mode': unit-arch or integration."
    exit 2
    ;;
esac
