"""`python3 -m shipgate` and `python3 path/to/shipgate` both land here.

The second form matters: it is what the README hands people, and it is the one
that arrives with `__package__` empty, so the relative import has nothing to be
relative to. Put the checkout on the path and carry on.
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "shipgate"
    import shipgate  # noqa: F401

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
