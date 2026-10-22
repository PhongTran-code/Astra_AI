# Astra Runtime Reference

This is the canonical local-development reference for starting, testing, and stopping the Astra backend and GUI.

## Source of truth

- `pyproject.toml` exposes `astra-server` as the primary server CLI.
- `coworker/server/run.py` owns the server CLI flags and writes the standalone launch token for the selected API port.
- `surfaces/gui/vite.config.ts` owns the Vite development port and requires `1420` with `strictPort`.
- `surfaces/gui/src-tauri/tauri.conf.json` points the Tauri webview at `http://localhost:1420`.

## Canonical ports

| Surface | Address | Rule |
|---|---|---|
| Standalone API server | `127.0.0.1:8765` | Use this port for the manual server and its launch token. |
| Browser/Tauri Vite dev server | `localhost:1420` | Keep this port fixed; Tauri `devUrl` must match it. |

Do not silently replace these values with `8766`, `1421`, `5173`, or another free port.
A temporary override is valid only when every consumer receives the same value and the override is recorded in the run report.

Check both ports before starting a second process:

```bash
ss -ltn '( sport = :8765 or sport = :1420 )'
```

## Start the API

From the repository root:

```bash
.venv/bin/astra-server --cwd /path/to/workspace --host 127.0.0.1 --port 8765
```

Use the current repository as the workspace when appropriate:

```bash
.venv/bin/astra-server --cwd "$PWD" --host 127.0.0.1 --port 8765
```

The standalone server creates a user-only token at `<state-dir>/sidecar-8765.token`.
Do not print or commit that token.

## Start the browser GUI

Start the API first, then from `surfaces/gui/`:

```bash
npm install             # first checkout only
npm run dev -- --host 127.0.0.1 --port 1420
```

Open `http://localhost:1420`.
Restart Vite after restarting the standalone API so it rereads the launch token.

## Start the desktop GUI

The Tauri shell starts the Vite dev server and supervises its server sidecar:

```bash
cd surfaces/gui
npm run tauri dev
```

Do not start a second manual API for the desktop flow unless the test explicitly needs one.

## Verification commands

Run the narrowest command that proves the changed behavior, then broaden by risk.

### Backend

```bash
.venv/bin/pytest tests/test_server.py -q
.venv/bin/pytest tests/test_team_*.py -q
.venv/bin/pytest tests -q
```

Use the first command for API/server behavior, the second for Team behavior, and the last for a full backend pass.

### GUI

From `surfaces/gui/`:

```bash
npx vitest run src/api.auth.test.ts src/streamGate.test.ts
npx tsc --noEmit
npm test
npm run build
npm run e2e
```

Use the targeted Vitest command for the affected unit contract, then typecheck, unit tests, build, and E2E according to risk.

### Package and desktop changes

From the repository root, run the relevant platform build after the backend and GUI checks:

```bash
bash packaging/build_linux.sh
bash packaging/build_dmg.sh
./packaging/build_windows.ps1
```

Do not claim a package is verified when only the browser GUI was checked.

## Stop and handoff

Stop a foreground process with `Ctrl-C` or the process supervisor that started it.
Record the process name, port, command, readiness result, and any expected authentication response.
Do not kill an unverified PID or change another process's port to hide a conflict.
