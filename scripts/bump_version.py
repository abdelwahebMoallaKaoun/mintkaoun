#!/usr/bin/env python3
"""Bump the project version across all files, update the changelog, and tag.

Single source of truth is ``mint/__init__.py`` (``__version__``), which flit
reads via the ``dynamic = ["version"]`` entry in ``pyproject.toml``. This
script keeps the two ``package.json`` files in sync with it, moves the
``[Unreleased]`` changelog entries under the new version, and (unless
``--no-git``) creates a release commit and an annotated ``v<version>`` tag.

Usage:
    python scripts/bump_version.py patch          # 1.5.3 -> 1.5.4
    python scripts/bump_version.py minor          # 1.5.3 -> 1.6.0
    python scripts/bump_version.py major          # 1.5.3 -> 2.0.0
    python scripts/bump_version.py 2.1.0          # set an explicit version
    python scripts/bump_version.py patch --no-git # edit files only, no commit/tag
    python scripts/bump_version.py patch --dry-run

Run from the repository root.
"""
from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INIT_PY = ROOT / "mint" / "__init__.py"
ROOT_PKG = ROOT / "package.json"
FRONTEND_PKG = ROOT / "frontend" / "package.json"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def read_current_version() -> str:
    text = INIT_PY.read_text()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        fail(f"could not find __version__ in {INIT_PY}")
    return match.group(1)


def compute_new_version(current: str, bump: str) -> str:
    if SEMVER_RE.match(bump):
        return bump  # explicit version

    m = SEMVER_RE.match(current)
    if not m:
        fail(f"current version {current!r} is not semver (major.minor.patch)")
    major, minor, patch = (int(x) for x in m.groups())

    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    fail(f"unknown bump {bump!r}; use major|minor|patch or an explicit X.Y.Z")


def repo_url() -> str:
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return ""
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    return url[:-4] if url.endswith(".git") else url


def replace_in_file(path: Path, pattern: str, replacement: str, dry_run: bool) -> None:
    text = path.read_text()
    new_text, count = re.subn(pattern, replacement, text, count=1)
    if count == 0:
        fail(f"pattern not found in {path}")
    print(f"  {path.relative_to(ROOT)}")
    if not dry_run:
        path.write_text(new_text)


def update_changelog(current: str, new: str, dry_run: bool) -> None:
    text = CHANGELOG.read_text()
    today = datetime.date.today().isoformat()

    # Split the [Unreleased] notes out and start a fresh, empty Unreleased.
    marker = "## [Unreleased]"
    if marker not in text:
        fail(f"'{marker}' section not found in {CHANGELOG}")

    new_section = (
        f"{marker}\n\n"
        f"## [{new}] - {today}"
    )
    text = text.replace(marker, new_section, 1)

    # Refresh the link reference definitions at the bottom of the file.
    base = repo_url()
    if base:
        text = re.sub(
            r"\[Unreleased\]:.*",
            f"[Unreleased]: {base}/compare/v{new}...HEAD",
            text,
            count=1,
        )
        new_link = f"[{new}]: {base}/compare/v{current}...v{new}"
        text = re.sub(
            r"(\[Unreleased\]:.*\n)",
            rf"\1{new_link}\n",
            text,
            count=1,
        )

    print(f"  {CHANGELOG.relative_to(ROOT)}")
    if not dry_run:
        CHANGELOG.write_text(text)


def git(*args: str, dry_run: bool) -> None:
    print("  git " + " ".join(args))
    if not dry_run:
        subprocess.check_call(["git", *args], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bump", help="major | minor | patch | an explicit X.Y.Z")
    parser.add_argument("--no-git", action="store_true", help="edit files only; skip commit and tag")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    args = parser.parse_args()

    current = read_current_version()
    new = compute_new_version(current, args.bump)
    if new == current:
        fail(f"new version equals current version ({current})")

    print(f"Bumping {current} -> {new}\n")
    print("Updating files:")
    replace_in_file(
        INIT_PY,
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{new}"',
        args.dry_run,
    )
    for pkg in (ROOT_PKG, FRONTEND_PKG):
        replace_in_file(
            pkg,
            r'"version":\s*"[^"]+"',
            f'"version": "{new}"',
            args.dry_run,
        )
    update_changelog(current, new, args.dry_run)

    if not args.no_git:
        print("\nGit:")
        git("add", "-A", dry_run=args.dry_run)
        git("commit", "-m", f"chore: release v{new}", dry_run=args.dry_run)
        git("tag", "-a", f"v{new}", "-m", f"Release v{new}", dry_run=args.dry_run)
        print(f"\nDone. Push with:  git push && git push origin v{new}")
    else:
        print("\nDone (files only). Review, then commit and tag manually.")


if __name__ == "__main__":
    main()
