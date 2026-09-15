"""Regression pin: STATIC_DIR must not depend on the process CWD.

The demo web server used to build its static path as
``PROJECT_ROOT / "demo" / "web" / "static"``, and
``helperium_sdk.settings.project_root()`` derives PROJECT_ROOT from
``os.getcwd()``. Running the suite (or the server) from ``demo/web/``
therefore produced ``demo/web/demo/web/static`` and ``StaticFiles()``
aborted the whole app import with::

    RuntimeError: Directory '/.../demo/web/demo/web/static' does not exist

so every test importing ``demo.web.server`` failed during collection.

Contract pinned here: ``STATIC_DIR`` is an absolute path derived from
``server.py``'s own location, so importing the module works from any CWD.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from demo.web.server import STATIC_DIR

# demo/web/tests/unit/test_static_dir_resolution.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
SERVER_DIR = REPO_ROOT / "demo" / "web"


def test_static_dir_is_absolute():
    assert STATIC_DIR.is_absolute(), (
        f"STATIC_DIR {STATIC_DIR} must be absolute; a CWD-relative path "
        "breaks whenever the process starts elsewhere"
    )


def test_static_dir_points_at_frontend():
    assert STATIC_DIR.is_dir(), f"STATIC_DIR {STATIC_DIR} must exist"
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "app.js").is_file()


def test_import_works_from_unrelated_cwd(tmp_path):
    """The original failure: import with CWD outside the repo tree.

    Also asserts the resolved path is the module-relative one, so a fix
    that merely happens to work from ``demo/web/`` would still fail here.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, demo.web.server as s; print(json.dumps(str(s.STATIC_DIR)))",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, (
        "importing demo.web.server failed outside the repo root — "
        f"STATIC_DIR is CWD-dependent again:\n{result.stderr}"
    )
    resolved = Path(json.loads(result.stdout))
    assert resolved == SERVER_DIR / "static", (
        f"STATIC_DIR resolved to {resolved}, expected {SERVER_DIR / 'static'}"
    )
    assert resolved.is_dir()
