"""Guard: the Docker SDK contract test must target the real repository root.

Regression guard for the REPO_ROOT bug: parents[3] resolves to services/
(so infra/scripts/compose.sh is never found and the test silently SKIPS),
while the repository root is parents[4].

If this test fails, test_docker_sdk_version.py is skipping instead of
actually verifying SDK drift — the CI control is a no-op.
"""

import importlib.util
from pathlib import Path

_CONTRACT_DIR = Path(__file__).resolve().parent
_MODULE_PATH = _CONTRACT_DIR / "test_docker_sdk_version.py"

_spec = importlib.util.spec_from_file_location("test_docker_sdk_version", _MODULE_PATH)
sdk_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdk_contract)

REPO_MARKERS = ("go.work", "AGENTS.md", "Makefile")


def test_repo_root_points_at_repository_root():
    for marker in REPO_MARKERS:
        assert (sdk_contract.REPO_ROOT / marker).is_file(), (
            f"REPO_ROOT={sdk_contract.REPO_ROOT} does not look like the "
            f"repository root (missing {marker}). "
            "Fix: REPO_ROOT must be parents[4], not parents[3]."
        )


def test_compose_sh_target_exists():
    assert sdk_contract.COMPOSE_SH.is_file(), (
        f"compose.sh not found at {sdk_contract.COMPOSE_SH} — "
        "the SDK version contract test will SKIP and never catch SDK drift. "
        "Fix REPO_ROOT in test_docker_sdk_version.py."
    )
