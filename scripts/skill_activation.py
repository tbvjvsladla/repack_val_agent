#!/usr/bin/env python3
"""Turn opt-in skills on or off in this workspace.

An opt-in skill (SKILL.md frontmatter `metadata.activation: opt-in`) ships inactive:
its folder is under skills/, but it has no discovery links and no active config.
Activation is the user's choice on the receiving system.

    python3 scripts/skill_activation.py status
    python3 scripts/skill_activation.py enable <skill> [--apply]
    python3 scripts/skill_activation.py disable <skill> [--apply]

enable  lays .agents/skills/<skill> and .claude/skills/<skill> (relative symlinks) and
        copies <skill>/config.example.yaml to config.yaml when config.yaml is missing.
        Existing files are never overwritten. It then lists the skill's own setup steps
        (dependencies, keys, MCP registration), which it does not run.
disable removes only the two links. config.yaml, envs/ and installed environments stay.
Without --apply nothing is written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIRS = (".agents/skills", ".claude/skills")
OPT_IN = re.compile(r"^\s+activation:\s*[\"']?opt-in[\"']?\s*$", re.M)
# Setup that activation does not perform; shown after enable.
NEXT_STEPS = {
    "deepeval-pack": [
        "cd skills/deepeval-pack && uv sync --frozen",
        "put the Judge profile in envs/ (see envs/profile.example.yaml) and the provider key in envs/.env",
        "set provider and judge_profile in skills/deepeval-pack/config.yaml",
        "skills/deepeval-pack/.venv/bin/python -B skills/deepeval-pack/scripts/run.py check",
        "register the MCP server: skills/deepeval-pack/references/installation.md",
    ],
    "skillevaluator": [
        "install the SkillEvaluator CLI (skills/skillevaluator/SKILL.md, Prerequisites)",
        "put the Judge profile in envs/ (see envs/profile.example.yaml) and the provider key in envs/.env",
        "set provider and judge_profile in skills/skillevaluator/config.yaml",
        "python3 -B skills/skillevaluator/scripts/run.py check",
    ],
}


def opt_in_skills() -> list[str]:
    names = []
    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---\n") and OPT_IN.search(text[4:text.find("\n---", 4)]):
            names.append(skill_md.parent.name)
    return names


def state(skill: str) -> dict:
    links = {base: os.path.lexists(ROOT / base / skill) for base in ADAPTER_DIRS}
    config = ROOT / "skills" / skill / "config.yaml"
    return {"skill": skill, "active": all(links.values()), "links": links, "config_yaml": config.is_file()}


def enable(skill: str, apply: bool) -> dict:
    actions = []
    for base in ADAPTER_DIRS:
        link = ROOT / base / skill
        target = f"../../skills/{skill}"
        if os.path.lexists(link):
            ok = link.is_symlink() and os.readlink(link) == target
            actions.append({"path": f"{base}/{skill}", "state": "present" if ok else "exists-not-modified"})
            continue
        if apply:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
        actions.append({"path": f"{base}/{skill}", "state": "created" if apply else "would-create"})
    example = ROOT / "skills" / skill / "config.example.yaml"
    config = example.with_name("config.yaml")
    if example.is_file():
        if config.exists():
            actions.append({"path": f"skills/{skill}/config.yaml", "state": "kept"})
        else:
            if apply:
                shutil.copy2(example, config)
            actions.append({"path": f"skills/{skill}/config.yaml", "state": "created" if apply else "would-create"})
    return {"actions": actions, "next_steps": NEXT_STEPS.get(skill, [f"read skills/{skill}/SKILL.md"])}


def disable(skill: str, apply: bool) -> dict:
    actions = []
    for base in ADAPTER_DIRS:
        link = ROOT / base / skill
        if not os.path.lexists(link):
            actions.append({"path": f"{base}/{skill}", "state": "absent"})
        elif not (link.is_symlink() and os.readlink(link) == f"../../skills/{skill}"):
            actions.append({"path": f"{base}/{skill}", "state": "not-a-skill-link-not-modified"})
        else:
            if apply:
                link.unlink()
            actions.append({"path": f"{base}/{skill}", "state": "removed" if apply else "would-remove"})
    return {"actions": actions, "kept": ["config.yaml", "envs/", "installed environments"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "enable", "disable"])
    parser.add_argument("skill", nargs="?")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    skills = opt_in_skills()
    if args.command == "status":
        print(json.dumps({"opt_in": [state(s) for s in skills]}, ensure_ascii=False, indent=2))
        return 0
    if args.skill not in skills:
        parser.error(f"not an opt-in skill: {args.skill}; choose from {skills}")
    result = (enable if args.command == "enable" else disable)(args.skill, args.apply)
    out = {"mode": "apply" if args.apply else "dry-run", "command": args.command, "skill": args.skill, **result,
           "after": state(args.skill)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
