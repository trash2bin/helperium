"""Tests for scripts/cleanup_stale_tenants.py (dry-run, deletion, safety)."""

import os
import time

import pytest

from cleanup_stale_tenants import find_stale_files, main


@pytest.fixture
def tenants_dir(tmp_path):
    d = tmp_path / "tenants"
    d.mkdir()
    return d


def _touch(path, age_days: float, size: int = 10):
    path.write_bytes(b"x" * size)
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def _age_files(*paths, age_days: float):
    old = time.time() - age_days * 86400
    for p in paths:
        os.utime(p, (old, old))


def test_find_stale_only_e2e_files(tenants_dir):
    old = tenants_dir / "e2e-abc123.json"
    _touch(old, age_days=30)
    fresh = tenants_dir / "e2e-fresh.json"
    _touch(fresh, age_days=0.1)
    real = tenants_dir / "autoparts.json"
    _touch(real, age_days=30)
    schema = tenants_dir / "e2e-abc123.schema.json"
    _touch(schema, age_days=30)

    stale = find_stale_files(7, tenants_dir)
    names = {f.name for f in stale}
    assert names == {"e2e-abc123.json", "e2e-abc123.schema.json"}


def test_find_stale_ignores_subdirs_and_missing_dir(tmp_path):
    assert find_stale_files(7, tmp_path / "nope") == []
    nested = tmp_path / "tenants" / "sub"
    nested.mkdir(parents=True)
    old = nested / "e2e-nested.json"
    _touch(old, age_days=30)
    # Files in subdirectories are never considered (no recursion).
    assert find_stale_files(7, nested.parent) == []


def test_symlink_escaping_dir_is_ignored(tenants_dir, tmp_path):
    outside = tmp_path / "outside.txt"
    _touch(outside, age_days=30)
    link = tenants_dir / "e2e-link.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported on this filesystem")
    assert find_stale_files(7, tenants_dir) == []


def test_dry_run_does_not_delete(tenants_dir, capsys):
    f = tenants_dir / "e2e-old.json"
    _touch(f, age_days=30)
    rc = main(["--days", "1", "--tenants-dir", str(tenants_dir)])
    assert rc == 0
    assert f.exists()
    out = capsys.readouterr().out
    assert "Dry-run" in out
    assert "e2e-old.json" in out


def test_delete_removes_stale_only(tenants_dir, capsys):
    old = tenants_dir / "e2e-old.json"
    _touch(old, age_days=30)
    keep = tenants_dir / "e2e-new.json"
    _touch(keep, age_days=0.1)
    real = tenants_dir / "autoparts.json"
    _touch(real, age_days=30)

    rc = main(["--delete", "--days", "1", "--tenants-dir", str(tenants_dir)])
    assert rc == 0
    assert not old.exists()
    assert keep.exists()
    assert real.exists()
    assert "Deleted 1/1" in capsys.readouterr().out


def test_negative_days_rejected():
    with pytest.raises(SystemExit):
        main(["--days", "-1"])


def test_zero_days_rejected():
    with pytest.raises(SystemExit):
        main(["--days", "0"])


def test_non_numeric_days_rejected():
    with pytest.raises(SystemExit):
        main(["--days", "soon"])
