"""Run manifest: environment and code, split so two runs can be compared (§6).

The split exists because regression is measured *between commits*: while ``code``
is expected to differ, ``environment`` must match completely or the comparison is
between two different machines wearing the same label. That makes an unobserved
environment field worse than a missing one - a plausible invented value ("CPU:
modern", "disk: SSD") would be compared as if it were a fact. So every probe here
either returns a measured value or is recorded as a gap with the reason, and
:meth:`RunManifest.publication_blockers` refuses to call a run publishable while
gaps remain. §6 forbids publishing a capacity number without a complete manifest.

The budget preset is part of the environment for the same reason (§5): "raised"
is not a specification, so the table is written with the values that were
actually in effect and where each came from (env or the documented default).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import resource
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .profile import LoadProfile

MANIFEST_VERSION = 1

# Fields §6 lists under ``environment`` that have to be *measured*, not assumed.
# ``generator_host``/``budget_preset``/``profile`` are supplied by the caller and
# are therefore always present; a probe that cannot run lands in ``unobserved``.
REQUIRED_HOST_FIELDS = (
    "cpu_model",
    "cpu_cores",
    "ram_gb",
    "disk_type",
    "kernel",
    "ulimit_n",
    "cpu_governor",
    "compose_version",
    "cgroup_cpu_max",
    "cgroup_memory_max",
    "tenant_db_size",
)


class ManifestError(RuntimeError):
    """The manifest cannot be built, and a guessed manifest is worse than none."""


@dataclass(frozen=True)
class BudgetSpec:
    """One row of the §5 budget table, with the source of its default."""

    name: str
    env_key: str | None
    default: str
    default_source: str


# §5. The env keys are the ones that actually exist: ``default_source`` names the
# file the default was read from, so a future preflight can check the copy.
BUDGET_TABLE: tuple[BudgetSpec, ...] = (
    BudgetSpec("chat_rate_limit", "CHAT_RATE_LIMIT", "30/minute", "docker-compose.yml"),
    BudgetSpec("reports_rate_limit", "REPORTS_RATE_LIMIT", "5/minute", ".env.example"),
    BudgetSpec("abuse_rps", "ABUSE_RPS", "1.0", ".env.example"),
    BudgetSpec("abuse_burst", "ABUSE_BURST", "5", ".env.example"),
    BudgetSpec("abuse_ip_rps", "ABUSE_IP_RPS", "1.0", ".env.example"),
    BudgetSpec("abuse_ip_burst", "ABUSE_IP_BURST", "20", ".env.example"),
    BudgetSpec(
        "abuse_min_interval_ms", "ABUSE_MIN_INTERVAL_MS", "1000", ".env.example"
    ),
    BudgetSpec("abuse_max_user_turns", "ABUSE_MAX_USER_TURNS", "50", ".env.example"),
    BudgetSpec("abuse_max_repeated", "ABUSE_MAX_REPEATED", "3", ".env.example"),
    BudgetSpec("abuse_max_msg_length", "ABUSE_MAX_MSG_LENGTH", "2000", ".env.example"),
    BudgetSpec("mcp_rate_limit_rps", "MCP_RATE_LIMIT_RPS", "10", "cmd/ratelimit.go"),
    BudgetSpec(
        "mcp_rate_limit_burst", "MCP_RATE_LIMIT_BURST", "20", "cmd/ratelimit.go"
    ),
    BudgetSpec(
        "mcp_rate_limit_max_ips", "MCP_RATE_LIMIT_MAX_IPS", "10000", "cmd/ratelimit.go"
    ),
)


def collect_budgets(
    environ: Mapping[str, str] | None = None,
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Every §5 budget with the value in effect and where that value came from.

    ``overrides`` exist because the budgets live in the *services'* environment
    while the harness usually runs somewhere else: reading only its own
    environment would print the documented defaults next to a stand whose limiter
    was raised, which is exactly the "raised is not a specification" trap §5 warns
    about. Anything the services' environment cannot be read for is described
    here instead, including "absent on this stand".
    """
    source = os.environ if environ is None else environ
    supplied = overrides or {}
    unknown = set(supplied) - {spec.name for spec in BUDGET_TABLE}
    if unknown:
        raise ManifestError(
            f"budget override names not in the §5 table: {sorted(unknown)}; a budget "
            "outside the table cannot be compared between runs"
        )
    budgets: dict[str, dict[str, Any]] = {}
    for spec in BUDGET_TABLE:
        if spec.name in supplied:
            budgets[spec.name] = {"value": supplied[spec.name], "origin": "stand"}
            continue
        raw = source.get(spec.env_key) if spec.env_key else None
        if raw:
            budgets[spec.name] = {
                "value": raw,
                "origin": "env",
                "env_key": spec.env_key,
            }
        else:
            budgets[spec.name] = {
                "value": spec.default,
                "origin": "default",
                "default_source": spec.default_source,
            }
    return budgets


@dataclass(frozen=True)
class CodeInfo:
    """The subject of comparison, and the first knob a capacity fix turns (§6)."""

    commit: str
    branch: str
    dirty: bool
    api_workers: int
    image_digests: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnvironmentInfo:
    """Everything that must match between two compared runs (§6)."""

    cpu_model: str | None
    cpu_cores: int | None
    ram_gb: float | None
    disk_type: str | None
    kernel: str | None
    ulimit_n: int | None
    cpu_governor: str | None
    compose_version: str | None
    cgroup_cpu_max: str | None
    cgroup_memory_max: str | None
    generator_host: str
    budget_preset: str
    budgets: dict[str, dict[str, Any]]
    profile: str
    profile_sha256: str
    substrate: str
    tenant_engine: str
    tenant_db_size: str | None
    tenant_count: int
    demo_history_turns: int
    backlog_mode: str
    hardcoded_constants: dict[str, Any]
    unobserved: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def missing_required(self) -> list[str]:
        """Required host fields that were never observed, in declaration order."""
        return [
            name for name in REQUIRED_HOST_FIELDS if getattr(self, name, None) is None
        ]

    def hash(self) -> str:
        """Stable digest of the environment, for the report's comparison column.

        Includes the gaps: two runs on the same machine, one of which could not
        read its governor, are not the same environment.
        """
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RunManifest:
    """The manifest written as ``run-manifest.json`` next to a run's evidence."""

    environment: EnvironmentInfo
    code: CodeInfo
    run_uuid: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    manifest_version: int = MANIFEST_VERSION
    modes: dict[str, Any] = field(default_factory=dict)

    def environment_hash(self, *, short: bool = False) -> str:
        digest = self.environment.hash()
        return digest[:16] if short else digest

    def to_json(self) -> str:
        payload = asdict(self)
        payload["environment_hash"] = self.environment_hash()
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)

    def publication_blockers(self) -> list[str]:
        """Why this run's numbers may not be published as capacity (§6, §2).

        Empty means the manifest is complete and the run is reproducible *with
        respect to the manifest*; the remaining requirements (quiet host,
        repeated runs) live in the preflight and in :mod:`agent_db.stress.ladder`.
        """
        blockers: list[str] = []
        for name in self.environment.missing_required():
            reason = self.environment.unobserved.get(name, "not observed")
            blockers.append(f"environment field {name!r} unobserved: {reason}")
        if self.code.dirty:
            blockers.append(
                f"working tree was dirty at {self.code.commit[:8]}: the recorded "
                "commit does not describe the code that ran"
            )
        return blockers


def collect_code(
    repo_root: str | Path,
    *,
    api_workers: int = 1,
    image_digests: Mapping[str, str] | None = None,
    runner: Callable[[list[str]], str] | None = None,
) -> CodeInfo:
    """Read the commit, the branch and whether the tree was clean."""

    def git(args: list[str]) -> str:
        if runner is not None:
            return runner(args)
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ManifestError(
                f"git {' '.join(args)} failed in {repo_root}: {exc}"
            ) from exc
        return completed.stdout

    commit = git(["rev-parse", "HEAD"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ManifestError(f"unexpected git commit format: {commit!r}")
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).strip() or "DETACHED"
    dirty = bool(git(["status", "--porcelain"]).strip())
    return CodeInfo(
        commit=commit,
        branch=branch,
        dirty=dirty,
        api_workers=api_workers,
        image_digests=dict(image_digests or {}),
    )


def _read_text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _cpu_model(unobserved: dict[str, str]) -> str | None:
    if platform.system() == "Linux":
        cpuinfo = _read_text("/proc/cpuinfo")
        if cpuinfo:
            for line in cpuinfo.splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    model = platform.processor() or platform.machine()
    if model:
        return model
    unobserved["cpu_model"] = (
        "neither /proc/cpuinfo nor platform.processor() reported one"
    )
    return None


def _ram_gb(unobserved: dict[str, str]) -> float | None:
    meminfo = _read_text("/proc/meminfo")
    if meminfo:
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
        if match:
            return round(int(match.group(1)) / 1024 / 1024, 2)
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError) as exc:  # pragma: no cover - platform
        unobserved["ram_gb"] = f"sysconf unavailable: {exc}"
        return None
    return round(pages * size / 1024**3, 2)


def _disk_type(unobserved: dict[str, str]) -> str | None:
    """Rotational flag of the device backing the run's working directory.

    Only ``lsblk`` knows this, and only on Linux: a container cannot see the host
    block device, so a gap here is the honest answer rather than "SSD" by
    assumption - the whole point of the field is that an HDD stand and an NVMe
    stand are not comparable.
    """
    if shutil.which("lsblk") is None:
        unobserved["disk_type"] = "lsblk is not available"
        return None
    try:
        completed = subprocess.run(
            ["lsblk", "-dno", "name,rota,model"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        unobserved["disk_type"] = f"lsblk failed: {exc}"
        return None
    lines = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        unobserved["disk_type"] = "lsblk reported no devices"
        return None
    # One entry per device: ``rota`` 0 is non-rotational (SSD/NVMe).
    kinds = {"1": "hdd", "0": "ssd"}
    described = [
        f"{parts[0]}={kinds.get(parts[1], 'unknown')}"
        for parts in lines
        if len(parts) >= 2
    ]
    return ",".join(described)


def _ulimit_n(unobserved: dict[str, str]) -> int | None:
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError) as exc:  # pragma: no cover - platform
        unobserved["ulimit_n"] = f"getrlimit failed: {exc}"
        return None
    if soft == resource.RLIM_INFINITY:
        unobserved["ulimit_n"] = (
            "soft limit is unlimited; the effective value is unknown"
        )
        return None
    return int(soft)


def _cpu_governor(unobserved: dict[str, str]) -> str | None:
    for cpu in range(64):
        text = _read_text(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor")
        if text:
            return text.strip()
        if cpu == 0 and not Path(f"/sys/devices/system/cpu/cpu{cpu}").exists():
            break
    unobserved["cpu_governor"] = (
        "/sys/.../scaling_governor is unreadable (no cpufreq in this namespace)"
    )
    return None


def _cgroup_field(name: str, unobserved: dict[str, str]) -> str | None:
    """Read one cgroup v2 controller file; cgroup v1 layouts are a gap."""
    text = _read_text(f"/sys/fs/cgroup/{name}")
    if text is None:
        unobserved[name] = "cgroup v2 file not readable from this namespace"
        return None
    return text.strip()


def _compose_version(unobserved: dict[str, str]) -> str | None:
    if shutil.which("docker") is None:
        unobserved["compose_version"] = "docker CLI not on PATH"
        return None
    try:
        completed = subprocess.run(
            ["docker", "compose", "version", "--short"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        unobserved["compose_version"] = f"docker compose version failed: {exc}"
        return None
    version = completed.stdout.strip()
    if not version:
        unobserved["compose_version"] = "docker compose version printed nothing"
        return None
    return version


def profile_digest(path: str | Path) -> str:
    """Snapshot hash of the profile actually run (§10 keeps the file too)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect_environment(
    profile: LoadProfile,
    *,
    profile_path: str | Path | None = None,
    generator_host: str = "same-host",
    tenant_engine: str = "",
    tenant_db_size: str | None = None,
    api_workers: int = 1,
    demo_history_turns: int | None = None,
    backlog_mode: str | None = None,
    hardcoded_constants: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    budget_values: Mapping[str, str] | None = None,
    probe_host: bool = True,
) -> EnvironmentInfo:
    """Build the ``environment`` group, recording what could not be observed.

    ``probe_host=False`` skips every host probe (no subprocesses, no ``/sys``) and
    records all of them as gaps: unit tests and dry runs must not shell out, and a
    manifest built that way is marked unpublishable, which is correct. ``cpu_cores``
    and ``kernel`` are always filled: they describe the running interpreter rather
    than the host around it, and neither can be misread as a claim about the stand.
    """
    source = os.environ if environ is None else environ
    unobserved: dict[str, str] = {}

    def skip(name: str) -> None:
        unobserved[name] = "host probing disabled (probe_host=False)"

    disk: str | None = None
    ulimit: int | None = None
    governor: str | None = None
    cpu_max: str | None = None
    memory_max: str | None = None
    compose: str | None = None
    if probe_host:
        cpu = _cpu_model(unobserved)
        ram = _ram_gb(unobserved)
        disk = _disk_type(unobserved)
        ulimit = _ulimit_n(unobserved)
        governor = _cpu_governor(unobserved)
        cpu_max = _cgroup_field("cpu.max", unobserved)
        memory_max = _cgroup_field("memory.max", unobserved)
        compose = _compose_version(unobserved)
    else:
        for name in (
            "cpu_model",
            "ram_gb",
            "disk_type",
            "ulimit_n",
            "cpu_governor",
            "cgroup_cpu_max",
            "cgroup_memory_max",
            "compose_version",
        ):
            skip(name)
        cpu = None
        ram = None

    if tenant_db_size is None:
        unobserved["tenant_db_size"] = (
            "not supplied: the harness does not query the tenant DB during preflight"
        )

    return EnvironmentInfo(
        cpu_model=cpu,
        cpu_cores=os.cpu_count(),
        ram_gb=ram,
        disk_type=disk,
        kernel=platform.release(),
        ulimit_n=ulimit,
        cpu_governor=governor,
        compose_version=compose,
        cgroup_cpu_max=cpu_max,
        cgroup_memory_max=memory_max,
        generator_host=generator_host,
        budget_preset=profile.budget_preset,
        budgets=collect_budgets(source, overrides=budget_values),
        profile=profile_path.name if profile_path else profile.name,
        profile_sha256=profile_digest(profile_path) if profile_path else "",
        substrate=profile.llm.substrate if profile.llm else "none",
        tenant_engine=tenant_engine,
        tenant_db_size=tenant_db_size,
        tenant_count=profile.tenants.count,
        demo_history_turns=(
            demo_history_turns
            if demo_history_turns is not None
            else _history_turns(source)
        ),
        backlog_mode=backlog_mode or source.get("BACKLOG_MODE") or "unset",
        hardcoded_constants=dict(hardcoded_constants or HARDCODED_CONSTANTS),
        unobserved=unobserved,
    )


def _history_turns(environ: Mapping[str, str]) -> int:
    raw = environ.get("DEMO_HISTORY_TURNS")
    if raw and raw.isdigit():
        return int(raw)
    from .constants import DEMO_HISTORY_TURNS

    return DEMO_HISTORY_TURNS


# §6: constants that cannot be set from the environment and therefore belong in
# the manifest - a run is not comparable if one of them moved in the code.
HARDCODED_CONSTANTS: dict[str, Any] = {
    "proactive_reconnect_idle_seconds": 240.0,
    "api_service_workers": 1,
}
