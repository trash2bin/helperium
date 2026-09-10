"""
Contract test: Docker image SDK version must match mounted code SDK version.

Prevents 'DemoSettings has no attribute X' AttributeError when SDK schemas drift.

WHY the image, not a running container:
  - The api service does NOT mount the source tree (only app_data:/data/app),
    so a running api container is exactly its image — checking the image is
    equivalent and works in BOTH environments:
      * CI:   compose --profile test up -d api (containers exist);
      * local `make ci-e2e`: dev.sh e2e-up runs a NATIVE process stack and
        never creates an api container — the old container-based check could
        therefore never pass locally (silent skip / strict false-fail).
  - `make ci-e2e` rebuilds helperium-api:latest right before this test, so a
    mismatch here means the build produced a stale image.

Skip policy: preconditions (compose.sh present, Docker daemon, image built)
are skipped in normal runs. Set CONTRACT_STRICT=1 (done by `make ci-e2e`)
to turn every skip into a FAILURE — an E2E pass that silently skips the
drift control is worthless.
"""

import os
import subprocess
from pathlib import Path

import pytest


def _skip_or_fail(reason: str):
    """Skip in normal runs; fail when CONTRACT_STRICT=1 (make ci-e2e)."""
    if os.environ.get("CONTRACT_STRICT") == "1":
        pytest.fail(f"CONTRACT_STRICT: {reason}")
    pytest.skip(reason)


# parents[4]: contract/ -> tests -> agent-db -> services -> REPO ROOT.
# parents[3] is services/, where infra/scripts/compose.sh does NOT exist,
# which made this test silently SKIP forever (guarded by test_contract_paths.py).
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE_SH = REPO_ROOT / "infra" / "scripts" / "compose.sh"
# Must match `image:` for the api service in infra/docker-compose.yml.
API_IMAGE = "helperium-api:latest"


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT
    )


def test_sdk_version_in_docker_matches_code():
    """
    Fail fast if helperium-sdk version in the api image != mounted code version.

    Fix on mismatch: make ci-e2e (rebuilds test-profile images first).
    """
    # 1. SDK version in mounted code
    try:
        import importlib.metadata

        code_version = importlib.metadata.version("helperium-sdk")
    except importlib.metadata.PackageNotFoundError:
        _skip_or_fail("helperium-sdk not installed in test environment")

    # 2. compose.sh present (repo layout sanity)
    if not COMPOSE_SH.is_file():
        _skip_or_fail(f"compose.sh not found at {COMPOSE_SH}")

    # 3. Docker daemon reachable
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, check=True, timeout=10
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        _skip_or_fail("Docker not available or not running")

    # 4. api image built (make ci-e2e / compose --profile test build)
    inspect = _run(["docker", "image", "inspect", API_IMAGE], timeout=30)
    if inspect.returncode != 0:
        _skip_or_fail(
            f"image {API_IMAGE} not built yet "
            f"(run: make ci-e2e or ./infra/scripts/compose.sh --profile test build)"
        )

    # 5. SDK version inside the image — entrypoint override, no deps started
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            API_IMAGE,
            "-c",
            "import importlib.metadata; print(importlib.metadata.version('helperium-sdk'))",
        ]
    )
    if result.returncode != 0:
        pytest.fail(
            f"Failed to get SDK version from image {API_IMAGE}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    docker_version = result.stdout.strip()

    # 6. Compare
    if code_version != docker_version:
        pytest.fail(
            f"\n"
            f"❌ SDK version mismatch detected!\n"
            f"\n"
            f"  Code version:   {code_version}\n"
            f"  Image version:  {docker_version} ({API_IMAGE})\n"
            f"\n"
            f"This causes AttributeError when schemas drift.\n"
            f"Fix: make ci-e2e (rebuilds test-profile images before E2E).\n"
        )

    print(f"✅ SDK versions match: {code_version}")
