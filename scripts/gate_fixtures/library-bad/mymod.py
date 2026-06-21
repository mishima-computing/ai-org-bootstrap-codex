"""A library MISSING a symbol its contract claims (no `baz`). The import-probe must catch the missing export."""

VERSION = "1.0"


def foo():
    return "foo"


def bar(x):
    return x + 1
