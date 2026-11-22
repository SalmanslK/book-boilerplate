# Architecture history

Superseded decisions and finished rows from `docs/architecture.md`, moved here when a newer
decision replaces them. Written once, never maintained; nothing here is a current rule.

## Known trade-offs, replaced by v1.5

| Trade-off | Why we accepted it |
|---|---|
| The Book CLI is `book` (`devtools/book.py`), and `devtools/factory/factory.py` keeps working as `factory` until v1.5 | Projects on an older Book, their pipelines and their hooks still call `factory.py`; the alias goes in its own release ([the v1.5 plan](plans/book-v1.5.md)). The module stays `devtools/factory/factory.py`. In a Book + Factory install the referee goes to `devtools/ai-factory/` |
