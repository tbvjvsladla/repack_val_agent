"""Compare real CLI bytes and exit codes against immutable source fixtures.

Usage: python3 verify_contract.py python|javascript /path/to/candidate-binary
Candidates are generated separately by an agent using the installed skill.
"""
import json
from pathlib import Path
import random
import subprocess
import sys


def main():
    language, binary = sys.argv[1:]
    fixtures = Path(__file__).resolve().parent / "fixtures"
    randomizer = random.Random(42)
    if language == "python":
        source = [sys.executable, str(fixtures / "source.py")]
        cases = [[], ["b:2", "a:1", "b:3"], ["a:0"], ["a:1000"], ["a:1001"],
                 ["a:-1"], ["a:+1"], ["a: 1"], ["a:1:2"], ["é:1"], ["a:1\n"], ["a:0002"]]
        cases += [[f"{randomizer.choice('abc')}:{randomizer.randrange(1001)}" for _ in range(20)] for _ in range(20)]
    elif language == "javascript":
        source = ["node", str(fixtures / "source.mjs")]
        cases = [[], ["b=2", "a=2", "c=3"], ["a=1", "a=1"], ["a=0"], ["a=9"],
                 ["a=10"], ["a=-1"], ["a=2\n"], ["é=2"], ["a==2"]]
        cases += [[f"{randomizer.choice('abc')}={randomizer.randrange(10)}" for _ in range(20)] for _ in range(20)]
    else:
        raise ValueError("expected python or javascript")
    failures = []
    for index, args in enumerate(cases):
        original = subprocess.run(source + args, capture_output=True, timeout=10)
        candidate = subprocess.run([binary] + args, capture_output=True, timeout=10)
        expected = (original.returncode, original.stdout, original.stderr)
        actual = (candidate.returncode, candidate.stdout, candidate.stderr)
        if actual != expected:
            failures.append({"case": index, "args": args, "original": repr(expected), "candidate": repr(actual)})
    print(json.dumps({"language": language, "cases": len(cases), "failures": failures}, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
