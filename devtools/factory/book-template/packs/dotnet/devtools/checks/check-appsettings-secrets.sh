#!/bin/sh
#
# Refuses a real credential in a tracked appsettings*.json.
#
# appsettings.json and appsettings.<Env>.json hold placeholders only — real
# values come from user secrets locally and app settings or Key Vault in Azure.
# That rule was already written down; this is what enforces it, because a
# signing key reached appsettings.Development.json once while user secrets were
# already configured and holding a working key. Convenience was never the
# problem, so a more convenient secret store would not have helped.
#
# Every tracked appsettings file is checked, Development included: a tracked file
# reaches git history permanently and is copied into the Docker image.
#
# Three rules, all encoding a convention the repository already follows, so no
# current file trips them:
#
#   Jwt:Key   must start with REPLACE, or say "not-a-secret" in its own value.
#             appsettings.Testing.json:8 already names itself that way; this
#             makes that name load-bearing rather than decorative.
#
#   Password= in a connection string must be "postgres" — what
#             scripts/start-local-postgres.ps1 starts the container with and
#             what DesignTimeDbContextFactory falls back to. A throwaway local
#             container password is not a secret. Anything else is.
#
#   "Password" as a JSON key — the shape Email:Smtp:Password uses. The rule
#             above looks for Password= and finds nothing here, so without this
#             an SMTP password passed both checks.
#
# Offending values are NEVER printed. Echoing one would put it in terminal
# scrollback and CI logs, which is the thing being prevented.
#
# Usage:
#   check-appsettings-secrets.sh [--staged]    what a commit would contain (hook)
#   check-appsettings-secrets.sh --tracked     every tracked file (CI)

set -eu

mode="${1:---staged}"

case "$mode" in
    --staged | --tracked) ;;
    *)
        echo "usage: $0 [--staged|--tracked]" >&2
        exit 2
        ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

findings="$tmp/findings"
: > "$findings"

if [ "$mode" = "--staged" ]; then
    files="$(git diff --cached --name-only --diff-filter=ACM -- '*appsettings*.json' || true)"
else
    files="$(git ls-files -- '*appsettings*.json' || true)"
fi

if [ -z "$files" ]; then
    echo "No appsettings files to check."
    exit 0
fi

printf '%s\n' "$files" | while IFS= read -r file; do
    [ -n "$file" ] || continue

    body="$tmp/body"

    if [ "$mode" = "--staged" ]; then
        # The staged blob, not the working tree — the hook must judge what the
        # commit would actually contain.
        git show ":$file" > "$body" 2>/dev/null || continue
    else
        [ -f "$file" ] || continue
        cat "$file" > "$body"
    fi

    grep -n '"Key"[[:space:]]*:[[:space:]]*"' "$body" 2>/dev/null | while IFS= read -r hit; do
        lineno="${hit%%:*}"
        value="$(printf '%s' "$hit" | sed 's/.*"Key"[[:space:]]*:[[:space:]]*"//; s/".*//')"

        case "$value" in
            REPLACE* | replace*) continue ;;
            *not-a-secret*) continue ;;
        esac

        printf '  %s:%s  Jwt:Key looks like a real signing key\n' "$file" "$lineno" >> "$findings"
    done || true

    grep -n 'Password=' "$body" 2>/dev/null | while IFS= read -r hit; do
        lineno="${hit%%:*}"
        value="$(printf '%s' "$hit" | sed 's/.*Password=//; s/[";].*//')"

        [ "$value" = "postgres" ] && continue

        printf '  %s:%s  connection string carries a real password\n' "$file" "$lineno" >> "$findings"
    done || true

    # The JSON form, "Password": "…", which the connection-string rule above does
    # not match — it looks for Password= and this has a colon. Email:Smtp:Password
    # is the first setting of this shape, and without this rule a real SMTP
    # password would pass both checks and reach a commit.
    grep -n '"Password"[[:space:]]*:[[:space:]]*"' "$body" 2>/dev/null | while IFS= read -r hit; do
        lineno="${hit%%:*}"
        value="$(printf '%s' "$hit" | sed 's/.*"Password"[[:space:]]*:[[:space:]]*"//; s/".*//')"

        case "$value" in
            REPLACE* | replace*) continue ;;
            *not-a-secret*) continue ;;
            postgres) continue ;;
            "") continue ;;
        esac

        printf '  %s:%s  a settings value carries a real password\n' "$file" "$lineno" >> "$findings"
    done || true
done

if [ -s "$findings" ]; then
    echo "Refusing: a real credential is present in a settings file that is committed." >&2
    echo >&2
    cat "$findings" >&2
    echo >&2
    echo "These files hold placeholders only. Put the real value in user secrets:" >&2
    echo >&2
    echo '  dotnet user-secrets set "Jwt:Key" "<64+ random bytes, base64>" \' >&2
    echo '    --project src/Boilerplate.API' >&2
    echo '  dotnet user-secrets set "ConnectionStrings:DefaultConnection" "<connection string>" \' >&2
    echo '    --project src/Boilerplate.API' >&2
    echo >&2
    echo "A Postgres started by scripts/start-local-postgres.ps1 uses Password=postgres." >&2
    echo "That is not a secret and may stay in the file." >&2
    exit 1
fi

echo "Settings files carry no credentials."
