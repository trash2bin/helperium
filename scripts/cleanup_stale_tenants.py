#!/usr/bin/env python3
"""Cleanup stale e2e-* tenant files from .data/tenants/.

E2E tests create temporary tenant configs (e2e-*.json + e2e-*.schema.json).
The pytest fixtures clean up after themselves, but stale files accumulate
from failed/interrupted runs. This script removes e2e-* files older than
TTL_DAYS (default: 7).

Safe-to-delete artifacts: ONLY files directly inside the tenants directory
(no recursion) whose name starts with ``e2e-``. Real tenants (autoparts,
default, sqlite-demo, ...) never match. Paths are re-validated with
resolve() containment before deletion.

Usage:
    python3 scripts/cleanup_stale_tenants.py              # dry-run (show what would be deleted)
    python3 scripts/cleanup_stale_tenants.py --delete     # actually delete
    python3 scripts/cleanup_stale_tenants.py --days 1     # custom TTL
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TENANTS_DIR = REPO_ROOT / ".data" / "tenants"
DEFAULT_TTL_DAYS = 7.0


def days_arg(value: str) -> float:
    """argparse type: TTL in days, must be > 0."""
    try:
        days = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if days <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return days


def find_stale_files(ttl_days: float, tenants_dir: Path) -> list[Path]:
    """Return e2e-* files older than ttl_days.

    Only regular files directly inside tenants_dir (no recursion). Symlinks
    that escape the directory are rejected via resolve() containment.
    """
    if not tenants_dir.is_dir():
        return []
    try:
        dir_real = tenants_dir.resolve()
    except OSError:
        return []

    cutoff = time.time() - ttl_days * 86400
    stale: list[Path] = []
    for f in tenants_dir.iterdir():
        if not f.is_file():
            continue
        if not f.name.startswith("e2e-"):
            continue
        # Containment: resolved path must stay inside the tenants dir.
        try:
            if not f.resolve().is_relative_to(dir_real):
                continue
        except OSError:
            continue
        try:
            if f.stat().st_mtime < cutoff:
                stale.append(f)
        except OSError:
            continue
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete files (default: dry-run)",
    )
    parser.add_argument(
        "--days",
        type=days_arg,
        default=DEFAULT_TTL_DAYS,
        help=f"TTL in days, > 0 (default: {DEFAULT_TTL_DAYS:g})",
    )
    parser.add_argument(
        "--tenants-dir",
        type=Path,
        default=DEFAULT_TENANTS_DIR,
        help=argparse.SUPPRESS,  # for tests
    )
    args = parser.parse_args(argv)

    tenants_dir: Path = args.tenants_dir
    stale = find_stale_files(args.days, tenants_dir)
    if not stale:
        print(f"✅ No stale e2e-* files older than {args.days:g} days in {tenants_dir}")
        return 0

    total_bytes = sum(f.stat().st_size for f in stale)
    print(
        f"Found {len(stale)} stale e2e-* files "
        f"({total_bytes / 1024:.1f} KB) older than {args.days:g} days:"
    )
    for f in sorted(stale):
        print(f"  {f.name}")

    if not args.delete:
        print("\nDry-run mode. Pass --delete to remove these files.")
        return 0

    deleted = 0
    for f in stale:
        try:
            f.unlink()
            deleted += 1
        except OSError as e:
            print(f"  ⚠️  failed to delete {f.name}: {e}", file=sys.stderr)
    print(f"\n🗑️  Deleted {deleted}/{len(stale)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
