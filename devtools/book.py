#!/usr/bin/env python3
"""
The Book's command line: `python devtools/book.py <command> ...` - `book pr open`, `book impact`, `book deploy
check`, and the Book's own steps as `book verify`, `book init` and `book upgrade`.

The commands live in devtools/factory/factory.py, the module this file runs; from v1.5 that module is not a
command of its own.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

# devtools/factory first, so `import book` inside factory.py finds the Book module there, never this file.
sys.path.insert(0, str(Path(__file__).resolve().parent / "factory"))

import factory  # noqa: E402

# `book verify` is the module's `book verify` step: the Book's steps need no second "book".
BOOK_STEPS = ("verify", "init", "upgrade")


def arguments(argv: list[str]) -> list[str]:
    return ["book", *argv] if argv and argv[0] in BOOK_STEPS else list(argv)


# A project that kept its own factory.py from before v1.4.0 through an upgrade (a CONFLICT not merged yet) has a
# main() that takes no name: nothing runs until it is merged.
NOT_MERGED = ("book: devtools/factory/factory.py predates Book v1.4.0 - merge it from the template (`book upgrade` "
              "lists it as a CONFLICT); nothing runs until then.")


def names_itself() -> bool:
    try:
        return "prog" in inspect.signature(factory.main).parameters
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = arguments(sys.argv[1:] if argv is None else argv)
    if not names_itself():
        print(NOT_MERGED, file=sys.stderr)
        return 2
    return factory.main(args, prog="book")


if __name__ == "__main__":
    sys.exit(main())
