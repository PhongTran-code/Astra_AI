"""Sandbox provider contracts and the deliberately weak local implementation.

The local provider is useful for development and tests, but it is explicitly
labelled ``weak_local``.  Strong isolation must come from a container or remote
adapter that enforces mounts, network, and resource limits outside the agent
process.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    import resource as _resource
except ImportError:  # pragma: no cover - Windows has no POSIX resource module.
    _resource = None

from .contracts import SandboxStatus
from .persistence import SandboxRepository


class SandboxError(RuntimeError):
    """A sandbox could not provision, execute, or preserve its isolation contract."""


class SandboxNotReadyError(SandboxError):
    """An operation requires a ready sandbox."""


class SandboxPolicyViolation(SandboxError):
    """A command or mount exceeds the sandbox policy."""


class SandboxQuotaExceeded(SandboxError):
    """A local best-effort quota was exceeded."""


@dataclass(frozen=True)
class SandboxExecutionResult:
    sandbox_id: str
    run_id: str
    job_path: str
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    started_at: str
    finished_at: str
    isolation_level: str
    network_enforced: bool


class SandboxAdapter(Protocol):
    """Contract implemented by a strong container/remote runtime."""

    provider_kind: str
    isolation_level: str
    strong_isolation: bool
    network_enforced: bool

    def provision(self, sandbox_id: str, home_ref: str) -> None: ...

    def execute(
        self,
        sandbox_id: str,
        run_id: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        resource_policy: Mapping[str, Any],
    ) -> SandboxExecutionResult: ...

    def health(self, sandbox_id: str) -> Mapping[str, Any]: ...

    def suspend(self, sandbox_id: str) -> None: ...

    def resume(self, sandbox_id: str) -> None: ...

    def reset(self, sandbox_id: str) -> None: ...

    def archive(self, sandbox_id: str) -> None: ...


class SandboxProvider(Protocol):
    provider_kind: str
    isolation_level: str
    strong_isolation: bool
    network_enforced: bool

    def provision(self, sandbox_id: str, home_ref: str) -> None: ...

    def prepare_job(self, sandbox_id: str, run_id: str) -> Path: ...

    def execute(
        self,
        sandbox_id: str,
        run_id: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        resource_policy: Mapping[str, Any],
    ) -> SandboxExecutionResult: ...

    def health(self, sandbox_id: str) -> Mapping[str, Any]: ...

    def suspend(self, sandbox_id: str) -> None: ...

    def resume(self, sandbox_id: str) -> None: ...

    def reset(self, sandbox_id: str) -> None: ...

    def archive(self, sandbox_id: str) -> None: ...


class LocalSandboxProvider:
    """Folder/process provider with no claim of kernel-level isolation."""

    provider_kind = "local"
    isolation_level = "weak_local"
    strong_isolation = False
    network_enforced = False

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def provision(self, sandbox_id: str, home_ref: str) -> None:
        sandbox_id = _safe_id(sandbox_id, "sandbox id")
        home = Path(home_ref).expanduser().resolve()
        _confined(home, self.base_dir, "sandbox home")
        home.mkdir(parents=True, exist_ok=True)
        (home.parent / "jobs").mkdir(parents=True, exist_ok=True)
        (home.parent / "snapshots").mkdir(parents=True, exist_ok=True)
        (home.parent / "archives").mkdir(parents=True, exist_ok=True)

    def prepare_job(self, sandbox_id: str, run_id: str) -> Path:
        home = self._home(sandbox_id)
        job = home.parent / "jobs" / _safe_id(run_id, "run id")
        _confined(job, home.parent, "sandbox job")
        job.mkdir(parents=True, exist_ok=True)
        (job / "tmp").mkdir(exist_ok=True)
        return job

    def execute(
        self,
        sandbox_id: str,
        run_id: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        resource_policy: Mapping[str, Any],
    ) -> SandboxExecutionResult:
        job = self.prepare_job(sandbox_id, run_id)
        argv = _argv(command)
        network = str(resource_policy.get("network_policy", "deny") or "deny").strip().lower()
        if network not in {"deny", "none"} or resource_policy.get("network_domains"):
            raise SandboxPolicyViolation(
                "local sandbox cannot enforce a requested network allowlist"
            )
        env = self._environment(sandbox_id, run_id, job, environment)
        started = _utc_now()
        timeout = resource_policy.get("max_wall_seconds")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
                raise SandboxPolicyViolation("max_wall_seconds must be a positive integer")
        process = subprocess.Popen(
            argv,
            cwd=job,
            env=env,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=(lambda: _apply_resource_limits(resource_policy)) if _resource is not None else None,
            text=False,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        finished = _utc_now()
        disk_limit = resource_policy.get("disk_mib")
        if disk_limit is not None and _directory_size(job) > _positive_limit(disk_limit, 1) * 1024 * 1024:
            raise SandboxQuotaExceeded("sandbox disk quota exceeded")
        max_output = _positive_limit(resource_policy.get("max_output_bytes"), 1_000_000)
        return SandboxExecutionResult(
            sandbox_id=sandbox_id,
            run_id=run_id,
            job_path=str(job),
            command=tuple(argv),
            exit_code=None if timed_out else process.returncode,
            stdout=stdout[:max_output].decode("utf-8", "replace"),
            stderr=stderr[:max_output].decode("utf-8", "replace"),
            timed_out=timed_out,
            started_at=started,
            finished_at=finished,
            isolation_level=self.isolation_level,
            network_enforced=self.network_enforced,
        )

    def health(self, sandbox_id: str) -> Mapping[str, Any]:
        home = self._home(sandbox_id)
        return {
            "sandbox_id": sandbox_id,
            "provider_kind": self.provider_kind,
            "isolation_level": self.isolation_level,
            "strong_isolation": self.strong_isolation,
            "network_enforced": self.network_enforced,
            "home_exists": home.is_dir(),
        }

    def suspend(self, sandbox_id: str) -> None:
        self._home(sandbox_id)

    def resume(self, sandbox_id: str) -> None:
        self._home(sandbox_id).mkdir(parents=True, exist_ok=True)

    def reset(self, sandbox_id: str) -> None:
        home = self._home(sandbox_id)
        if not home.exists():
            home.mkdir(parents=True)
            return
        archive = home.parent / "archives" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True, exist_ok=False)
        shutil.move(str(home), str(archive / "home"))
        home.mkdir(parents=True, exist_ok=True)

    def archive(self, sandbox_id: str) -> None:
        home = self._home(sandbox_id)
        if not home.exists():
            return
        archive = home.parent / "archives" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True, exist_ok=False)
        shutil.move(str(home), str(archive / "home"))

    def _home(self, sandbox_id: str) -> Path:
        sandbox_id = _safe_id(sandbox_id, "sandbox id")
        home = self.base_dir / sandbox_id / "home"
        return _confined(home, self.base_dir, "sandbox home")

    def _environment(
        self,
        sandbox_id: str,
        run_id: str,
        job: Path,
        explicit: Mapping[str, str],
    ) -> dict[str, str]:
        if not isinstance(explicit, Mapping):
            raise SandboxPolicyViolation("sandbox environment must be an explicit mapping")
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._home(sandbox_id)),
            "PWD": str(job),
            "TMPDIR": str(job / "tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "ASTRA_SANDBOX_ID": sandbox_id,
            "ASTRA_RUN_ID": run_id,
        }
        reserved = frozenset(env)
        for key, value in explicit.items():
            key = str(key).strip()
            if not key or "=" in key or "\x00" in key:
                raise SandboxPolicyViolation("sandbox environment key is malformed")
            if key in reserved:
                raise SandboxPolicyViolation(f"sandbox environment cannot override {key}")
            if not isinstance(value, str) or "\x00" in value:
                raise SandboxPolicyViolation("sandbox environment values must be strings without NUL bytes")
            env[key] = value
        return env


class ContainerSandboxAdapter:
    """Docker/Podman adapter with an explicit deny-by-default run contract.

    This adapter is intentionally opt-in. It never silently falls back to the
    local provider, and it refuses construction when the selected runtime is not
    installed. The command is built as an argv list, with no host environment
    inheritance and no shell interpolation.
    """

    strong_isolation = True
    network_enforced = True

    def __init__(
        self,
        runtime: str,
        image: str,
        base_dir: str | Path,
    ) -> None:
        runtime = str(runtime or "").strip().lower()
        if runtime not in {"docker", "podman"}:
            raise SandboxError("container runtime must be docker or podman")
        executable = shutil.which(runtime)
        if executable is None:
            raise SandboxError(f"requested container runtime is not installed: {runtime}")
        image = str(image or "").strip()
        if not image or "\x00" in image or any(char.isspace() for char in image):
            raise SandboxError("container image must be a single non-empty image reference")
        self.runtime = runtime
        self.executable = executable
        self.image = image
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.provider_kind = runtime
        self.isolation_level = "container"

    def provision(self, sandbox_id: str, home_ref: str) -> None:
        sandbox_id = _safe_id(sandbox_id, "sandbox id")
        home = Path(home_ref).expanduser().resolve()
        _confined(home, self.base_dir, "container sandbox home")
        home.mkdir(parents=True, exist_ok=True)
        (home.parent / "archives").mkdir(parents=True, exist_ok=True)
        (home.parent / "snapshots").mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        sandbox_id: str,
        run_id: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        resource_policy: Mapping[str, Any],
    ) -> SandboxExecutionResult:
        sandbox_id = _safe_id(sandbox_id, "sandbox id")
        run_id = _safe_id(run_id, "run id")
        argv = _argv(command)
        if not isinstance(environment, Mapping):
            raise SandboxPolicyViolation("sandbox environment must be an explicit mapping")
        network = str(resource_policy.get("network_policy", "deny")).strip().lower()
        if network not in {"deny", "none"}:
            raise SandboxPolicyViolation("container sandbox only supports network deny")
        if resource_policy.get("network_domains"):
            raise SandboxPolicyViolation(
                "container sandbox cannot enforce a network domain allowlist"
            )
        home = self._home(sandbox_id)
        job = home.parent / "jobs" / run_id
        _confined(job, home.parent, "container sandbox job")
        job.mkdir(parents=True, exist_ok=True)
        (job / "tmp").mkdir(exist_ok=True)
        mounts = self._mounts(resource_policy.get("mounts") or ())
        name = f"astra-{sandbox_id}-{run_id}"[:120]
        container_argv = [
            self.executable,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(_positive_limit(resource_policy.get("max_processes"), 256)),
            "--mount",
            f"type=bind,src={home},dst=/home/agent,rw",
            "--mount",
            f"type=bind,src={job},dst=/workspace,rw",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec",
        ]
        cpu_millis = resource_policy.get("cpu_millis")
        if cpu_millis is not None:
            container_argv.extend(["--cpus", str(_positive_limit(cpu_millis, 1000) / 1000)])
        memory_mib = resource_policy.get("memory_mib")
        if memory_mib is not None:
            container_argv.extend(["--memory", f"{_positive_limit(memory_mib, 512)}m"])
        disk_mib = resource_policy.get("disk_mib")
        if disk_mib is not None:
            container_argv.extend(["--storage-opt", f"size={_positive_limit(disk_mib, 1024)}m"])
        for source, target, access in mounts:
            mode = "ro" if access == "read" else "rw"
            container_argv.extend(["--mount", f"type=bind,src={source},dst={target},{mode}"])
        explicit_environment: dict[str, str] = {}
        reserved = frozenset(
            {"PATH", "HOME", "PWD", "TMPDIR", "LANG", "LC_ALL", "ASTRA_SANDBOX_ID", "ASTRA_RUN_ID"}
        )
        for key, value in environment.items():
            key = str(key).strip()
            if not key or "=" in key or "\x00" in key:
                raise SandboxPolicyViolation("sandbox environment key is malformed")
            if key in reserved:
                raise SandboxPolicyViolation(f"sandbox environment cannot override {key}")
            if not isinstance(value, str) or "\x00" in value:
                raise SandboxPolicyViolation("sandbox environment values must be strings without NUL bytes")
            explicit_environment[key] = value
            # Keep secret material out of the container client's argv. The
            # runtime reads the value from the explicit subprocess environment.
            container_argv.extend(["--env", key])
        container_argv.extend(["--workdir", "/workspace", self.image, *argv])
        started = _utc_now()
        timeout = resource_policy.get("max_wall_seconds")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
                raise SandboxPolicyViolation("max_wall_seconds must be a positive integer")
        client_environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **explicit_environment,
        }
        process = subprocess.Popen(
            container_argv,
            cwd=job,
            env=client_environment,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=False,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        finally:
            if timed_out:
                subprocess.run(
                    [self.executable, "rm", "-f", name],
                    env=client_environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        finished = _utc_now()
        max_output = _positive_limit(resource_policy.get("max_output_bytes"), 1_000_000)
        return SandboxExecutionResult(
            sandbox_id=sandbox_id,
            run_id=run_id,
            job_path=str(job),
            command=tuple(argv),
            exit_code=None if timed_out else process.returncode,
            stdout=stdout[:max_output].decode("utf-8", "replace"),
            stderr=stderr[:max_output].decode("utf-8", "replace"),
            timed_out=timed_out,
            started_at=started,
            finished_at=finished,
            isolation_level=self.isolation_level,
            network_enforced=True,
        )

    def health(self, sandbox_id: str) -> Mapping[str, Any]:
        home = self._home(sandbox_id)
        return {
            "sandbox_id": sandbox_id,
            "provider_kind": self.provider_kind,
            "isolation_level": self.isolation_level,
            "strong_isolation": True,
            "network_enforced": True,
            "runtime_available": Path(self.executable).exists(),
            "home_exists": home.is_dir(),
        }

    def suspend(self, sandbox_id: str) -> None:
        self._home(sandbox_id)

    def resume(self, sandbox_id: str) -> None:
        self._home(sandbox_id).mkdir(parents=True, exist_ok=True)

    def reset(self, sandbox_id: str) -> None:
        home = self._home(sandbox_id)
        if not home.exists():
            home.mkdir(parents=True, exist_ok=True)
            return
        archive = home.parent / "archives" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True, exist_ok=False)
        shutil.move(str(home), str(archive / "home"))
        home.mkdir(parents=True, exist_ok=True)

    def archive(self, sandbox_id: str) -> None:
        home = self._home(sandbox_id)
        if not home.exists():
            return
        archive = home.parent / "archives" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True, exist_ok=False)
        shutil.move(str(home), str(archive / "home"))

    def _home(self, sandbox_id: str) -> Path:
        return _confined(self.base_dir / _safe_id(sandbox_id, "sandbox id") / "home", self.base_dir, "container sandbox home")

    @staticmethod
    def _mounts(mounts: Sequence[Mapping[str, Any]]) -> list[tuple[Path, str, str]]:
        if not isinstance(mounts, Sequence):
            raise SandboxPolicyViolation("sandbox mounts must be a sequence")
        result: list[tuple[Path, str, str]] = []
        for mount in mounts:
            if not isinstance(mount, Mapping):
                raise SandboxPolicyViolation("sandbox mount must be an object")
            source = Path(str(mount.get("source_ref") or "")).expanduser().resolve()
            target = str(mount.get("target_path") or "").strip()
            access = str(mount.get("access_mode") or "").strip()
            if not source.exists():
                raise SandboxPolicyViolation(f"sandbox mount source does not exist: {source}")
            if (
                not target.startswith("/")
                or target == "/"
                or target in {"/proc", "/sys", "/dev"}
                or "," in target
                or "\x00" in target
                or any(part == ".." for part in Path(target).parts)
            ):
                raise SandboxPolicyViolation("sandbox mount target must be an absolute confined path")
            if access not in {"read", "write"}:
                raise SandboxPolicyViolation("sandbox mount access mode must be read or write")
            result.append((source, target, access))
        return result


class ExternalSandboxProvider:
    """Adapter facade for a strong container/remote runtime.

    The adapter must explicitly attest to strong isolation and network
    enforcement; otherwise construction fails closed instead of relabelling a
    folder provider as secure.
    """

    def __init__(self, adapter: SandboxAdapter) -> None:
        if not adapter.strong_isolation or not adapter.network_enforced:
            raise SandboxError("external sandbox adapter must enforce isolation and network policy")
        self.adapter = adapter
        self.provider_kind = adapter.provider_kind
        self.isolation_level = adapter.isolation_level
        self.strong_isolation = True
        self.network_enforced = True

    def provision(self, sandbox_id: str, home_ref: str) -> None:
        self.adapter.provision(sandbox_id, home_ref)

    def prepare_job(self, sandbox_id: str, run_id: str) -> Path:
        raise SandboxError("external providers own job paths; use execute directly")

    def execute(self, sandbox_id: str, run_id: str, command: Sequence[str], *, environment: Mapping[str, str], resource_policy: Mapping[str, Any]) -> SandboxExecutionResult:
        return self.adapter.execute(sandbox_id, run_id, command, environment=environment, resource_policy=resource_policy)

    def health(self, sandbox_id: str) -> Mapping[str, Any]:
        return self.adapter.health(sandbox_id)

    def suspend(self, sandbox_id: str) -> None:
        self.adapter.suspend(sandbox_id)

    def resume(self, sandbox_id: str) -> None:
        self.adapter.resume(sandbox_id)

    def reset(self, sandbox_id: str) -> None:
        self.adapter.reset(sandbox_id)

    def archive(self, sandbox_id: str) -> None:
        self.adapter.archive(sandbox_id)


class SandboxManager:
    """Persist sandbox state and delegate execution to a provider."""

    def __init__(
        self,
        repository: SandboxRepository,
        base_dir: str | Path,
        *,
        provider: SandboxProvider | None = None,
    ) -> None:
        self.repository = repository
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider or LocalSandboxProvider(self.base_dir)

    def provision(self, sandbox_id: str, workspace_id: str, agent_id: str) -> dict[str, Any]:
        sandbox_id = _safe_id(sandbox_id, "sandbox id")
        existing = self.repository.get_sandbox(sandbox_id)
        home = self.base_dir / sandbox_id / "home"
        if existing is None:
            record = self.repository.create_sandbox(
                sandbox_id,
                workspace_id,
                agent_id,
                self.provider.provider_kind,
                self.provider.isolation_level,
                str(home),
            )
        else:
            if existing["workspace_id"] != workspace_id or existing["agent_id"] != agent_id:
                raise SandboxError("sandbox identity belongs to another workspace or agent")
            if existing["isolation_level"] != self.provider.isolation_level:
                raise SandboxError("sandbox provider isolation level cannot change in place")
            record = existing
            if record["status"] == SandboxStatus.ARCHIVED.value:
                record = self.repository.transition_status(sandbox_id, SandboxStatus.PROVISIONING)
        self.provider.provision(sandbox_id, str(home))
        if record["status"] == SandboxStatus.PROVISIONING.value:
            record = self.repository.transition_status(sandbox_id, SandboxStatus.READY)
        return self.repository.get_sandbox(sandbox_id) or record

    def execute(
        self,
        sandbox_id: str,
        run_id: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        resource_policy: Mapping[str, Any] | None = None,
        mounts: Sequence[Mapping[str, Any]] = (),
    ) -> SandboxExecutionResult:
        sandbox = self.repository.get_sandbox(sandbox_id)
        if sandbox is None or sandbox["status"] != SandboxStatus.READY.value:
            raise SandboxNotReadyError(f"sandbox is not ready: {sandbox_id}")
        selected_mounts = mounts or (resource_policy or {}).get("mounts") or sandbox.get("mounts", ())
        policy = dict(sandbox.get("resource_policy") or {})
        policy.update(dict(resource_policy or {}))
        if selected_mounts:
            policy["mounts"] = [dict(mount) for mount in selected_mounts]
        if isinstance(self.provider, LocalSandboxProvider):
            job = self.provider.prepare_job(sandbox_id, run_id)
            self._materialize_mounts(job, selected_mounts)
        self.repository.transition_status(sandbox_id, SandboxStatus.BUSY)
        try:
            result = self.provider.execute(
                sandbox_id,
                run_id,
                command,
                environment=environment or {},
                resource_policy=policy,
            )
        except Exception:
            self.repository.transition_status(sandbox_id, SandboxStatus.ERROR)
            raise
        self.repository.transition_status(sandbox_id, SandboxStatus.READY)
        return result

    def health(self, sandbox_id: str) -> Mapping[str, Any]:
        return self.provider.health(sandbox_id)

    def suspend(self, sandbox_id: str) -> dict[str, Any]:
        sandbox = self._require(sandbox_id)
        if sandbox["status"] == SandboxStatus.READY.value:
            self.provider.suspend(sandbox_id)
            self.repository.transition_status(sandbox_id, SandboxStatus.SUSPENDED)
        return self.repository.get_sandbox(sandbox_id) or sandbox

    def resume(self, sandbox_id: str) -> dict[str, Any]:
        sandbox = self._require(sandbox_id)
        self.provider.resume(sandbox_id)
        if sandbox["status"] in {SandboxStatus.SUSPENDED.value, SandboxStatus.OFFLINE.value}:
            self.repository.transition_status(sandbox_id, SandboxStatus.READY)
        return self.repository.get_sandbox(sandbox_id) or sandbox

    def restart(self, sandbox_id: str) -> dict[str, Any]:
        """Re-provision a stopped or unhealthy sandbox and make it ready.

        Restart is intentionally different from ``resume``: an error or
        archived sandbox must pass through the durable provisioning state so a
        crash cannot leave the database claiming that it is ready.
        """

        sandbox = self._require(sandbox_id)
        status = sandbox["status"]
        if status == SandboxStatus.BUSY.value:
            raise SandboxError("busy sandbox must be stopped before restart")
        if status == SandboxStatus.READY.value:
            return sandbox
        if status == SandboxStatus.SUSPENDED.value:
            self.provider.resume(sandbox_id)
            self.repository.transition_status(sandbox_id, SandboxStatus.READY)
            return self.repository.get_sandbox(sandbox_id) or sandbox
        if status != SandboxStatus.PROVISIONING.value:
            self.repository.transition_status(sandbox_id, SandboxStatus.PROVISIONING)
        try:
            self.provider.provision(sandbox_id, sandbox["home_ref"])
            self.repository.transition_status(sandbox_id, SandboxStatus.READY)
        except Exception:
            try:
                self.repository.transition_status(sandbox_id, SandboxStatus.ERROR)
            except Exception:
                pass
            raise
        return self.repository.get_sandbox(sandbox_id) or sandbox

    def reset(self, sandbox_id: str) -> dict[str, Any]:
        sandbox = self._require(sandbox_id)
        if sandbox["status"] == SandboxStatus.BUSY.value:
            raise SandboxError("busy sandbox must be stopped before reset")
        self.provider.reset(sandbox_id)
        if sandbox["status"] == SandboxStatus.SUSPENDED.value:
            self.repository.transition_status(sandbox_id, SandboxStatus.READY)
        return self.repository.get_sandbox(sandbox_id) or sandbox

    def archive(self, sandbox_id: str) -> dict[str, Any]:
        sandbox = self._require(sandbox_id)
        if sandbox["status"] == SandboxStatus.BUSY.value:
            raise SandboxError("busy sandbox must be stopped before archive")
        self.provider.archive(sandbox_id)
        if sandbox["status"] != SandboxStatus.ARCHIVED.value:
            self.repository.transition_status(sandbox_id, SandboxStatus.ARCHIVED)
        return self.repository.get_sandbox(sandbox_id) or sandbox

    def snapshot(self, sandbox_id: str, snapshot_id: str) -> str:
        sandbox = self._require(sandbox_id)
        if not isinstance(self.provider, LocalSandboxProvider):
            raise SandboxError("snapshot storage must be implemented by the external provider")
        source = Path(sandbox["home_ref"]).expanduser().resolve()
        target = source.parent / "snapshots" / _safe_id(snapshot_id, "snapshot id")
        _confined(target, source.parent, "sandbox snapshot")
        if target.exists():
            raise SandboxError("snapshot already exists")
        shutil.copytree(source, target, symlinks=True)
        return str(target)

    def _materialize_mounts(self, job: Path, mounts: Sequence[Mapping[str, Any]]) -> None:
        if not isinstance(mounts, Sequence):
            raise SandboxPolicyViolation("sandbox mounts must be a sequence")
        for mount in mounts:
            if not isinstance(mount, Mapping):
                raise SandboxPolicyViolation("sandbox mount must be an object")
            source = Path(str(mount.get("source_ref") or "")).expanduser().resolve()
            target_raw = str(mount.get("target_path") or "")
            target = _mount_target(job, target_raw)
            access = str(mount.get("access_mode") or "")
            if access not in {"read", "write"}:
                raise SandboxPolicyViolation("sandbox mount access mode must be read or write")
            if not source.exists():
                raise SandboxPolicyViolation(f"sandbox mount source does not exist: {source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise SandboxPolicyViolation(f"sandbox mount target already exists: {target_raw}")
            if access == "read":
                if source.is_dir():
                    shutil.copytree(source, target, symlinks=False)
                else:
                    shutil.copy2(source, target, follow_symlinks=True)
            else:
                if self.provider.strong_isolation:
                    raise SandboxPolicyViolation("write mounts require provider-enforced mount mediation")
                target.symlink_to(source, target_is_directory=source.is_dir())

    def _require(self, sandbox_id: str) -> dict[str, Any]:
        sandbox = self.repository.get_sandbox(sandbox_id)
        if sandbox is None:
            raise SandboxError(f"sandbox not found: {sandbox_id}")
        return sandbox


def _argv(command: Sequence[str]) -> list[str]:
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence) or not command:
        raise SandboxPolicyViolation("sandbox command must be a non-empty argv sequence")
    argv = [str(part) for part in command]
    if any(not part or "\x00" in part for part in argv):
        raise SandboxPolicyViolation("sandbox command contains an empty or NUL argument")
    return argv


def _safe_id(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise SandboxPolicyViolation(f"{label} is not path-safe")
    return value


def _confined(path: Path, root: Path, label: str) -> Path:
    path = path.resolve()
    root = root.resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise SandboxPolicyViolation(f"{label} escapes its provider root") from error
    return path


def _mount_target(job: Path, raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise SandboxPolicyViolation("sandbox mount target is required")
    relative = raw.lstrip("/")
    target = (job / relative).resolve()
    return _confined(target, job, "sandbox mount target")


def _positive_limit(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SandboxPolicyViolation("output limit must be a positive integer")
    return value


def _apply_resource_limits(policy: Mapping[str, Any]) -> None:
    if _resource is None:
        return
    limits = (
        ("max_wall_seconds", _resource.RLIMIT_CPU, 1),
        ("memory_mib", _resource.RLIMIT_AS, 1024 * 1024),
        ("max_processes", _resource.RLIMIT_NPROC, 1),
    )
    for key, limit_kind, multiplier in limits:
        value = policy.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SandboxPolicyViolation(f"{key} must be a positive integer")
        hard = value * multiplier
        try:
            _resource.setrlimit(limit_kind, (hard, hard))
        except (ValueError, OSError):
            # The host may impose a lower hard limit.  The provider remains weak
            # isolation, so the caller sees process failure rather than a false
            # claim that the quota was enforced.
            return


def _directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
