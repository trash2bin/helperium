"""Contract tests for the native stand run scripts (§5, §6).

The declared contract lives in ``scripts/stress/stand.env``: the budgets in
effect on the stand have to reach the harness, because reading only its own
environment prints the documented defaults next to a stand whose limiter was
raised - the da85632e run recorded ``CHAT_RATE_LIMIT=30/minute`` with
``caps_the_ladder: true`` while the stand actually ran 60000/minute. The same
run recorded ``api_workers: 1`` because nothing passed the worker count the
stand was launched with.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts" / "stress"
RUN_SCRIPTS = ("run-l1.sh", "run-l1-high.sh", "run-l1-relieved.sh", "run-l2.sh", "run-l2-realistic.sh", "run-l3.sh")

# The stand's raised limits, declared in stand.env and repeated by the run
# scripts so the manifest records what the services actually ran with.
STAND_BUDGETS = (
    "MCP_RATE_LIMIT_RPS",
    "MCP_RATE_LIMIT_BURST",
    "ABUSE_IP_RPS",
    "ABUSE_IP_BURST",
    "ABUSE_MAX_USER_TURNS",
    "CHAT_RATE_LIMIT",
)


def script_text(name: str) -> str:
    path = SCRIPTS / name
    assert path.exists(), f"{path} is missing: the run scripts are the declared way to drive the stand"
    return path.read_text()


@pytest.mark.parametrize("name", RUN_SCRIPTS)
def test_every_run_script_hands_the_stand_budgets_to_the_harness(name):
    text = script_text(name)
    for key in STAND_BUDGETS:
        assert f'--budget "{key}=$' in text, (
            f"{name} does not pass --budget {key}=...: the harness then records "
            "the documented default next to a stand whose limiter was raised (§5, "
            "the da85632e trap)"
        )


@pytest.mark.parametrize("name", RUN_SCRIPTS)
def test_every_run_script_declares_the_workers_it_launched_with(name):
    text = script_text(name)
    assert "--api-workers" in text, (
        f"{name} does not pass --api-workers: the manifest then records the "
        "hardcoded default (1) whatever the stand actually ran (the da85632e trap)"
    )
