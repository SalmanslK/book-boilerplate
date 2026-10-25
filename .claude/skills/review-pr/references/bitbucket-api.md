# Bitbucket Cloud API — the calls this skill makes

Bitbucket Cloud REST API **v2.0**, base `https://api.bitbucket.org/2.0`.

**GitHub is not supported yet.** If the remote is not `bitbucket.org`, say so and
stop — do not improvise calls against another host's API.

Read the repository from the remote rather than hardcoding it, so a fork or a
rename does not silently post to the wrong place:

```bash
REPO=$(git remote get-url origin | sed -E 's#.*bitbucket\.org[:/]##; s#\.git$##')
```

Two substitutions, not one pattern with an optional `(\.git)?` group — a greedy
match happily swallows `.git` into the slug, and the API is lenient enough to
accept `workspace/repo.git` and answer normally, so the bug survives testing and
surfaces later somewhere less forgiving.

---

## Authentication, without leaking the token

The token comes from the credential store git already pushes with — see
[Credentials](../SKILL.md#credentials). Nothing needs configuring.

```bash
TOK=$(printf 'protocol=https\nhost=bitbucket.org\npath=%s.git\n\n' "$REPO" \
  | GCM_INTERACTIVE=never git credential fill 2>/dev/null \
  | sed -n 's/^password=//p')
```

- **`GCM_INTERACTIVE=never`** — without it, Git Credential Manager opens a GUI
  sign-in prompt when it has nothing stored, and a non-interactive shell hangs on
  it until the command times out.
- **The `path=` line** — a store configured per repository path
  (`credential.useHttpPath`) returns nothing, or another repository's credential,
  for a bare host query.
- **Empty `$TOK`** means the store has nothing for this repository. Say so and
  stop; never prompt for the token in the conversation.

**Bearer is the scheme.** A repository, workspace or OAuth access token goes in an
`Authorization: Bearer` header with no username. (An Atlassian API token — it starts
with `ATATT` — is the one exception: it needs Basic with the account **email** as
the user. Check the prefix without printing the token: `[ "${TOK#ATATT}" != "$TOK" ]`.)

**Never put the token in `-u`, in a URL, or in a file.** Arguments are visible in
the process list and in shell history; a file survives the run. `curl --config -`
reads the credential from stdin, which does neither:

```bash
curl --silent --fail-with-body --config - "$URL" <<CURLRC
header = "Authorization: Bearer $TOK"
CURLRC
```

`$TOK` lives only inside the one shell invocation that uses it. Never `echo` it,
never `set -x` around it, never print a request with its headers.

`--fail-with-body` makes an HTTP error a non-zero exit *and* still prints the body,
which is where Bitbucket explains what it objected to.

**Required scope:** `pull request: write`. Reading needs `pull request: read`, so a
read-only token gets through Step 1 and fails at Step 5 — check before posting
rather than after.

---

## Verify the credential — one read-only call

```
GET /2.0/repositories/$REPO
```

A `200` means the scheme is right and the token can read. Do this before
reviewing, so a bad credential costs one request, not a finished review.

---

## Find the PR for a branch

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
URL="https://api.bitbucket.org/2.0/repositories/$REPO/pullrequests?state=OPEN&q=source.branch.name=%22$BRANCH%22"
```

Returns `values[]`. Each carries `id`, `title`, `description`, `source.branch.name`,
`destination.branch.name`, `source.commit.hash`, and `links.html.href`.

- **Empty** → no open PR. Stop; do not create one.
- **More than one** → list them and ask. Two open PRs from one branch usually means
  someone opened a second by mistake, and posting to the wrong one is confusing to
  unpick.

By id instead:

```
GET /2.0/repositories/$REPO/pullrequests/$PR_ID
```

**Also fetch the diffstat** to know what the PR actually touches, rather than
trusting the local working tree, which may be ahead of what was pushed:

```
GET /2.0/repositories/$REPO/pullrequests/$PR_ID/diffstat?pagelen=100
```

---

## Read existing comments — always, before posting

```
GET /2.0/repositories/$REPO/pullrequests/$PR_ID/comments?pagelen=100
```

Paginate on `next` until it is absent. Each value has `id`, `content.raw`,
`content.html`, `user.display_name`, `deleted`, `parent` (for a reply), and — for
inline comments — `inline.path` and `inline.to`.

Read them for two reasons:

1. **Someone may already have reviewed this.** An earlier run of this skill, or a
   person, may have raised the same point. Say what is already there in the
   posting plan.
2. **Dedupe.** Key on `inline.path` + `inline.to` and skip any finding whose line
   already carries a comment. Nothing machine-readable goes in the body —
   Bitbucket escapes HTML comments instead of stripping them, so a hidden marker is
   not hidden.

Two exclusions when building the key, and both matter:

- **`"deleted": true`** — a comment the author removed should not silently come back.
- **anything with a `parent`** — **a reply carries the same `inline` block as the
  comment it answers.** Include replies and a thread makes its own line look
  occupied, so the next run silently drops a real finding there. Filter to top-level
  comments only.

---

## Post a comment

```
POST /2.0/repositories/$REPO/pullrequests/$PR_ID/comments
Content-Type: application/json
```

**Inline comment** — `path` plus exactly one of `to` or `from`:

```json
{
  "content": { "raw": "**🔴 CRITICAL — two writes outside a transaction — New**\n\n…" },
  "inline": { "path": "src/orders/create_order.py", "to": 97 }
}
```

| Field | Means |
|---|---|
| `path` | Repository-relative, forward slashes, **as the diff spells it**. A path that is not in the diff is accepted and renders detached from any line |
| `to` | Line number in the **new** version — what you want for added or changed code |
| `from` | Line number in the **old** version — only for a line the PR deleted |

**Send `to` or `from`, never both.** Both is accepted and renders unpredictably.

**Reply into a thread** — for something that belongs on a conversation already
under way, rather than as a new comment:

```json
{ "content": { "raw": "…" }, "parent": { "id": 123456 } }
```

(A summary comment is the same body with no `inline` key. This skill does not post
one — see [Step 5](../SKILL.md#step-5--post).)

Build the JSON into a file under the scratchpad and post it with `--data @file`.
Inlining a multi-line markdown body into an argument mangles newlines and breaks on
quotes in code snippets.

> **A `while read` loop silently drops the last comment.** `read` returns non-zero
> at EOF, so a file whose final line has no trailing newline gets read into the
> variables and then skipped — the loop exits before the body runs. **Write the
> index with a trailing newline**, or use `while read … || [ -n "$line" ]`. Always
> reconcile the count afterwards rather than trusting the loop.

> **A "failed" POST may have landed.** A timeout or a dropped connection after the
> request was sent can leave the comment on the PR. Re-list the comments before
> retrying anything, and let the `path:line` key decide.

> **Severity emoji reach Bitbucket fine and can kill a Windows console.** The JSON
> is UTF-8 and the API is happy, but printing the posting plan through Python on a
> cp1252 console raises `UnicodeEncodeError`. Set `PYTHONIOENCODING=utf-8` on any
> command that echoes a comment body or a severity marker. The failure is in the
> *reporting*, not the posting, so it can strand a run after the writes have already
> happened.

---

## What must never be called

These exist on the same resource and are all one word away from a comment:

```
POST   /pullrequests/$PR_ID/approve            ← approving is a person's decision
DELETE /pullrequests/$PR_ID/approve
POST   /pullrequests/$PR_ID/request-changes    ← also a person's decision
POST   /pullrequests/$PR_ID/merge              ← never
POST   /pullrequests/$PR_ID/decline            ← never
PUT    /pullrequests/$PR_ID                    ← edits title, description, reviewers
PUT    /pullrequests/$PR_ID/comments/$ID       ← edits a comment — own, cosmetic only (SKILL.md, hard limit 3)
DELETE /pullrequests/$PR_ID/comments/$ID       ← own, and only when the user asks
```

`POST .../comments` is the only write this skill makes on its own initiative.

---

## Failures worth recognising

| | Almost always |
|---|---|
| `401` | The wrong scheme for the token — Basic for an access token, or Bearer for an `ATATT` API token — or the token is revoked |
| `403` | The token lacks `pull request: write`. Reading worked, so this surfaces only at the first post |
| `404` on a PR that exists | Wrong workspace or slug — check what the remote actually says |
| `400` on an inline comment | `path` not in the diff, or both `to` and `from` sent |
| `429` | Rate limited. **Stop.** Report what was posted; do not retry in a loop |
| Hang with no output | `git credential fill` waiting on a GUI prompt — `GCM_INTERACTIVE=never` was missing |

**On any failure, stop and report what had already been posted.** The `path:line`
key makes a re-run resume rather than duplicate, so a partial post is recoverable —
an account locked out by retries is not.
