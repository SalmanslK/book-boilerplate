#!/usr/bin/env python3
"""
The Book's command line: `python devtools/book.py <command> ...` - `book pr open`, `book impact`, `book deploy
check`, and the Book's own steps as `book verify`, `book init` and `book upgrade`.

It is the command devtools/factory/factory.py has always been, under its v1.4.0 name: this file only names it
and forwards. `python devtools/factory/factory.py <command>` keeps working until v1.5, and the hooks, the local
CI and the pipelines may run either.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

# devtools/factory first, so `import book` inside factory.py finds the Book module there, never this file.
sys.path.insert(0, str(Path(__file__).resolve().parent / "factory"))

import factory  # noqa: E402

# `factory book verify` is `book verify`: the Book's steps need no second "book".
BOOK_STEPS = ("verify", "init", "upgrade")


def arguments(argv: list[str]) -> list[str]:
    return ["book", *argv] if argv and argv[0] in BOOK_STEPS else list(argv)


# A project that kept its own factory.py through the v1.4.0 upgrade (a CONFLICT not merged yet) has a main() that
# takes no name: run it under its own, and say which file to merge.
NOT_MERGED = ("book: devtools/factory/factory.py predates Book v1.4.0 - merge it from template 1.4.0 (`book upgrade` "
              "lists it as a CONFLICT); running it as `factory` until then.")


def names_itself() -> bool:
    try:
        return "prog" in inspect.signature(factory.main).parameters
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = arguments(sys.argv[1:] if argv is None else argv)
    if not names_itself():
        print(NOT_MERGED, file=sys.stderr)
        return factory.main(args)
    return factory.main(args, prog="book")


if __name__ == "__main__":
    sys.exit(main())
