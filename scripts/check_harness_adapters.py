#!/usr/bin/env python3
"""Check the inner-harness-agnostic layout of this workspace.

Three static checks (see rules/harness.md):
  wiring    AGENTS.md links the canonical rules; CLAUDE.md imports AGENTS.md;
            every canonical skill is reachable from .agents/skills and
            .claude/skills through a directory symlink, and nothing else is there.
  wording   canonical/entry files carry no harness-hardcoded role wording, and a
            harness-specific tool name is only used next to the other harness's
            equivalent (a paired example).
  old-path  live files no longer point at the former canonical paths.

Before checking the workspace the script runs a negative control on a synthetic
tree: each check must fail on a seeded defect and pass on a clean copy.
Static success does not prove that a harness actually discovers the files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import re
import subprocess
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RULES = ("workflow.md", "docs.md", "harness.md")
ADAPTER_DIRS = (".agents/skills", ".claude/skills")

BANNED = [
    re.compile(p) for p in (
        r"Codex가 지휘", r"Codex 지휘",
        r"Codex 도구", r"Codex에서 사용하기", r"Codex (세션 정책|실행 환경|정책을)",
        r"대화형 Codex 호스트", r"Claude Code 전용 지침", r"(host|adaptation): \"Codex",
        r"Claude가 지휘", r"Claude\s+(subagent|서브에이전트)만",
    )
]
# Single-harness phrasing that is acceptable only as a paired example.
PAIRABLE = re.compile(r"Codex\s+(subagent|서브에이전트)")
# Host-specific tool names that need a paired example from the other harness.
CODEX_TOOLS = re.compile(r"exec_command|write_stdin|spawn_agent|(?<![\w\"])\$[a-z][a-z0-9]*-[a-z0-9-]+")
CLAUDE_TOOLS = re.compile(r"Agent tool|Agent 도구|run_in_background")
OLD_PATH = re.compile(r"\.codex/(rules|specs)|\.agents/skills/[a-z0-9-]+")
# Provenance lines keep the history of earlier adaptations.
PROVENANCE_LINE = re.compile(r"(Adapted|Derived) from|[Mm]odified .*for Codex|개정|전환 전|최초 설치 당시")
PROVENANCE_FILES = re.compile(r"(^|/)references/(adaptation|codex)\.md$")
# A line may name a discovery adapter path only while describing it as one
# (symlink/discovery path) or naming both harness adapters together.
ADAPTER_CONTEXT = re.compile(r"심볼릭|symlink|발견 경로|탐색 경로|\.agents/skills.*\.claude/skills|\.claude/skills.*\.agents/skills")


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    fields = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields


def check_wiring(root: Path) -> list[str]:
    errors = []
    agents = root / "AGENTS.md"
    if not agents.is_file():
        return ["AGENTS.md missing"]
    text = agents.read_text(encoding="utf-8")
    for name in RULES:
        if f"(rules/{name})" not in text:
            errors.append(f"AGENTS.md does not link rules/{name}")
        if not (root / "rules" / name).is_file():
            errors.append(f"rules/{name} missing")
    claude = root / "CLAUDE.md"
    if not claude.is_file() or "@AGENTS.md" not in claude.read_text(encoding="utf-8").split():
        errors.append("CLAUDE.md missing or does not import @AGENTS.md")
    skills = sorted(p.parent.name for p in (root / "skills").glob("*/SKILL.md"))
    if not skills:
        errors.append("no canonical skills under skills/")
    for name in skills:
        fm = frontmatter((root / "skills" / name / "SKILL.md").read_text(encoding="utf-8"))
        if fm.get("name") != name or not fm.get("description"):
            errors.append(f"skills/{name}/SKILL.md frontmatter needs name={name} and description")
    for base in ADAPTER_DIRS:
        adapter_dir = root / base
        present = sorted(p.name for p in adapter_dir.iterdir()) if adapter_dir.is_dir() else []
        for extra in sorted(set(present) - set(skills)):
            errors.append(f"{base}/{extra} has no canonical skill")
        for name in skills:
            link = adapter_dir / name
            if not link.is_symlink():
                errors.append(f"{base}/{name} is not a symlink")
            elif os.readlink(link) != f"../../skills/{name}" or not (link / "SKILL.md").is_file():
                errors.append(f"{base}/{name} must be the relative symlink ../../skills/{name}")
    errors += check_tracked_links(root, skills)
    return errors


def check_tracked_links(root: Path, skills: list[str]) -> list[str]:
    """In a Git work tree every adapter must be committed as a symlink (mode 120000)."""
    probe = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
    if probe.returncode or Path(probe.stdout.strip()).resolve() != root.resolve():
        return []  # not a repository root (e.g. an export candidate): nothing to compare
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "--", *ADAPTER_DIRS],
                            capture_output=True, text=True, check=True).stdout
    modes = {line.split("\t", 1)[1]: line.split()[0] for line in listed.splitlines() if "\t" in line}
    return [f"{base}/{name} is not tracked by Git as a symlink"
            for base in ADAPTER_DIRS for name in skills
            if modes.get(f"{base}/{name}") != "120000"]


def live_files(root: Path) -> list[Path]:
    files = [root / n for n in ("AGENTS.md", "CLAUDE.md", "README.md", "PUBLICATION.md", "THIRD_PARTY_NOTICES.md")]
    files += sorted((root / "rules").glob("*.md"))
    files += sorted((root / "docs").glob("*/doc_gen_guide.md"))
    files += [root / "campaigns" / "README.md"]
    files += sorted(p for p in (root / "skills").rglob("*")
                    if p.is_file() and p.suffix in {".md", ".yaml", ".yml", ".ts", ".mjs", ".js", ".py", ".json"}
                    and p.name != "PACKAGE_MANIFEST.json"
                    and "fixtures" not in p.parts and "tests" not in p.parts)
    files += sorted(p for p in (root / "scripts").glob("*.py") if p.name != Path(__file__).name)
    return [p for p in files if p.is_file()]


def check_text(root: Path) -> tuple[list[str], list[str]]:
    wording, old = [], []
    for path in live_files(root):
        rel = path.relative_to(root).as_posix()
        provenance_file = bool(PROVENANCE_FILES.search(rel))
        harness_adapter = rel.endswith("agents/openai.yaml")  # Codex-only metadata by definition
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if provenance_file or PROVENANCE_LINE.search(line):
                continue
            where = f"{rel}:{n}"
            # The adapter table in rules/harness.md is where per-harness names belong.
            adapter_table = rel == "rules/harness.md" and line.startswith("|")
            if not (harness_adapter or adapter_table):
                for pattern in BANNED:
                    if pattern.search(line):
                        wording.append(f"{where}: harness-hardcoded wording /{pattern.pattern}/")
                if PAIRABLE.search(line) and "Claude" not in line:
                    wording.append(f"{where}: single-harness subagent wording without a pairing")
                if CODEX_TOOLS.search(line) and "Claude" not in line:
                    wording.append(f"{where}: Codex-specific tool without a Claude Code pairing")
                if CLAUDE_TOOLS.search(line) and "Codex" not in line:
                    wording.append(f"{where}: Claude Code-specific tool without a Codex pairing")
            if OLD_PATH.search(line) and not ADAPTER_CONTEXT.search(line):
                old.append(f"{where}: former canonical path")
    return wording, old


def run(root: Path) -> dict[str, list[str]]:
    wording, old = check_text(root)
    return {"wiring": check_wiring(root), "wording": wording, "old-path": old}


def negative_control() -> list[str]:
    """Each check must pass on a clean synthetic tree and fail on its seeded defect."""
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean"
        (clean / "rules").mkdir(parents=True)
        for name in RULES:
            (clean / "rules" / name).write_text("# rule\n", encoding="utf-8")
        (clean / "AGENTS.md").write_text("".join(f"- [r](rules/{n})\n" for n in RULES), encoding="utf-8")
        (clean / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        skill = clean / "skills" / "demo-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo\n---\nRun in the host's subagent (Codex subagents or the Claude Code Agent tool).\n",
            encoding="utf-8")
        for base in ADAPTER_DIRS:
            (clean / base).mkdir(parents=True)
            (clean / base / "demo-skill").symlink_to("../../skills/demo-skill", target_is_directory=True)
        if any(run(clean).values()):
            problems.append(f"clean tree did not pass: {run(clean)}")
        seeds = {
            "wiring": lambda r: ((r / ".claude/skills/demo-skill").unlink(),
                                 (r / ".claude/skills/demo-skill").symlink_to("../../skills/missing")),
            "wording": lambda r: (r / "skills/demo-skill/SKILL.md").open("a", encoding="utf-8").write(
                "이 스킬을 읽는 Codex가 지휘자다. Use `exec_command` only.\n"),
            "old-path": lambda r: (r / "rules/docs.md").open("a", encoding="utf-8").write(
                "See .codex/rules/workflow.md and .agents/skills/demo-skill/SKILL.md.\n"),
            "absolute-link": lambda r: ((r / ".claude/skills/demo-skill").unlink(),
                                        (r / ".claude/skills/demo-skill").symlink_to(r / "skills/demo-skill")),
            "old-path-bare": lambda r: (r / "skills/demo-skill/SKILL.md").open("a", encoding="utf-8").write(
                "정본 링크는 `.agents/skills/demo-skill`이다.\n"),
            "old-path-code": lambda r: (r / "skills/demo-skill/cli.ts").write_text(
                "// usage: node .agents/skills/demo-skill/cli.ts\n", encoding="utf-8"),
        }
        target = {"absolute-link": "wiring", "old-path-bare": "old-path", "old-path-code": "old-path"}
        for check, seed in seeds.items():
            case = Path(tmp) / check
            shutil.copytree(clean, case, symlinks=True)
            seed(case)
            if not run(case)[target.get(check, check)]:
                problems.append(f"seeded {check} defect was not detected")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    control = negative_control()
    result = run(args.root.resolve()) if not control else {}
    ok = not control and not any(result.values())
    print(json.dumps({"status": "PASS" if ok else "FAIL", "negative_control": control or "PASS",
                      **{k: v or "PASS" for k, v in result.items()}}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
