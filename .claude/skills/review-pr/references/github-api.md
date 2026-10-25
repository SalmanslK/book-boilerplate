# GitHub — how this skill talks to a pull request

Use the **`gh` CLI**. It holds the credential (`gh auth login`); this skill never reads, prints or stores a
token. If `gh auth status` fails, tell the person to run `gh auth login` and stop. Never accept a token
pasted into the conversation.

## Detect the host

`git remote get-url origin` contains `github.com` → this file applies. `bitbucket.org` → the Bitbucket
sections of `SKILL.md` and `bitbucket-api.md` apply. Anything else → say so and suggest `/review-branch`.

```bash
REPO=$(git remote get-url origin | sed -E 's#.*github\.com[:/]##; s#\.git$##')   # owner/name
gh auth status                       # must succeed
gh api "repos/$REPO" --jq .full_name # read-only check that the credential reaches the repo
```

## Find the PR and its head commit

```bash
gh pr view <number> --repo "$REPO" --json number,title,baseRefName,headRefName,headRefOid,state
gh pr list --repo "$REPO" --head "$(git branch --show-current)" --state open --json number,title
```

Stop if more than one matches; list them and ask. Record the **head commit** (`headRefOid`): inline comment
line numbers refer to the file as the PR stands at that commit, and a PR that moves invalidates them.

## Read existing comments — before reviewing

```bash
gh api "repos/$REPO/pulls/<n>/comments" --paginate   # inline review comments
gh api "repos/$REPO/issues/<n>/comments" --paginate  # general conversation comments
gh pr view <n> --repo "$REPO" --json reviews
```

**Dedupe on position: `path` + `line`.** Count only **top-level** comments (no `in_reply_to_id`); a reply
shares its parent's position and must not make a line look occupied. A comment already on that line is an
earlier run's or a person's work — skip the finding and say so in the report.

## Post — one review, inline comments in one call

Post all inline findings as a single review of type `COMMENT`. **Never `APPROVE` or `REQUEST_CHANGES`**:
this skill does not approve or block.

```bash
cat > /tmp/review.json <<'JSON'
{
  "commit_id": "<headRefOid>",
  "event": "COMMENT",
  "body": "<the terminal review's verdict, short>",
  "comments": [
    {"path": "src/File.cs", "line": 42, "side": "RIGHT", "body": "<finding>"}
  ]
}
JSON
gh api -X POST "repos/$REPO/pulls/<n>/reviews" --input /tmp/review.json
```

- `line` is the line in the **new** file; `side: RIGHT`. A finding on deleted code or spanning files stays
  in the terminal review.
- GitHub renders `<!-- comments -->` as hidden text, but put **nothing machine-readable** in a body anyway:
  no marker, no tracking id, no tool signature, no AI attribution.
- Fall back to `gh pr comment <n> --repo "$REPO" --body-file <file>` for a finding with no line.

## When a call fails

Do not retry blindly. `401`/`403` means the credential lacks write access to pull requests; `422` usually
means a line that is not in the diff. A failed post may still have landed — **re-list the comments before any
retry**, then reconcile the `path:line` pairs actually on the PR against the plan and post whatever is
absent.
