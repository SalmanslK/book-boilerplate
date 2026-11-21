#!/bin/sh
#
# Refuses a deploy whose Compose files do not supply every setting the target
# environment's appsettings file marks as REPLACE.
#
# WHY THIS EXISTS
#
# QA went down on 2026-09-07 in a restart loop:
#
#     OptionsValidationException: Email:FrontendBaseUrl is not configured.
#
# The setting had been added to appsettings.QA.json as REPLACE_AT_DEPLOY_TIME —
# correctly — but nothing was ever added to compose.qa.yaml to supply it. Every
# test passed and DEV ran the same commit happily, because the base Compose file
# sets ASPNETCORE_ENVIRONMENT=Development and the email validator skips itself
# there. So the failure existed only in an environment no test and no local run
# ever starts, and the first thing to discover it was the deploy that replaced a
# working container.
#
# A REPLACE placeholder is a promise that something else fills the value in. This
# is what checks the promise was kept, before the old container is thrown away.
#
# WHAT IT COMPARES
#
#   appsettings.<Env>.json          every key whose value starts with REPLACE
#   docker compose ... config       the api service's rendered environment
#
# `config` merges the overlays and resolves .env substitutions without starting
# anything, so it answers the question the files themselves cannot: the QA port
# collision this same rendering caught in CI read correctly in both files too.
#
# Settings keys nest with a colon and reach the container as a double
# underscore, so Email:FrontendBaseUrl is looked for as Email__FrontendBaseUrl.
#
# VALUES ARE NEVER PRINTED. The rendered environment holds the signing key, the
# connection string and the SMTP password, and this runs in CI logs and over SSH
# during a deploy. Only key names are reported — a key name is not a secret, and
# the name is the whole of what a person needs to fix it.
#
# Usage:
#   check-required-settings.sh <Env> <compose-file> [<compose-file>...]
#
#   check-required-settings.sh QA compose.yaml compose.qa.yaml
#   check-required-settings.sh QA -f compose.yaml -f compose.qa.yaml

set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <Env> <compose-file> [<compose-file>...]" >&2
    exit 2
fi

env_name="$1"
shift

settings="src/Boilerplate.API/appsettings.${env_name}.json"

if [ ! -f "$settings" ]; then
    echo "No $settings — nothing declares a required setting for $env_name." >&2
    exit 2
fi

command -v jq >/dev/null 2>&1 || { echo "jq is required." >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "docker is required." >&2; exit 2; }

# Both `compose.yaml compose.qa.yaml` and `-f compose.yaml -f compose.qa.yaml`
# are accepted. The deploy skill and the Compose commands beside it carry the
# flags already, and a caller who copies that form should not get a confusing
# "no such file" out of it.
compose_args=""
for arg in "$@"; do
    case "$arg" in
        -f) continue ;;
        *)  compose_args="$compose_args -f $arg" ;;
    esac
done

# Every REPLACE placeholder, as the environment variable name it becomes.
# `paths(scalars)` walks the whole tree, so a setting nested three deep is found
# without this script knowing the shape of the file.
required=$(jq -r '
    [ paths(scalars) as $p
      | select(getpath($p) | if type == "string" then ascii_upcase | startswith("REPLACE") else false end)
      | $p | join("__") ]
    | sort | .[]' "$settings" | tr -d '\r')

if [ -z "$required" ]; then
    echo "No REPLACE placeholders in $settings — nothing to check."
    exit 0
fi

# The rendered api environment, as `NAME<tab>ok|placeholder`. Compose normalises
# `environment:` to an object, but a list of NAME=VALUE strings is still valid
# input elsewhere, so both shapes are handled rather than assumed.
#
# The value never leaves jq. All that comes out is whether it is still a REPLACE
# placeholder, which is the only thing this script asks about it.
# Rendered first, into a variable, rather than piped straight into jq. A shell
# pipeline reports the LAST command's status, so a `compose config` that failed on
# a required variable would exit 0 through jq and leave an empty result here —
# which this script would then report as every setting missing, burying the one
# line that says which variable is unset.
rendered=$(docker compose $compose_args config --format json) || {
    echo "" >&2
    echo "docker compose config failed. The message above names the .env variable" >&2
    echo "that is missing; nothing was checked." >&2
    exit 1
}

supplied=$(printf '%s' "$rendered" | jq -r '
    (.services.api.environment // {}) as $e
    | ( if ($e | type) == "object"
        then ($e | to_entries)
        else ($e | map({ key: (. | split("=")[0]), value: (. | split("=")[1:] | join("=")) }))
        end )
    | .[]
    | select(.value != null)
    | "\(.key)\t\(if (.value | tostring | ascii_upcase | startswith("REPLACE")) then "placeholder" else "ok" end)"' | tr -d '\r')
# tr: Windows' native jq.exe ends every line with CRLF, and a key carrying a trailing CR never matches,
# so on a developer's machine every setting read as missing. Linux jq writes LF; this changes nothing there.

missing=""
still_placeholder=""

for key in $required; do
    state=$(printf '%s\n' "$supplied" | awk -F'\t' -v k="$key" '$1 == k { print $2; exit }')

    case "$state" in
        ok)          ;;
        placeholder) still_placeholder="$still_placeholder $key" ;;
        *)           missing="$missing $key" ;;
    esac
done

if [ -z "$missing" ] && [ -z "$still_placeholder" ]; then
    echo "$env_name: every REPLACE setting is supplied by the Compose files."
    exit 0
fi

echo "$env_name: the API will refuse to start." >&2

for key in $missing; do
    echo "  MISSING      $key — no Compose file passes it" >&2
done

for key in $still_placeholder; do
    echo "  PLACEHOLDER  $key — passed, but still a REPLACE value" >&2
done

echo "" >&2
echo "appsettings.${env_name}.json marks these REPLACE, so ValidateOnStart refuses" >&2
echo "to boot without them. Add each to the api service's environment in the" >&2
echo "overlay, taking the value from .env — the shape Jwt__Key already uses." >&2

exit 1
