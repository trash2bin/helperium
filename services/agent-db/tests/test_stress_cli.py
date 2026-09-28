"""CLI contract tests (§6, §10).

The CLI is the one place where the harness meets an operator, so what is pinned
here is the interface rather than the maths (the ladder and the records own that):
the commands exist, the layout on disk is the one the report points at, a missing
stand is refused instead of measured, and a run leaves enough behind that someone
who was not present can check the numbers.

Everything runs against a scripted connection, so there is no network and no
clock dependence: the point is the wiring, and the ladder already has tests with
an injected clock.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_db.stress import cli as stress_cli

L1_PROFILE = (
    Path(__file__).resolve().parents[1]
    / "agent_db"
    / "stress"
    / "profiles"
    / "mcp-tool-call-l1.json"
)


@dataclass
class FakeResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def read(self) -> bytes:
        return self.body


@dataclass
class FakeStand:
    """The scripted stand: its config and every request it saw."""

    tools: list[str] = field(default_factory=lambda: ["db_map", "db_get", "db_search"])
    requests: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)
    tool_is_error: bool = False
    fail_manifest: bool = False

    def connection(self) -> FakeConnection:
        return FakeConnection(stand=self)  # one connection per MCP session


@dataclass
class FakeConnection:
    """The MCP subset the harness uses: handshake, manifest, tool call.

    One instance per session, because the pool opens a session per worker and
    the workers run concurrently: a shared response slot would hand one worker
    another worker's body.
    """

    stand: FakeStand
    response: FakeResponse | None = None

    def request(self, method, url, body=None, headers=None):  # noqa: ANN001
        parsed = json.loads(body.decode()) if body else None
        self.stand.requests.append((method, url, parsed))
        if method == "GET":
            self.response = (
                FakeResponse(500, {"Content-Type": "application/json"}, b"{}")
                if self.stand.fail_manifest
                else FakeResponse(
                    200,
                    {"Content-Type": "application/json"},
                    json.dumps(
                        {"mcp_tools": [{"name": n} for n in self.stand.tools]}
                    ).encode(),
                )
            )
            return
        payload = parsed or {}
        if payload.get("method") == "initialize":
            self.response = FakeResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps({"jsonrpc": "2.0", "id": 0, "result": {}}).encode(),
            )
        elif payload.get("method") == "notifications/initialized":
            self.response = FakeResponse(202, {}, b"")
        else:
            result: dict[str, Any] = {"content": [{"type": "text", "text": "ok"}]}
            if self.stand.tool_is_error:
                result["isError"] = True
            self.response = FakeResponse(
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                ).encode(),
            )

    def getresponse(self) -> FakeResponse:
        assert self.response is not None, "getresponse before request"
        return self.response

    def close(self) -> None:
        return


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal clean checkout, because §6 attributes numbers to a commit."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("stress stand\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "user.email", "stress@example.invalid")
    git("config", "user.name", "stress harness")
    git("add", "README.md")
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return root


def _profile(tmp_path: Path, *, tenants: int = 2, rates: str = "5,10") -> Path:
    payload = json.loads(L1_PROFILE.read_text(encoding="utf-8"))
    payload["tenants"]["count"] = tenants
    payload["arrival"] = {"model": "constant", "rps": 5, "duration_s": 2, "warmup_s": 0.5}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(stress_cli.app, args, catch_exceptions=False)


class TestTenantResolution:
    def test_auto_names_match_the_declared_count(self):
        from agent_db.stress.profile import load_profile

        profile = load_profile(L1_PROFILE)  # 4 tenants in the committed profile
        assert stress_cli._tenants_for(profile, "auto") == [
            "stress-1",
            "stress-2",
            "stress-3",
            "stress-4",
        ]

    def test_a_different_count_is_refused(self):
        from agent_db.stress.profile import ProfileValidationError, load_profile

        profile = load_profile(L1_PROFILE)
        # The fan-out is part of the measured shape: 2 tenants against a profile
        # that declares 4 is a different experiment, not a shorter one.
        with pytest.raises(ProfileValidationError, match="tenants.count=4"):
            stress_cli._tenants_for(profile, "a,b")

    def test_explicit_names_are_taken_verbatim(self):
        from agent_db.stress.profile import load_profile

        profile = load_profile(L1_PROFILE)
        assert stress_cli._tenants_for(profile, "one, two ,three,four") == [
            "one",
            "two",
            "three",
            "four",
        ]


class TestRateParsing:
    def test_default_ladder_is_used_when_no_rates_are_given(self, tmp_path):
        from agent_db.stress.profile import load_profile

        assert stress_cli._rates_for(load_profile(L1_PROFILE), "") == (5, 10, 20, 40, 80)

    def test_explicit_rates_are_parsed(self):
        from agent_db.stress.profile import load_profile

        assert stress_cli._rates_for(load_profile(L1_PROFILE), "2.5, 5") == (2.5, 5.0)

    def test_a_non_numeric_rate_is_a_refusal(self):
        from agent_db.stress.profile import LoadProfile, ProfileValidationError, load_profile

        profile: LoadProfile = load_profile(L1_PROFILE)
        with pytest.raises(ProfileValidationError, match="rates must be numbers"):
            stress_cli._rates_for(profile, "10,fast")


class TestRunCommand:
    def _run(
        self,
        runner: CliRunner,
        tmp_path: Path,
        stand: FakeStand,
        repo: Path,
        *extra: str,
    ):
        """Invoke `run` with a tiny ladder so the test stays a wiring test."""
        args = [
            "run",
            str(_profile(tmp_path)),
            "--mcp-url",
            "http://127.0.0.1:9/mcp",
            "--artifact-root",
            str(tmp_path / "evidence"),
            "--tenants",
            "t-1,t-2",
            "--rates",
            "5",
            "--duration-s",
            "2",
            "--warmup-s",
            "0.5",
            "--repeats",
            "1",
            "--no-host-probe",
            "--repo-root",
            str(repo),
            *extra,
        ]
        return _invoke(runner, args)

    def test_a_completed_run_writes_the_documented_tree(
        self, runner, tmp_path, repo, monkeypatch
    ):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = self._run(runner, tmp_path, stand, repo)
        assert result.exit_code == 0, result.stdout

        run_dir = _run_dir(tmp_path)
        for name in (
            "run-manifest.json",
            "preflight.json",
            "profile.json",
            "status.json",
            "report.json",
        ):
            assert (run_dir / name).is_file(), name
        assert json.loads((run_dir / "status.json").read_text())["status"] == (
            "completed"
        )

        raw_files = sorted(
            path for path in (run_dir / "raw").glob("*.jsonl") if ".calls." not in path.name
        )
        calls_files = sorted((run_dir / "raw").glob("*.calls.jsonl"))
        assert len(raw_files) == 1
        # §10 keeps the per-call decomposition next to the turn records, without
        # planned offsets of its own.
        assert len(calls_files) == 1
        calls = [
            json.loads(line)
            for line in calls_files[0].read_text(encoding="utf-8").splitlines()
        ]
        assert calls and all("started_wall_ms" in call for call in calls)
        records = [
            json.loads(line)
            for line in raw_files[0].read_text(encoding="utf-8").splitlines()
        ]
        assert len(records) == 10  # 2 s at 5 rps, warm-up included
        assert {record["tenant"] for record in records} <= {"t-1", "t-2"}

        generator = sorted((run_dir / "generator").glob("*.json"))
        assert len(generator) == 1
        summary = json.loads(generator[0].read_text())
        assert summary["ticket"]["target_rps"] == 5.0
        assert "spin_tail_ms" in summary and "cpu_measured_s" in summary

        report = json.loads((run_dir / "report.json").read_text())
        assert report["profile"] == "mcp-tool-call-l1"
        assert report["target_layer"] == "L1"
        assert report["layer_rule"] == "compensated turn latency (no LLM inside the turn)"
        assert len(report["rows"]) == 1
        assert (run_dir / f"report-{report['run_uuid']}.md").is_file()

    def test_a_ladder_crash_or_ctrl_c_leaves_a_truthful_tree(
        self, runner, tmp_path, repo, monkeypatch
    ):
        """§10: aborted exists so a dead generator is not reported as running."""
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))

        def explode(**kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(stress_cli, "run_ladder", explode)
        result = self._run(runner, tmp_path, stand, repo)
        assert result.exit_code == 3, result.stdout

        run_dir = _run_dir(tmp_path)
        status = json.loads((run_dir / "status.json").read_text())
        assert status["status"] == "aborted"
        assert "operator" in status["abort_reason"]
        # The lock is released: the next run on this target must not be told
        # that a dead generator still owns it.
        locks = list((tmp_path / "evidence" / ".stress-locks").glob("*.lock"))
        assert locks == []

    def test_an_unexpected_ladder_exception_names_itself(
        self, runner, tmp_path, repo, monkeypatch
    ):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))

        def explode(**kwargs: Any) -> None:
            raise RuntimeError("worker exploded")

        monkeypatch.setattr(stress_cli, "run_ladder", explode)
        result = self._run(runner, tmp_path, stand, repo)
        assert result.exit_code == 3, result.stdout

        status = json.loads((_run_dir(tmp_path) / "status.json").read_text())
        assert status["status"] == "aborted"
        assert "worker exploded" in status["abort_reason"]
        locks = list((tmp_path / "evidence" / ".stress-locks").glob("*.lock"))
        assert locks == []

    def test_a_stub_log_is_copied_into_the_run_evidence(self, runner, tmp_path, repo, monkeypatch):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        log = tmp_path / "stub-timings.jsonl"
        log.write_text('{"arrival_ms": 1, "service_ms": 2}\n', encoding="utf-8")
        result = self._run(runner, tmp_path, stand, repo, "--stub-log", str(log))
        assert result.exit_code == 0, result.stdout
        copied = _run_dir(tmp_path) / "stub" / "stub-timings.jsonl"
        assert copied.is_file()
        assert "service_ms" in copied.read_text(encoding="utf-8")

    def test_a_missing_stub_log_refuses_instead_of_lying(self, runner, tmp_path, repo, monkeypatch):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = self._run(
            runner, tmp_path, stand, repo, "--stub-log", str(tmp_path / "nope.jsonl")
        )
        assert result.exit_code == 2, result.stdout
        status = json.loads((_run_dir(tmp_path) / "status.json").read_text())
        assert status["status"] == "failed"
        assert "stub log not found" in status["abort_reason"]

    def test_the_preflight_probe_touches_every_tenant_before_the_ladder(
        self, runner, tmp_path, repo, monkeypatch
    ):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = self._run(runner, tmp_path, stand, repo)
        assert result.exit_code == 0, result.stdout

        preflight = json.loads((_run_dir(tmp_path) / "preflight.json").read_text())
        probes = [
            check
            for check in preflight["checks"]
            if check["check"] == "fixture_resolves"
        ]
        # One probe per tenant, and the manifest was read for both.
        assert {check["tenant"] for check in probes} == {"t-1", "t-2"}
        assert all(check["status"] == "ok" for check in probes)
        manifest_checks = [
            check for check in preflight["checks"] if check["check"] == "manifest"
        ]
        assert set(manifest_checks[0]["tenants"]) == {"t-1", "t-2"}

    def test_a_fixture_that_answers_with_an_error_refuses_the_run(
        self, runner, tmp_path, monkeypatch
    ):
        stand = FakeStand(tool_is_error=True)
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = self._run(runner, tmp_path, stand, repo)
        assert result.exit_code == 2
        assert "preflight refused" in result.stderr

        run_dir = _run_dir(tmp_path)
        status = json.loads((run_dir / "status.json").read_text())
        assert status["status"] == "failed"
        assert "isError" in status["abort_reason"]
        # A refused run schedules no ticks, so there is nothing in raw/.
        assert list((run_dir / "raw").glob("*.jsonl")) == []

    def test_a_dry_run_plans_and_manifests_without_ticks(
        self, runner, tmp_path, repo, monkeypatch
    ):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = self._run(runner, tmp_path, stand, repo, "--dry-run")
        assert result.exit_code == 0, result.stdout
        run_dir = _run_dir(tmp_path)
        status = json.loads((run_dir / "status.json").read_text())
        assert status["dry_run"] is True
        assert (run_dir / "run-manifest.json").is_file()
        assert list((run_dir / "raw").glob("*.jsonl")) == []

    def test_the_run_is_not_publishable_without_a_code_artefact(
        self, runner, tmp_path, repo, monkeypatch
    ):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = self._run(runner, tmp_path, stand, repo)
        # No git repository and no image digests in the temp tree, so nothing
        # ties the numbers to a build - and the run says so instead of shipping
        # them as capacity.
        assert "not publishable" in result.stdout
        blockers = json.loads((_run_dir(tmp_path) / "report.json").read_text())[
            "blockers"
        ]
        assert any("runtime artefact" in blocker for blocker in blockers)

    def test_a_second_run_on_the_same_target_is_refused(
        self, runner, tmp_path, repo, monkeypatch
    ):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        first = self._run(runner, tmp_path, stand, repo)
        assert first.exit_code == 0, first.stdout
        # The first run released its lock, so a second identical run is fine;
        # what must refuse is a *concurrent* one, which test_stress_guard pins.
        second = self._run(runner, tmp_path, stand, repo)
        assert second.exit_code == 0, second.stdout
        assert len(_run_dirs(tmp_path)) == 2


class TestCheckCommand:
    def test_a_runnable_stand_is_reported_as_runnable(self, runner, tmp_path, monkeypatch):
        stand = FakeStand()
        monkeypatch.setattr(stress_cli, "McpTransport", _transport_for(stand))
        result = _invoke(
            runner,
            [
                "check",
                str(L1_PROFILE),
                "--mcp-url",
                "http://127.0.0.1:9/mcp",
                "--tenants",
                "t-1,t-2,t-3,t-4",
                "--json-output",
                str(tmp_path / "preflight.json"),
            ],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads((tmp_path / "preflight.json").read_text())
        assert payload["status"] == "ok"
        assert payload["judged_metric"].startswith("compensated turn latency")
        # The budget table travels with the preflight, because "raised" is not a
        # specification (§5).
        assert payload["budgets"]["mcp_rate_limit_rps"]["origin"] in {
            "default",
            "env",
            "stand",
        }


class TestRefusals:
    def test_a_missing_profile_file_is_refused(self, runner, tmp_path):
        result = _invoke(runner, ["check", str(tmp_path / "nope.json")])
        assert result.exit_code == 2

    def test_an_unreachable_stand_is_refused_without_a_traceback(self, runner):
        # Port 9 (discard) is not listening: the refusal has to be a message, not
        # a stack trace, because this is the first thing an operator meets.
        result = _invoke(
            runner,
            [
                "check",
                str(L1_PROFILE),
                "--mcp-url",
                "http://127.0.0.1:9/mcp",
                "--tenants",
                "t-1,t-2,t-3,t-4",
            ],
        )
        assert result.exit_code == 2
        assert "preflight refused" in result.stderr

    def test_a_budget_outside_the_table_is_refused(self, runner, tmp_path):
        result = _invoke(
            runner,
            ["check", str(L1_PROFILE), "--budget", "made_up_budget=1"],
        )
        assert result.exit_code == 2
        assert "§5 table" in result.stderr


def _run_dir(tmp_path: Path) -> Path:
    runs = _run_dirs(tmp_path)
    assert len(runs) == 1
    return runs[0]


def _run_dirs(tmp_path: Path) -> list[Path]:
    """Run directories for this target, excluding the lock directory."""
    return [
        path
        for path in (tmp_path / "evidence").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]


def _transport_for(stand: FakeStand):
    """The CLI's McpTransport, wired to a scripted stand instead of a socket."""
    from agent_db.stress.transport import McpTransport

    def build(base_url: str, **kwargs: Any) -> Any:
        kwargs.pop("connection_factory", None)
        return McpTransport(base_url, connection_factory=_factory(stand), **kwargs)

    return build


def _factory(stand: FakeStand):
    def build(scheme, host, port, timeout):  # noqa: ANN001
        return stand.connection()

    return build
