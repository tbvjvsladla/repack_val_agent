"""Inputs and trap expectations for the acceptance fixture.

Shared by judge_acceptance.py and the fixture tests. Never given to the executor.
"""
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SEEDED = TESTS_DIR / "fixtures" / "acceptance" / "seeded"
VARIANTS = TESTS_DIR / "fixtures" / "acceptance" / "variants"
GATE = SKILL_DIR / "scripts" / "slop_gate.py"

PROJECT_DIR = "project"
RUN_DIR = "runs/run-1"

ALLOWED = ["docs", "node/src", "python/app", "rust/src", "scripts/build.sh"]
FIX_TYPES = ["banner-comment", "commented-code", "lint-suppression", "swallowed-error"]
CARGO_CHECK = 'PATH="$HOME/.cargo/bin:$PATH" cargo test --offline --manifest-path rust/Cargo.toml'
CHECKS = [
    CARGO_CHECK,
    "python3 -m unittest discover -s python/tests -t python",
    "node --test 'node/**/*.test.*'",
    "bash scripts/check.sh",
]

# Files the executor must leave byte-identical (tests, check script, out-of-scope slop, undesignated prose).
UNCHANGED = ["legacy", "rust/tests", "python/tests", "node/test", "scripts/check.sh", "docs", "config",
             "rust/Cargo.toml", "rust/Cargo.lock", "node/package.json", ".gitignore"]

# T5: suppressions that must stay, each with a justified_keep disposition.
T5 = {"rule": "RS-LINT-SUPPRESSION", "file": "rust/src/proto.rs", "contains": "non_snake_case", "count": 2}

# T7: slop only a reviewer can find. Observation only, never a pass condition.
T7 = [
    {"file": "node/src/retry.js", "contains": "increment the retry counter", "token": "retry counter"},
    {"file": "python/app/ledger.py", "contains": "def _format", "token": "_format"},
    {"file": "python/app/ledger.py", "contains": "def export_done", "token": "export_done"},
]

FORBIDDEN_SUBSTRINGS = ["repack_val_agent", "repack-val-agent", "ws_docker"]
