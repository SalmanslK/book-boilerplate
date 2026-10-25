"""The Factory app's New project wizard: what it checks before the kit's setup.py runs, and the argv it builds.

The console starts `python <kit>/setup.py --greenfield|--brownfield … --json` — the kit's own, no-AI
setup — once a person has seen the exact command and confirmed it. This module holds everything the
console decides before that: where the kit is (`console.py --kit <folder>`), which stacks it offers,
whether an uploaded FSD is the kind of file it says it is, and whether the folder a person typed is
one setup may use. It never runs setup.py itself, never writes a file, and never creates a folder;
setup.py makes the one folder the person named, and its own refusals reach the person as it wrote them.

Every field is checked against a strict pattern before any process runs; the only processes this
module starts are fixed-argv `git rev-parse` reads, in a folder that has already passed those checks.
Python 3.11+, standard library only.
"""
from __future__ import annotations

import os
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

# -- the kit ----------------------------------------------------------------------------------------

KIT_SCRIPT = "setup.py"
KIT_INSTALLER = "init.py"
KIT_ADAPTERS = Path("templates") / "adapters"
# A stack is an adapter file's stem: what --stack takes, and nothing a shell or a path could read otherwise.
STACK_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
# The kit's release, read from init.py's text — never imported, never run.
KIT_VERSION_LINE = re.compile(r'^VERSION\s*=\s*"(\d+(?:\.\d+){0,3})"\s*$', re.MULTILINE)
KIT_HINT = "python devtools/factory/console/console.py --kit <the factory kit's folder>"


class KitError(ValueError):
    """The folder named with --kit is not a factory kit."""


@dataclass(frozen=True)
class Kit:
    path: Path
    version: str | None
    stacks: tuple[str, ...]

    @property
    def setup_script(self) -> Path:
        return self.path / KIT_SCRIPT

    def describe(self) -> dict:
        return {"path": str(self.path), "version": self.version, "stacks": list(self.stacks),
                "setup": str(self.setup_script)}


def load_kit(given: str | os.PathLike) -> Kit:
    """The kit in this folder: setup.py, init.py and at least one stack adapter — or a KitError saying what is missing."""
    folder = Path(os.path.expanduser(str(given))).resolve()
    if not folder.is_dir():
        raise KitError(f"--kit {given}: no such folder")
    missing = [name for name in (KIT_SCRIPT, KIT_INSTALLER) if not (folder / name).is_file()]
    if missing:
        raise KitError(f"--kit {folder}: not a factory kit — it has no {' or '.join(missing)}")
    stacks = tuple(sorted(p.stem for p in (folder / KIT_ADAPTERS).glob("*.toml") if STACK_PATTERN.fullmatch(p.stem)))
    if not stacks:
        raise KitError(f"--kit {folder}: no stack adapters in {KIT_ADAPTERS.as_posix()}/")
    try:
        found = KIT_VERSION_LINE.search((folder / KIT_INSTALLER).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        found = None
    return Kit(folder, found.group(1) if found else None, stacks)


# -- the FSD a person uploads ---------------------------------------------------------------------
# The kinds setup.py files (its FSD_TYPES), each by the media type the page sends and the bytes it must
# start with. The name the browser gives is never trusted: the kind comes from the media type, and the
# bytes must agree with it before anything is kept.

SETUP_UPLOAD_MAX_BYTES = 25 * 1024 * 1024  # setup.py's FSD_MAX_BYTES
FSD_KINDS: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/pdf": "pdf",
    "text/markdown": "md",
    "text/plain": "txt",
}
FSD_SUFFIXES = tuple(sorted(set(FSD_KINDS.values())))
KIND_PATTERN = re.compile(r"^(docx|pdf|md|txt)$")
ZIP_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF-"
DOCX_PARTS = ("[Content_Types].xml", "word/document.xml")
# Text an FSD may hold: tab, line breaks and form feed; any other control character means it is not text.
TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


class NotThatKind(ValueError):
    """The uploaded bytes are not the kind of file the upload said they were."""


def check_fsd_bytes(path: Path, kind: str) -> None:
    """Refuse a staged upload whose bytes are not a `kind` file: a .docx is a ZIP holding a Word document,
    a PDF starts %PDF-, a .md or .txt is UTF-8 text with no binary control characters."""
    with path.open("rb") as handle:
        head = handle.read(8)
    if kind == "pdf":
        if not head.startswith(PDF_MAGIC):
            raise NotThatKind("that file is not a PDF: it does not start with %PDF-")
    elif kind == "docx":
        if not head.startswith(ZIP_MAGIC):
            raise NotThatKind("that file is not a .docx: a Word document is a ZIP archive, and this does not start like one")
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError, ValueError) as error:
            raise NotThatKind(f"that file is not a .docx: its ZIP archive cannot be read ({error})") from None
        if not all(part in names for part in DOCX_PARTS):
            raise NotThatKind("that file is a ZIP archive but not a Word document: it has no word/document.xml")
    elif kind in ("md", "txt"):
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise NotThatKind(f"that file is not a .{kind} text file: it is not UTF-8 text") from None
        if TEXT_CONTROL.search(text):
            raise NotThatKind(f"that file is not a .{kind} text file: it holds binary control characters")
        if not text.strip():
            raise NotThatKind(f"that .{kind} file holds no text")
    else:
        raise NotThatKind("unknown kind of file")


# -- the folders a person types -------------------------------------------------------------------

PATH_MAX = 240
# The project's name: setup.py files the FSD as `<Name> - V1.<ext>` and the console lists it by this name.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()&'+-]{0,59}$")
# A new folder's own name: one path part, plain characters.
FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
PATH_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f*?"<>|]')
POSIX_SYSTEM = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/run", "/sbin", "/sys", "/usr", "/var",
                "/System", "/Library", "/Applications", "/private")
MODES = ("greenfield", "brownfield")
DETECT = "detect"
GIT_SECONDS = 30


class Refusal(ValueError):
    """The console refuses this setup before running anything; the message says what to change."""


def windows() -> bool:
    return os.name == "nt"


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def inside(child: Path, parent: Path) -> bool:
    """`child` is `parent` or under it, compared as the file system compares names."""
    c = os.path.normcase(os.path.normpath(str(child)))
    p = os.path.normcase(os.path.normpath(str(parent)))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def system_folders() -> list[Path]:
    """Folders nothing is ever set up in or under."""
    if not windows():
        return [Path(p) for p in POSIX_SYSTEM]
    found = []
    for key in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        value = os.environ.get(key)
        if value:
            found.append(Path(value))
    drive = os.environ.get("SystemDrive", "C:")
    found += [Path(drive + "\\Windows"), Path(drive + "\\Program Files"), Path(drive + "\\Program Files (x86)"),
              Path(drive + "\\ProgramData")]
    return found


def text_value(body: dict, name: str, limit: int, *, required: bool = True) -> str:
    value = body.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise Refusal(f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise Refusal(f"{name} must be text")
    value = value.strip()
    if len(value) > limit:
        raise Refusal(f"{name} is longer than {limit} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise Refusal(f"{name} may not contain control characters")
    return value


def absolute_folder(body: dict, name: str) -> Path:
    """
    A folder path as typed: absolute (a drive and a root on Windows; no network share), no `..` part, no
    wildcard or control characters, not a system folder, and not reached through a link or junction —
    the folder the person sees is the folder setup uses. Normalised, never resolved past what was typed.
    """
    raw = text_value(body, name, PATH_MAX)
    if PATH_BAD_CHARS.search(raw):
        raise Refusal(f"{name}: a folder path may not contain * ? \" < > | or control characters")
    parts = re.split(r"[\\/]+", raw)
    if any(part in ("..", ".") for part in parts):
        raise Refusal(f"{name}: give the folder's full path, without '.' or '..' parts")
    if windows():
        win = PureWindowsPath(raw)
        if raw.startswith(("\\\\", "//")):
            raise Refusal(f"{name}: a network share is not used for a project; give a folder on this computer")
        if not (win.drive and win.root and re.fullmatch(r"[A-Za-z]:", win.drive)):
            raise Refusal(f"{name}: give the full path, starting with a drive, such as D:\\Projects")
        if ":" in raw[2:]:
            raise Refusal(f"{name}: a ':' may only follow the drive letter")
    elif not raw.startswith("/"):
        raise Refusal(f"{name}: give the full path, starting with /")
    path = Path(os.path.normpath(raw))
    if path.parent == path:
        raise Refusal(f"{name}: not the root of a drive; give a folder")
    for system in system_folders():
        if inside(path, system):
            raise Refusal(f"{name}: {path} is a system folder")
    # Every part of the path that exists must be a real folder, not a link or junction to somewhere else:
    # the folder the person typed is the folder setup writes in.
    for part in [path, *path.parents]:
        if is_link(part):
            raise Refusal(f"{name}: {part} is a link or junction to another folder; give the real folder's path")
    return path


def is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, "is_junction", None)  # Python 3.12+
        if junction is not None:
            return junction()
        if os.name != "nt" or not path.exists():
            return False
        os.readlink(path)  # Python 3.11 on Windows: reads a junction's target, raises OSError for a plain folder
        return True
    except OSError:
        return False


def git_toplevel(folder: Path) -> Path | None:
    """`git rev-parse --show-toplevel` in a folder that passed absolute_folder; None outside a work tree."""
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True,
                              timeout=GIT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = done.stdout.decode("utf-8", "replace").strip()
    return Path(out) if done.returncode == 0 and out else None


@dataclass
class SetupPlan:
    """What the console will run, checked: shown to the person before they confirm, and run as it is."""

    mode: str
    folder: Path            # the project's folder: new (greenfield) or the repository (brownfield)
    name: str               # given to setup.py --name ("" when brownfield leaves it out)
    shown: str              # the name the project list shows
    stack: str              # "" when brownfield detects it
    fsd: Path | None        # the staged upload
    fsd_kind: str
    cwd: Path
    command: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {"mode": self.mode, "dir": str(self.folder), "name": self.name, "shown": self.shown,
                "stack": self.stack or DETECT, "fsd_kind": self.fsd_kind, "command": self.command, "cwd": str(self.cwd)}


def plan_setup(body: dict, *, kit: Kit, python: str, staged, projects: list[tuple[Path, str]],
               listed: list[tuple[Path, str]]) -> SetupPlan:
    """
    Check one setup request and build its argv — or a Refusal. `staged(sha256, kind)` returns the path
    of an upload staged in this console session (or raises); `projects` are those open in the console and
    `listed` those on the person's list, as (folder, shown name).
    """
    mode = body.get("mode")
    if mode not in MODES:
        raise Refusal("mode must be greenfield or brownfield")
    name = text_value(body, "name", 60, required=mode == "greenfield")
    if name and (not NAME_PATTERN.fullmatch(name) or name.endswith((".", " "))):
        raise Refusal("name: 1 to 60 letters, digits, spaces and . _ ( ) & ' + -, starting with a letter or digit "
                      "and not ending with a dot")
    stack = body.get("stack") or (DETECT if mode == "brownfield" else None)
    if stack == DETECT and mode == "brownfield":
        stack = ""
    elif stack not in kit.stacks:
        raise Refusal(f"stack must be one of: {', '.join(kit.stacks)}" + (" (or detect)" if mode == "brownfield" else ""))
    sha = body.get("fsd") or ""
    kind = body.get("fsd_type") or ""
    if mode == "greenfield" and not sha:
        raise Refusal("a new project needs its FSD: upload the .docx, .md, .txt or .pdf first")
    fsd = None
    if sha:
        if not isinstance(kind, str) or not KIND_PATTERN.fullmatch(kind):
            raise Refusal("fsd_type must be docx, pdf, md or txt")
        fsd = staged(sha, kind)

    if mode == "greenfield":
        parent = absolute_folder(body, "parent")
        folder_name = text_value(body, "folder", 80)
        if not FOLDER_PATTERN.fullmatch(folder_name) or folder_name.endswith((".", " ")) or WINDOWS_RESERVED.match(folder_name):
            raise Refusal("folder: the new folder's own name — letters, digits, spaces and . _ -, one part, "
                          "not a name Windows reserves")
        if not parent.is_dir():
            raise Refusal(f"parent: {parent} is not a folder on this computer; give one that exists — setup "
                          "makes only the new project folder inside it")
        folder = parent / folder_name
        if is_link(folder):
            raise Refusal(f"{folder} is a link or junction; setup would write wherever it points. Use a real folder.")
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise Refusal(f"{folder} already exists and is not empty. A new project needs a new or empty folder; "
                          "for an existing repository choose Brownfield.")
        if git_toplevel(parent) is not None:
            raise Refusal(f"{parent} is inside a git repository; a new project there would be a repository inside "
                          "another. Choose a parent folder outside every repository.")
        cwd = parent
    else:
        folder = absolute_folder(body, "path")
        if not folder.is_dir():
            raise Refusal(f"path: {folder} is not a folder on this computer")
        top = git_toplevel(folder)
        if top is None or not same_path(top, folder):
            raise Refusal(f"path: {folder} is not the top folder of a git repository" +
                          (f" (its top is {top})" if top is not None else "") + ". Give the repository's own folder.")
        cwd = folder

    shown = name or folder.name
    for where, entries in (("open in this console", projects), ("on your project list", listed)):
        for path, entry_name in entries:
            if same_path(path, folder):
                raise Refusal(f"{folder} is already {where}, as {entry_name}")
            if mode == "greenfield" and inside(folder, path):
                raise Refusal(f"{folder} is inside {entry_name}, a project already {where}")
            if entry_name == shown:
                raise Refusal(f"another project {where} is already called {shown}; give this one another name")

    command = [python, str(kit.setup_script), f"--{mode}", f"--dir={folder}"]
    if name:
        command.append(f"--name={name}")
    if fsd is not None:
        command.append(f"--fsd={fsd}")
    if stack:
        command.append(f"--stack={stack}")
    command.append("--json")
    return SetupPlan(mode, folder, name, shown, stack, fsd, kind if fsd is not None else "", cwd, command)
