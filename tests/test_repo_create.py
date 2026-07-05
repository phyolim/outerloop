# Self-contained: runs from anywhere. No DB/env needed — pure-function checks.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")

"""repo_slug must always emit a GitHub-safe name from an arbitrary ticket title, so a
ticket-controlled title can never reach the gh argv as anything but [a-z0-9-]."""
import re
from outerloop import git_ops

SAFE = re.compile(r"^[a-z0-9-]+$")


def slug(id_, title):
    return git_ops.repo_slug({"id": id_, "title": title})


def test_slug():
    # ordinary title -> readable, prefixed, id-scoped
    assert slug(7, "Add dark mode") == "inbox-7-add-dark-mode"
    # every hostile / empty title still yields a safe, non-empty name
    for title in ["", "   ", "!!!", "a/b; rm -rf ~", "Ünïcödé", "--leading--", "café ☕"]:
        s = slug(3, title)
        assert SAFE.match(s), f"unsafe slug {s!r} from {title!r}"
        assert s.startswith("inbox-3-")
    # empty-ish titles fall back to a name, never a bare prefix
    assert slug(3, "") == "inbox-3-project"
    assert slug(3, "!!!") == "inbox-3-project"
    print("ok")


if __name__ == "__main__":
    test_slug()
