"""
The requirements index (requirements/read-me.txt) read as data, and the weekly capture rule, for
`book requirements status` and devtools/checks/check-protected-paths.py — one reading, not two.

The index has a LINKS section (`<label>: <link, or "none - <why>">`, one line per live source) and
folder sections — HAS A LIVE LINK, and those with none (the template's NO LIVE LINK; an index may split it
into NO LIVE SOURCE, for files the client delivered, and NO LIVE LINK APPLICABLE, for our own code) — each
naming its folders at the start of a line (`fsd/`). A folder's own read-me.txt keeps one `Last checked: <date> - <outcome>` line.

The weekly rule (requirements/read-me.txt, Cadence; /book:check-sources, Phase 4): **at most one capture
per source per week**, Monday to Sunday. A capture is a snapshot file added or replaced in a source's
folder; its `read-me.txt`, its `backup/` folder (a superseded copy moved aside) and a saved
`comment-threads - <date>.md` (a record, not a capture) are not captures.

Standard library only; reads text, runs nothing.
"""
from __future__ import annotations

import datetime
import re

WEEKLY_RULE = "at most one capture per source per week, Monday to Sunday — requirements/read-me.txt, Cadence"
LIVE_SECTION = "HAS A LIVE LINK"
# The sections listing folders with no live link: the template's one, and the two an index may split it into.
NO_LIVE_SECTIONS = ("NO LIVE LINK", "NO LIVE SOURCE", "NO LIVE LINK APPLICABLE")
LINKS_SECTION = "LINKS"

HEADING = re.compile(r"^===\s*(.+?)\s*===\s*$")
FOLDER = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)/")
LINK_LINE = re.compile(r"^([^\s(][^:]*?(?:\([^)]*\))?)\s*:\s+(.+)$")
DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
VERSION = re.compile(r"(?:^|[\s_-])V(\d+)(?=[./\s]|$)")
LAST_CHECKED = re.compile(r"^Last checked:\s*(.*)$", re.MULTILINE)


def sections(text: str) -> dict[str, list[str]]:
    """The index's lines by `=== <heading> ===`, keyed by the heading's text up to any " - " or " (", in capitals."""
    found: dict[str, list[str]] = {"": []}
    current = ""
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            current = re.split(r" - | \(", heading.group(1))[0].strip().upper()
            found[current] = []
        else:
            found[current].append(line)
    return found


def links(text: str) -> list[tuple[str, str]]:
    """(label, value) for every line of LINKS; prose lines (in brackets, or without a colon) are skipped."""
    pairs = []
    for line in sections(text).get(LINKS_SECTION, []):
        stripped = line.strip()
        if not stripped or stripped.startswith("("):
            continue
        match = LINK_LINE.match(stripped)
        if match and not match.group(1).lower().startswith("http"):
            pairs.append((match.group(1).strip(), match.group(2).strip()))
    return pairs


def folders(text: str, section: str | tuple[str, ...]) -> list[str]:
    """The folders a folder section (or several) names, in order: `fsd/` at the start of a line gives `fsd`."""
    names = []
    found = sections(text)
    for line in [line for name in ((section,) if isinstance(section, str) else section)
                 for line in found.get(name, [])]:
        match = FOLDER.match(line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return names


def live_folders(text: str) -> list[str]:
    """The folders the weekly rule judges: those under HAS A LIVE LINK."""
    return folders(text, LIVE_SECTION)


def link_state(value: str) -> tuple[str, str]:
    """
    ("live", link) for a link; ("none" | "not shared" | "not set up", the reason) for what the index
    records instead; ("not configured", …) for the template's `<link>` placeholder or anything else.
    """
    value = value.strip()
    if re.match(r"https?://", value):
        return "live", value
    lowered = value.lower()
    for state in ("not shared", "not set up"):
        if lowered.startswith(state):
            return state, value
    if lowered.startswith("none"):
        return "none", value[len("none"):].lstrip(" -–—:;,").strip()
    if not value or "<link>" in lowered:
        return "not configured", "the template's <link> placeholder"
    return "not configured", value


def last_checked(read_me: str) -> tuple[str, str]:
    """(date, outcome) from a folder read-me's `Last checked:` line; ("", "") when it has none."""
    match = LAST_CHECKED.search(read_me)
    if not match:
        return "", ""
    line = match.group(1).strip()
    date = DATE.search(line)
    rest = line[date.end():] if date else line
    outcome = re.split(r"[:;(]|\.\s", rest.lstrip(" -–—").strip(), maxsplit=1)[0].strip(" .")
    return (date.group(1) if date else ""), outcome


def is_snapshot(path: str, folder: str) -> bool:
    """
    A capture's file: under `folder/`, and not its read-me.txt, a dotfile (.gitkeep), a file in backup/
    or a comment-threads record.
    """
    prefix = folder.rstrip("/") + "/"
    if not path.startswith(prefix):
        return False
    rest = path[len(prefix):]
    name = rest.rsplit("/", 1)[-1]
    if name.lower() == "read-me.txt" or name.lower().startswith("comment-threads") or name.startswith("."):
        return False
    return "/backup/" not in "/" + rest.lower()


def version(name: str) -> int:
    """The highest V<n> a name carries, numerically (V14 above V9); -1 when it carries none."""
    numbers = [int(n) for n in VERSION.findall(name)]
    return max(numbers) if numbers else -1


def name_date(name: str) -> str:
    """The last YYYY-MM-DD a file name carries, or ""."""
    dates = DATE.findall(name)
    return dates[-1] if dates else ""


def week_of(day: datetime.date) -> tuple[int, int]:
    """The ISO week (year, week): Monday to Sunday."""
    year, week, _ = day.isocalendar()
    return year, week


def monday(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=day.weekday())


def parse_day(text: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def second_captures(branch: dict[str, dict[datetime.date, list[str]]],
                    base: dict[str, list[datetime.date]], judged_from: datetime.date) -> list[str]:
    """
    One line per breach of the weekly rule. `branch`: per source folder, the days the branch captured
    and the files each day; `base`: per folder, the days the destination already captured. Captures
    on one day are one capture (several files of one source, or a capture spread over commits). Two
    days in one week, or any day in a week the destination already captured, is a second capture.
    Captures dated before `judged_from` are not judged: they predate this check, and history is never
    rewritten (CLAUDE.md, Git).
    """
    found = []
    for folder in sorted(branch):
        days = sorted(day for day in branch[folder] if day >= judged_from)
        for week in sorted({week_of(day) for day in days}):
            mine = [day for day in days if week_of(day) == week]
            theirs = sorted({day for day in base.get(folder, []) if week_of(day) == week})
            if len(mine) > 1 or theirs:
                shown = "; ".join(f"{day} ({', '.join(sorted(branch[folder][day])[:3])})" for day in mine)
                already = f"; the destination already captured it on {', '.join(map(str, theirs))}" if theirs else ""
                found.append(f"  {folder}/ — week {week[0]}-W{week[1]:02d}: captured {shown}{already}")
    return found
