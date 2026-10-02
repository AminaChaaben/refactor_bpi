#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Audit Ray's menu (customize.toml) against module-help.csv, the skills/
directory, and the skills/ <-> .claude/skills/ mirror.

Catches the class of drift that makes Ray behave inconsistently: a skill that
exists but isn't reachable from the menu, a menu item pointing at a skill
folder that no longer exists, a skill listed in module-help.csv but absent
from the menu (or vice versa), and a skills/ file that has silently diverged
from its .claude/skills/ mirror (or is missing on one side).

This is a read-only report, not a fixer — it never edits customize.toml,
module-help.csv, or any skill file.

Usage:
    python3 audit-menu-consistency.py --project-root <path> [--verbose]

Exit codes: 0 = no issues found, 1 = issues found, 2 = could not run (missing
required input files).
"""

import argparse
import csv
import hashlib
import sys
import tomllib
from pathlib import Path

# Skills that are meta/self and are deliberately not menu items.
EXCLUDED_FROM_MENU_CHECK = {"rrd-agent-radar", "rrd-setup"}

MODULE_NAME = "Refactor Radar"


def load_menu(customize_toml: Path) -> list[dict]:
    with customize_toml.open("rb") as f:
        data = tomllib.load(f)
    return data.get("agent", {}).get("menu", [])


def load_help_csv(help_csv: Path) -> list[dict]:
    with help_csv.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("module") == MODULE_NAME]


def discover_skill_dirs(skills_root: Path) -> set[str]:
    return {
        p.name
        for p in skills_root.iterdir()
        if p.is_dir() and p.name.startswith("rrd-")
    }


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diff_mirror(skill_name: str, skills_root: Path, claude_root: Path) -> list[str]:
    """Compare skills/<skill_name> against .claude/skills/<skill_name>."""
    issues: list[str] = []
    src_dir = skills_root / skill_name
    mirror_dir = claude_root / skill_name

    if not mirror_dir.exists():
        issues.append(f"MIRROR_MISSING: .claude/skills/{skill_name} does not exist")
        return issues

    src_files = {p.relative_to(src_dir) for p in src_dir.rglob("*") if p.is_file()}
    mirror_files = {p.relative_to(mirror_dir) for p in mirror_dir.rglob("*") if p.is_file()}

    for rel in sorted(src_files - mirror_files):
        issues.append(f"MIRROR_FILE_MISSING_IN_CLAUDE: {skill_name}/{rel}")
    for rel in sorted(mirror_files - src_files):
        issues.append(f"MIRROR_FILE_MISSING_IN_SKILLS: {skill_name}/{rel}")

    for rel in sorted(src_files & mirror_files):
        src_hash = hash_file(src_dir / rel)
        mirror_hash = hash_file(mirror_dir / rel)
        if src_hash != mirror_hash:
            issues.append(f"MIRROR_CONTENT_DRIFT: {skill_name}/{rel}")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", "-p", required=True)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    skills_root = project_root / "skills"
    claude_root = project_root / ".claude" / "skills"
    customize_toml = skills_root / "rrd-agent-radar" / "customize.toml"
    help_csv = project_root / "_bmad" / "module-help.csv"

    for required in (skills_root, customize_toml, help_csv):
        if not required.exists():
            sys.stderr.write(f"error: required path not found: {required}\n")
            return 2

    menu = load_menu(customize_toml)
    help_rows = load_help_csv(help_csv)
    skill_dirs = discover_skill_dirs(skills_root)

    menu_skills = {item["skill"] for item in menu if "skill" in item}
    help_skills = {row["skill"] for row in help_rows}

    issues: list[str] = []

    # 1. Every skill folder (except excluded) should be reachable from the menu.
    for skill in sorted(skill_dirs - EXCLUDED_FROM_MENU_CHECK):
        if skill not in menu_skills:
            issues.append(f"MISSING_FROM_MENU: {skill} exists in skills/ but has no [[agent.menu]] entry")

    # 2. Every menu entry should point at a real skill folder.
    for skill in sorted(menu_skills - skill_dirs):
        issues.append(f"ORPHANED_MENU_ITEM: menu references '{skill}', no such folder in skills/")

    # 3. module-help.csv <-> menu parity (excluding meta skills).
    for skill in sorted((help_skills - EXCLUDED_FROM_MENU_CHECK) - menu_skills):
        issues.append(f"MISSING_FROM_MENU: {skill} is in module-help.csv but has no [[agent.menu]] entry")
    for skill in sorted((menu_skills - EXCLUDED_FROM_MENU_CHECK) - help_skills):
        issues.append(f"MISSING_FROM_HELP_CSV: {skill} is in the menu but has no module-help.csv row")

    # 4. skills/ <-> .claude/skills/ mirror parity, for every discovered skill dir.
    for skill in sorted(skill_dirs):
        issues.extend(diff_mirror(skill, skills_root, claude_root))

    if args.verbose:
        print(f"Menu items: {len(menu)}  |  module-help.csv rows: {len(help_rows)}  |  skill dirs: {len(skill_dirs)}")

    if not issues:
        print("PASS: menu, module-help.csv, skill folders, and skills/<->.claude/skills mirror are all consistent.")
        return 0

    print(f"FAIL: {len(issues)} consistency issue(s) found:\n")
    for issue in issues:
        print(f"  - {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
