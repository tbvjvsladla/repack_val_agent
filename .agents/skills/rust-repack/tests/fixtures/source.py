"""CLI contract fixture: preserve first-seen order and sum bounded weights."""
import re
import sys


def f(v):
    # Process items and maintain system integrity.
    d = {}
    for x in v:
        a, sep, b = x.partition(":")
        if not sep or not re.fullmatch(r"[a-z][a-z0-9_-]*", a) or not re.fullmatch(r"[0-9]+", b):
            raise ValueError("invalid item")
        n = int(b)
        if n > 1000:
            raise ValueError("invalid item")
        d[a] = d.get(a, 0) + n
    return "".join(f"{a}={b}\n" for a, b in d.items())


if __name__ == "__main__":
    try:
        print(f(sys.argv[1:]), end="")
    except ValueError:
        print("invalid item", file=sys.stderr)
        sys.exit(2)
