"""
Jira Cloud for the referee: one issue — a Task, a Bug, a Story — read so a factory run can build or
fix it. **Read only:** this module has a GET and nothing else, so no session can change Jira through
it (CLAUDE.md "Jira": Claude drafts, and posts only after approval, by hand).

The credential is the person's own, in git's credential store for the Jira site (`[jira] site` in
factory.toml): the username is their Atlassian email and the password a scoped API token with
`read:jira-work` — the only kind supported, so Jira itself refuses a write. Asked with GCM_INTERACTIVE=never, as in `bitbucket.py`. It reaches
curl on stdin, never a command line, and is never printed, logged or stored. Nothing this module
prints carries an email: people appear by display name only (CLAUDE.md "Private settings").

Python 3.11+, standard library only; curl when there is one, as in `bitbucket.py`.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import bitbucket

ATTEMPTS = 3
KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
# Custom fields worth reading, by name: what a Task or a Bug says beyond its description.
USEFUL_CUSTOM_FIELD = re.compile(r"acceptance|steps to reproduce|expected|actual|environment|severity|root cause", re.I)
Fetch = Callable[[str, str], tuple[int, str]]


class JiraError(Exception):
    """Jira could not be asked — never a reason to guess what the issue says, only to stop."""


def credential(root: Path, site: str) -> str:
    """The value of a Basic Authorization header, from git's credential store. Never printed."""
    env = {**os.environ, "GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}
    why = ""
    for _ in range(3):
        try:
            done = subprocess.run(["git", "credential", "fill"], cwd=root, env=env, capture_output=True, text=True,
                                  input=f"protocol=https\nhost={site}\n\n", timeout=30)
        except subprocess.TimeoutExpired:
            why = " (`git credential fill` timed out)"
            continue  # a credential helper waiting on a prompt that cannot show
        except OSError as error:
            raise JiraError(f"`git credential fill` could not run ({type(error).__name__})") from None
        if done.returncode:
            # Its exit code only: what a credential helper prints is not ours to repeat.
            why = f" (`git credential fill` exited {done.returncode})"
            continue
        found = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
        if found.get("username") and found.get("password"):
            return base64.b64encode(f"{found['username']}:{found['password']}".encode()).decode()
        why = ""
    raise JiraError(f"No Jira credential for {site} in git's credential store{why} — devtools/README.md, "
                    "Reading a Jira issue, says how to store one")


def fetch(url: str, basic: str) -> tuple[int, str]:
    """(status, body). curl when there is one, the header on stdin; urllib otherwise."""
    curls = bitbucket.curl_binaries()
    if not curls:
        request = urllib.request.Request(url, headers={"Authorization": f"Basic {basic}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            raise JiraError("Jira could not be reached") from None
    config = f'header = "Authorization: Basic {basic}"\nheader = "Accept: application/json"\n'
    try:
        return bitbucket.get_through_curls(curls, url, config, ATTEMPTS)
    except bitbucket.CurlFailure as failure:
        raise JiraError(f"Jira could not be reached: {failure}") from None


def cloud_id(site: str, get: Fetch = fetch) -> str:
    """The site's id, which a scoped token's address needs. `_edge/tenant_info` asks for no credential."""
    status, body = get(f"https://{site}/_edge/tenant_info", "")
    try:
        found = json.loads(body).get("cloudId", "") if status == 200 else ""
    except ValueError:
        found = ""
    if not found:
        raise JiraError(f"{site} did not say its cloud id (answered {status})")
    return found


def read_issue(site: str, key: str, basic: str, get: Fetch = fetch) -> dict:
    """
    The issue's fields, with `names` mapping custom field ids to their names. Only a scoped, read-only
    API token is supported, and Atlassian accepts one only at api.atlassian.com under the site's cloud
    id — so even a leaked token could not change Jira. A classic token is refused there.
    """
    if not KEY.match(key):
        raise JiraError(f"{key!r} is not a Jira key (PROJECT-123)")
    query = urllib.parse.urlencode({"fields": "*all", "expand": "names"})
    url = f"https://api.atlassian.com/ex/jira/{cloud_id(site, get)}/rest/api/3/issue/{urllib.parse.quote(key)}?{query}"
    status, body = get(url, basic)
    if status in (401, 403):
        raise JiraError(f"Jira answered {status} for {key} — the stored token is wrong, expired, not a scoped token "
                        "with read:jira-work, or cannot see this project")
    if status == 404:
        raise JiraError(f"Jira has no {key}, or the stored token cannot see it")
    if status != 200:
        raise JiraError(f"Jira answered {status} for {key}")
    try:
        return json.loads(body)
    except ValueError:
        raise JiraError("Jira answered with something that is not JSON") from None


# ---------------------------------------------------------------- Atlassian Document Format → text

def adf_text(node: object) -> str:
    """A description or comment in Atlassian Document Format, as plain Markdown-ish text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(child) for child in node)
    if not isinstance(node, dict):
        return ""
    kind, attrs = node.get("type", ""), node.get("attrs") or {}
    inner = adf_text(node.get("content"))
    if kind == "text":
        text = node.get("text", "")
        link = next((m.get("attrs", {}).get("href") for m in node.get("marks") or [] if m.get("type") == "link"), "")
        return f"[{text}]({link})" if link and link != text else text
    if kind == "mention":
        return attrs.get("text") or "@someone"
    if kind in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url", "")
    if kind == "emoji":
        return attrs.get("text") or attrs.get("shortName", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return "[an attached image or file]\n\n" if kind != "media" else ""
    if kind == "heading":
        return "#" * int(attrs.get("level", 3)) + " " + inner.strip() + "\n\n"
    if kind == "paragraph":
        return inner.strip() + "\n\n" if inner.strip() else ""
    if kind == "codeBlock":
        return f"```{attrs.get('language', '')}\n{inner}\n```\n\n"
    if kind in ("bulletList", "orderedList"):
        lines = []
        for number, item in enumerate(node.get("content") or [], 1):
            text = adf_text(item).strip().replace("\n\n", "\n").replace("\n", "\n   ")
            lines.append(f"{f'{number}.' if kind == 'orderedList' else '-'} {text}")
        return "\n".join(lines) + "\n\n"
    if kind == "blockquote":
        return "> " + inner.strip().replace("\n", "\n> ") + "\n\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "table":
        rows = [[adf_text(cell).strip().replace("\n", " ").replace("|", "\\|") for cell in row.get("content") or []]
                for row in node.get("content") or []]
        if not rows:
            return ""
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
        lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return "\n".join(lines) + "\n\n"
    if kind == "panel":
        return f"> **{attrs.get('panelType', 'note').title()}:** " + inner.strip().replace("\n", "\n> ") + "\n\n"
    return inner


def person(value: object) -> str:
    """A display name only — never an email or an account id."""
    return (value or {}).get("displayName", "") if isinstance(value, dict) else ""


def field_text(value: object) -> str:
    if isinstance(value, dict) and value.get("type") == "doc":
        return adf_text(value).strip()
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (field_text(v) for v in value)))
    return "" if value is None else str(value)


def summary(issue: dict) -> dict:
    """The issue's key, type, status and updated time: no text, nothing personal."""
    fields = issue.get("fields") or {}
    return {"key": issue.get("key", ""), "type": (fields.get("issuetype") or {}).get("name", ""),
            "status": (fields.get("status") or {}).get("name", ""), "updated": fields.get("updated", "")}


def render(issue: dict, site: str) -> str:
    """The issue for a session to read: everything a person would read on its page, people by name."""
    fields, names = issue.get("fields") or {}, issue.get("names") or {}
    key = issue.get("key", "")
    about = summary(issue)
    lines = [f"# {key} · {about['type']} · {fields.get('summary', '')}", "",
             f"https://{site}/browse/{key}", ""]
    facts = [("Status", about["status"]), ("Priority", field_text(fields.get("priority"))),
             ("Assignee", person(fields.get("assignee")) or "unassigned"), ("Reporter", person(fields.get("reporter"))),
             ("Parent", " ".join(filter(None, [(fields.get("parent") or {}).get("key", ""),
                                              ((fields.get("parent") or {}).get("fields") or {}).get("summary", "")]))),
             ("Labels", ", ".join(fields.get("labels") or [])), ("Components", field_text(fields.get("components"))),
             ("Fix versions", field_text(fields.get("fixVersions"))), ("Created", fields.get("created", "")),
             ("Updated", fields.get("updated", ""))]
    lines += [f"- **{label}:** {value}" for label, value in facts if value]
    lines += ["", "## Description", "", adf_text(fields.get("description")).strip() or "*(none)*", ""]
    for field_id, name in sorted(names.items(), key=lambda pair: pair[1]):
        if field_id.startswith("customfield_") and USEFUL_CUSTOM_FIELD.search(name):
            text = field_text(fields.get(field_id)).strip()
            if text:
                lines += [f"## {name}", "", text, ""]
    links = []
    for link in fields.get("issuelinks") or []:
        kind = link.get("type") or {}
        other, verb = (link["outwardIssue"], kind.get("outward", "")) if "outwardIssue" in link \
            else (link.get("inwardIssue") or {}, kind.get("inward", ""))
        other_fields = other.get("fields") or {}
        links.append(f"- {verb} {other.get('key', '')} — {other_fields.get('summary', '')} "
                     f"({(other_fields.get('status') or {}).get('name', '')})")
    links += [f"- sub-task {s.get('key', '')} — {(s.get('fields') or {}).get('summary', '')}"
              for s in fields.get("subtasks") or []]
    if links:
        lines += ["## Linked issues", "", *links, ""]
    attachments = fields.get("attachment") or []
    if attachments:
        lines += ["## Attachments", "", "Not downloaded — open the issue to see them.", "",
                  *(f"- {a.get('filename', '')} ({a.get('mimeType', '')}, {a.get('created', '')[:10]})"
                    for a in attachments), ""]
    comments = ((fields.get("comment") or {}).get("comments")) or []
    lines += [f"## Comments ({len(comments)})", ""]
    for comment in comments:
        lines += [f"**{person(comment.get('author')) or '?'}** ({comment.get('created', '')[:16]}):", "",
                  adf_text(comment.get("body")).strip(), ""]
    return "\n".join(lines).rstrip() + "\n"
