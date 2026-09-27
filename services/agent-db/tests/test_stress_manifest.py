"""Tests for the run manifest (§6): what is recorded, and what is refused."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from agent_db.stress.manifest import (
    BUDGET_TABLE,
    HARDCODED_CONSTANTS,
    REQUIRED_HOST_FIELDS,
    BinarySpec,
    ManifestError,
    RunManifest,
    collect_budgets,
    collect_code,
    collect_environment,
    profile_digest,
)
from agent_db.stress.profile import load_profile

STRESS_DIR = Path(__file__).resolve().parents[1] / "agent_db" / "stress"
L1_PROFILE = STRESS_DIR / "profiles" / "mcp-tool-call-l1.json"


@pytest.fixture
def profile():
    return load_profile(L1_PROFILE)


def environment(profile, **kwargs):
    return collect_environment(profile, probe_host=False, **kwargs)


# ``os.cpu_count()`` and ``platform.release()`` describe the interpreter's own
# host and are always filled; every other field is either measured or a gap.
PROBED_HOST_FIELDS = tuple(
    name for name in REQUIRED_HOST_FIELDS if name not in {"cpu_cores", "kernel"}
)


class TestEnvironmentHonesty:
    def test_every_unobserved_host_field_is_recorded_as_a_gap(self, profile):
        env = environment(profile)
        assert set(env.missing_required()) == set(PROBED_HOST_FIELDS)
        for name in PROBED_HOST_FIELDS:
            assert env.unobserved[name], name

    def test_no_host_value_is_invented_when_probing_is_off(self, profile):
        # The whole point of the split: a plausible value would be compared as a
        # fact later. An empty field is a gap, a filled one is a measurement.
        env = environment(profile)
        for name in PROBED_HOST_FIELDS:
            assert getattr(env, name) is None, name

    def test_the_interpreter_host_is_always_described(self, profile):
        env = environment(profile)
        assert env.cpu_cores == 8
        assert env.kernel

    def test_supplied_facts_survive_without_probing(self, profile):
        env = environment(
            profile,
            tenant_engine="postgres 16.4",
            tenant_db_size="2.1 MiB",
            generator_host="stand-02",
            backlog_mode="full",
        )
        assert env.tenant_engine == "postgres 16.4"
        assert env.tenant_db_size == "2.1 MiB"
        assert env.generator_host == "stand-02"
        assert env.backlog_mode == "full"
        assert "tenant_db_size" not in env.missing_required()

    def test_substrate_follows_the_layer(self, profile):
        assert environment(profile).substrate == "none"

    def test_hardcoded_constants_are_recorded(self, profile):
        env = environment(profile)
        assert env.hardcoded_constants["proactive_reconnect_idle_seconds"] == 240.0
        assert HARDCODED_CONSTANTS["api_service_workers"] == 1


class TestEnvironmentHash:
    def test_same_facts_hash_the_same(self, profile):
        assert environment(profile).hash() == environment(profile).hash()

    def test_a_different_fact_changes_the_hash(self, profile):
        base = environment(profile)
        other = environment(profile, backlog_mode="errors")
        assert base.hash() != other.hash()

    def test_gaps_are_part_of_the_identity(self, profile):
        # One run that could not read its governor is not the same environment as
        # one that could, even on the same machine.
        probed = environment(profile)
        partial = environment(profile)
        object.__setattr__(partial, "unobserved", {})
        assert probed.hash() != partial.hash()

    def test_short_hash_is_a_prefix_of_the_full_one(self, profile):
        manifest = RunManifest(environment=environment(profile), code=_code())
        assert manifest.environment_hash().startswith(
            manifest.environment_hash(short=True)
        )


class TestBudgets:
    def test_every_row_of_the_budget_table_is_written(self):
        budgets = collect_budgets({})
        assert set(budgets) == {spec.name for spec in BUDGET_TABLE}

    def test_defaults_name_their_source(self):
        budgets = collect_budgets({})
        assert budgets["chat_rate_limit"]["value"] == "30/minute"
        assert budgets["chat_rate_limit"]["origin"] == "default"
        assert budgets["chat_rate_limit"]["default_source"] == "docker-compose.yml"

    def test_an_env_value_replaces_the_default_and_says_so(self):
        budgets = collect_budgets({"MCP_RATE_LIMIT_RPS": "1000"})
        assert budgets["mcp_rate_limit_rps"] == {
            "value": "1000",
            "origin": "env",
            "env_key": "MCP_RATE_LIMIT_RPS",
        }

    def test_an_empty_env_value_is_not_a_raised_budget(self):
        budgets = collect_budgets({"MCP_RATE_LIMIT_RPS": ""})
        assert budgets["mcp_rate_limit_rps"]["origin"] == "default"

    def test_a_stand_value_replaces_the_harness_environment(self):
        # The budgets live in the services' environment, not in the harness's;
        # reading only the latter would print defaults next to a raised limiter.
        budgets = collect_budgets({}, overrides={"mcp_rate_limit_rps": "10000"})
        assert budgets["mcp_rate_limit_rps"] == {"value": "10000", "origin": "stand"}

    def test_a_stand_can_declare_a_budget_absent(self):
        budgets = collect_budgets(
            {}, overrides={"chat_rate_limit": "absent: no api-service on this stand"}
        )
        assert budgets["chat_rate_limit"]["value"].startswith("absent")

    def test_an_unknown_budget_name_is_refused(self):
        with pytest.raises(ManifestError, match="not in the §5 table"):
            collect_budgets({}, overrides={"chat_rate_limit_rps": "10"})


class TestCodeInfo:
    @staticmethod
    def _repo(tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(repo), *args], check=True, capture_output=True
            )

        git("init", "-q")
        git("config", "user.email", "stress@example.invalid")
        git("config", "user.name", "stress test")
        (repo / "file.txt").write_text("one\n", encoding="utf-8")
        git("add", "file.txt")
        git("commit", "-q", "-m", "initial")
        return repo

    def test_clean_tree_is_recorded_with_its_commit(self, tmp_path):
        repo = self._repo(tmp_path)
        code = collect_code(repo)
        expected = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert code.commit == expected
        assert code.dirty is False
        assert code.api_workers == 1

    def test_an_uncommitted_change_marks_the_tree_dirty(self, tmp_path):
        repo = self._repo(tmp_path)
        (repo / "file.txt").write_text("two\n", encoding="utf-8")
        assert collect_code(repo).dirty is True

    def test_missing_repository_is_an_error_not_an_empty_manifest(self, tmp_path):
        with pytest.raises(ManifestError):
            collect_code(tmp_path / "not-a-repo")

    def test_a_dirty_tree_blocks_publication(self, profile):
        clean = RunManifest(environment=environment(profile), code=_code(dirty=False))
        dirty = RunManifest(environment=environment(profile), code=_code(dirty=True))
        assert not any("dirty" in blocker for blocker in clean.publication_blockers())
        assert any("dirty" in blocker for blocker in dirty.publication_blockers())


def _code(dirty: bool = False):
    from agent_db.stress.manifest import CodeInfo

    # A complete manifest names its artefact: without a digest or a binary the
    # numbers cannot be tied to a build, and publication refuses the run.
    return CodeInfo(
        commit="0" * 40,
        branch="main",
        dirty=dirty,
        api_workers=1,
        image_digests={"data-service": "sha256:" + "a" * 64},
    )


def _fake_git(args: list[str]) -> str:
    """A clean tree at a fixed commit: these tests are about binaries only."""
    if args[:2] == ["rev-parse", "HEAD"]:
        return "0" * 40 + "\n"
    if args[0] == "rev-parse":
        return "main\n"
    return ""


class TestBinaries:
    """A native stand runs binaries, and the manifest must name them (§6).

    Regression: a gateway binary two months older than the tree served a load run
    with a config fetch and a server build per request, and every measurement was
    attributed to HEAD, because nothing in the artefact recorded the binary.
    """

    @staticmethod
    def _tree(tmp_path: Path, *, binary_mtime: float, source_mtime: float) -> dict:
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "main.go").write_text("package main\n", encoding="utf-8")
        # A test file edited after the build must not mark the binary stale:
        # tests never enter the artefact.
        (repo / "src" / "main_test.go").write_text("package main\n", encoding="utf-8")
        os.utime(repo / "src" / "main.go", (source_mtime, source_mtime))
        os.utime(repo / "src" / "main_test.go", (source_mtime + 10, source_mtime + 10))
        binary = repo / "bin" / "gateway"
        binary.parent.mkdir()
        binary.write_bytes(b"\x7fELFBIN")
        os.utime(binary, (binary_mtime, binary_mtime))
        return {"repo": repo, "binary": binary}

    def test_a_stale_binary_blocks_publication(self, tmp_path, profile):
        tree = self._tree(
            tmp_path, binary_mtime=time.time() - 86400, source_mtime=time.time()
        )
        code = collect_code(
            tree["repo"],
            binaries=[BinarySpec("mcp-gateway", tree["binary"], tree["repo"] / "src")],
            runner=_fake_git,
        )
        info = code.binaries["mcp-gateway"]
        assert info["freshness"] == "stale"
        assert len(info["sha256"]) == 64
        assert info["newest_source"] == "main.go"
        blockers = RunManifest(
            environment=environment(profile), code=code
        ).publication_blockers()
        assert any("is older than its newest source" in blocker for blocker in blockers)

    def test_a_binary_newer_than_its_sources_is_fresh(self, tmp_path, profile):
        tree = self._tree(
            tmp_path, binary_mtime=time.time(), source_mtime=time.time() - 86400
        )
        code = collect_code(
            tree["repo"],
            binaries=[BinarySpec("data-service", tree["binary"], tree["repo"] / "src")],
            runner=_fake_git,
        )
        assert code.binaries["data-service"]["freshness"] == "fresh"
        assert not [
            blocker
            for blocker in RunManifest(
                environment=environment(profile), code=code
            ).publication_blockers()
            if "runtime binary" in blocker
        ]

    def test_a_binary_without_readable_sources_is_unchecked_not_fresh(self, tmp_path, profile):
        tree = self._tree(
            tmp_path, binary_mtime=time.time(), source_mtime=time.time()
        )
        code = collect_code(
            tree["repo"],
            binaries=[
                BinarySpec("gateway", tree["binary"], tree["repo"] / "no-such-dir")
            ],
            runner=_fake_git,
        )
        assert code.binaries["gateway"]["freshness"] == "unchecked"
        blockers = RunManifest(
            environment=environment(profile), code=code
        ).publication_blockers()
        assert any("freshness could not be checked" in b for b in blockers)

    def test_a_missing_binary_is_an_error_not_an_unchecked(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ManifestError, match="not found"):
            collect_code(
                repo,
                binaries=[BinarySpec("gateway", repo / "bin" / "gateway", repo)],
                runner=_fake_git,
            )

    def test_a_run_that_names_no_artefact_at_all_is_blocked(self, tmp_path, profile):
        repo = tmp_path / "repo"
        repo.mkdir()
        code = collect_code(repo, runner=_fake_git)
        assert code.image_digests == {}
        assert code.binaries == {}
        manifest = RunManifest(environment=environment(profile), code=code)
        assert any(
            "no runtime artefact" in blocker for blocker in manifest.publication_blockers()
        )


class TestRunManifest:
    def test_blockers_name_every_gap(self, profile):
        manifest = RunManifest(environment=environment(profile), code=_code())
        blockers = manifest.publication_blockers()
        assert len(blockers) == len(PROBED_HOST_FIELDS)
        assert any("cpu_governor" in blocker for blocker in blockers)
        assert any("host probing disabled" in blocker for blocker in blockers)

    def test_a_complete_environment_has_no_blockers(self, profile):
        env = environment(profile)
        object.__setattr__(env, "unobserved", {})
        for name in REQUIRED_HOST_FIELDS:
            object.__setattr__(env, name, "observed")
        manifest = RunManifest(environment=env, code=_code())
        assert manifest.publication_blockers() == []

    def test_json_keeps_the_split_and_carries_the_hash(self, profile):
        manifest = RunManifest(environment=environment(profile), code=_code())
        payload = json.loads(manifest.to_json())
        assert set(payload) >= {"environment", "code", "run_uuid", "started_at"}
        assert payload["environment_hash"] == manifest.environment_hash()
        assert payload["environment"]["budget_preset"] == "off"
        assert payload["code"]["commit"] == "0" * 40

    def test_profile_digest_changes_with_the_file(self, tmp_path):
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        first.write_text("{}", encoding="utf-8")
        second.write_text('{"a": 1}', encoding="utf-8")
        assert profile_digest(first) != profile_digest(second)
