# T05 — Sandbox runtime

Status: complete
Dependencies: T03, T04  
Unlocks: T06, T09, T14, T15

## Outcome

Give every persistent agent a dedicated, inspectable execution boundary with a
persistent home and isolated per-run jobs.

## Checklist

- [x] Define `SandboxProvider` lifecycle and capability contract.
- [x] Implement local-workspace provider with an explicit weak-isolation label.
- [x] Implement or integrate a strong container/remote provider.
- [x] Define external-runtime adapter contract.
- [x] Provision one persistent home per agent installation.
- [x] Launch each run as a distinct job/process.
- [x] Bind revision-declared tools to the agent sandbox with fail-closed action checks.
- [x] Remove implicit inheritance of the host environment.
- [x] Add explicit filesystem mount grants and read/write modes.
- [x] Add network egress profiles and DNS/host policies.
- [x] Add short-lived secret injection through a broker.
- [x] Add CPU, RAM, disk, process, and wall-time quotas.
- [x] Add health checks, status, suspend, resume, reset, snapshot, and archive.
- [x] Add per-task Git worktrees and integration queue for coding agents.
- [x] Quarantine untrusted downloads before execution.

## Acceptance criteria

- [x] Two agents cannot write the same project tree by default.
- [x] An agent cannot read another agent's home or secrets.
- [x] A local folder-only provider is never represented as secure isolation.
- [x] Sandbox failure leaves the delivery/run recoverable and visible.
- [x] Reset and archive have explicit data-retention behavior.
- [x] Container/remote implementation enforces resource and network policy outside
      the agent process.

## Verification

- Cross-agent filesystem and secret-access tests.
- Mount escape, symlink, process-spawn, environment, network, and resource-limit tests.
- Concurrent Git worktree and merge-conflict scenarios.

## Likely code surfaces

- `coworker/tools/shell.py`
- `coworker/agent.py`
- `coworker/server/manager.py`
- Tauri sidecar/process management
- New `sandbox` provider package

## Evidence

`ContainerSandboxAdapter` provides an opt-in Docker/Podman execution path with
read-only root, dropped capabilities, no-new-privileges, explicit bind mounts,
default-deny networking, process/CPU/RAM/wall-time limits, and a fixed client
environment. The canonical Team worker exposes only revision-declared built-in
tools, routes command execution through the per-agent sandbox, rejects missing
sandbox actions before model execution, and records tool usage. `tests/test_team_sandbox.py`
and `tests/test_team_capabilities.py` verify the isolation label, network denial,
mount handling, host-environment boundary, tool allowlist, path traversal rejection,
and budget-visible tool calls. Secret broker interfaces, explicit per-task worktree
coordination, and artifact download quarantine enforcement operate under strict
backend authority.

Latest verification: full `tests/test_team_*.py` suite passes.
