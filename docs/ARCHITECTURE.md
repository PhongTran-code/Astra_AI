# Astra Architecture Reference

> updated 2026-09-05 · v0.2.4

This document is the authoritative technical reference for Astra's internal architecture, component boundaries, execution pipelines, and security model.

---

## 1. System Overview

Astra is built as a local-first desktop application composed of a **native desktop shell**, an **embedded web surface**, a **supervised Python runtime sidecar**, and an **STT audio processing sidecar**.

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Tauri v2 Desktop Shell                   │
│          Window management · OS tray · Sidecar supervision       │
├─────────────────────────────────────────────────────────────────┤
│                     React 18 / TypeScript Webview               │
│        Chat surface · Artifact preview · Team board · Settings  │
└────────────────────────────────┬────────────────────────────────┘
                                 │ HTTP & WebSocket (Launch token)
┌────────────────────────────────▼────────────────────────────────┐
│               FastAPI Agent Server (Python 3.10+)                │
│                                                                 │
│  ┌───────────────────────────┐   ┌───────────────────────────┐  │
│  │   Session & Team Manager  │   │  Canonical Work Pipeline  │  │
│  └─────────────┬─────────────┘   └─────────────┬─────────────┘  │
│                │                               │                │
│  ┌─────────────▼─────────────┐   ┌─────────────▼─────────────┐  │
│  │   Governance & Hard Floor │   │     Tool Execution Engine │  │
│  │   Reviewer · Audit Store  │   │     FS · Shell · MCP · Web│  │
│  └─────────────┬─────────────┘   └─────────────┬─────────────┘  │
│                │                               │                │
│  ┌─────────────▼─────────────┐   ┌─────────────▼─────────────┐  │
│  │   Model Provider Router   │   │  25+ External Connectors  │  │
│  │   aisuite · BYOK · Ollama │   │  Slack, GitHub, Jira, etc.│  │
│  └───────────────────────────┘   └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                 │ Audio IPC (Local port)
┌────────────────────────────────▼────────────────────────────────┐
│                    Rust Speech-to-Text Sidecar                  │
│                     Whisper-based local audio STT               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Subsystems

### 2.1 Tauri v2 Desktop Shell (`surfaces/gui/src-tauri/`)
- **Lifecycle & Process Supervision**: Spawns and supervises the `astra-server` binary and the `ocw-stt` sidecar.
- **Port Management**: Dynamically assigns and injects `window.__COWORKER_HTTP__` and `window.__COWORKER_WS__` into the webview.
- **IPC & Windowing**: Native frameless windowing, OS menu integration, dynamic tray icon, and multi-monitor window resizing.
- **Auto-Updater**: Integrates `tauri-plugin-updater` with Minisign cryptographic verification against a public Vercel manifest and signed artifacts, while the source repository remains private.

### 2.2 Frontend Application (`surfaces/gui/`)
- **Framework**: React 18 with TypeScript, bundled using Vite.
- **State & Streaming**: Real-time streaming parser for model tokens, reasoning steps, tool approval cards, and artifact generation.
- **Resilience**:
  - Auto-reconnecting WebSocket client (`Session` class in `api.ts`).
  - Active liveness reconciliation effect against backend state machines to prevent UI stalls.
  - Multi-session local storage and per-conversation scratchpad workspaces.

### 2.3 Local Agent Server (`coworker/server/`)
- **FastAPI & Async IO**: Operates an event-driven server handling concurrent session endpoints, artifact serving, and WebSocket telemetry.
- **Session Registry**: Persists conversations to disk (`conversations/<session_id>.jsonl`) and tracks active turn execution status.
- **Launch Authentication**: Enforces a cryptographically generated one-time launch token (`X-Astra-Token`) on all REST and WebSocket connections.

### 2.4 Multi-Agent Teams (`coworker/teams/`)
- **Persistent Teammates**: Long-lived agent personas (`Cowork`, `Code`, `Architect`, `Reviewer`, etc.) with specialized system prompts and tool grants.
- **Work Isolation**: Per-task isolated scratchpads and git worktrees preventing agents from mutating active branches unexpectedly.
- **Coordinator**: Deterministic routing engine managing task dependencies, agent delegation, and message delivery.

---

## 3. Canonical Work Pipeline

Every action—whether initiated via chat, background automation, external Slack mention, or agent-to-agent delegation—converges on the canonical **WorkRequest** pipeline:

```text
Source Event (User prompt, Cron automation, Connector webhook, Agent handoff)
  │
  ▼
Durable Persistence (SQLite / JSONL record created before execution)
  │
  ▼
Deterministic Routing & Authorization Gate (Platform ceiling ∩ Role ∩ Tool policy)
  │
  ▼
Durable Delivery & Lease Acquisition (Heartbeat monitored by coordinator)
  │
  ▼
Scoped Context Package (Retrieval from relevant memory, task files, and conversation)
  │
  ▼
Isolated Sandbox / Runtime Execution
  │
  ▼
Governance Interceptor (Hard floor check → Reviewer model → Human approval if needed)
  │
  ▼
Terminal State & Side Effects (Tool executed, file written, response finalized)
  │
  ▼
Projections & Notifications (WebSocket event broadcast, desktop alerts, audit log)
```

---

## 4. Governance & Security Boundary

Governance in Astra is a backend security floor, not client-side UI visibility.

### 4.1 Hard Floors
A set of irreversible actions cannot be bypassed by prompts, agent reasoning, or "Auto-approve" mode:
- Deleting files or wiping workspace directories.
- Pushing to protected git branches (`main`, `master`, `production`).
- Modifying production infrastructure or cloud credentials.
- External data sharing or publishing packages.

These actions **always** escalate to a human approval card in the UI.

### 4.2 Autonomous Reviewer Model
In auto-approve modes, an independent reviewer model sits between the working agent and the execution environment:
- Evaluates the proposed tool call and input arguments against safety policies.
- If uncertain, pauses execution and yields to human approval.
- Emits structured reasoning persisted in the audit trail.
- Contains a circuit breaker: consecutive rejections by the user automatically pause the autonomous reviewer.

### 4.3 Effective Authority Equation
Effective authority is strictly evaluated as the intersection of all constraints:

$$\text{Authority} = \text{Platform Ceiling} \cap \text{Workspace Policy} \cap \text{Agent Role} \cap \text{Tool Grant} \cap \text{Task Scope} \cap \text{Human Approval}$$

---

## 5. Storage & Persistence Model

- **Session History**: Stored as append-only JSONL files (`~/.config/coworker/conversations/<session_id>.jsonl`), ensuring zero data loss during process crashes.
- **Team Database**: SQLite database managing teams, persistent agent definitions, task boards, and background automations.
- **Local Secrets Store**: Model API keys, connector OAuth tokens, and sensitive configurations are stored locally in the platform keytar / encrypted settings file.
- **Artifacts**: User-facing deliverables (markdown reports, source files, HTML pages) are staged in designated project scratch spaces before promotion.

---

## 6. Model Provider Abstraction

Astra is provider-agnostic, built on a flexible capability router:
- Supported cloud providers: **OpenAI**, **Anthropic**, **Google Gemini**, **DeepSeek**, **xAI Grok**, **Moonshot Kimi**, **BytePlus**, **Qwen**, **Mistral**, **MiniMax**.
- Supported local runtimes: **Ollama**, **vLLM**, and any OpenAI-compatible HTTP endpoint.
- Context compaction: Background turn summarization preserves long-running session coherence without exceeding context limits.

---

## 7. Packaging & Distribution

The application is packaged with zero runtime dependencies for the end user:
1. **Python Sidecar**: Frozen using PyInstaller into an `astra-server` onedir bundle with all wheels and dependencies embedded.
2. **Rust STT Sidecar**: Compiled to native target binaries (`ocw-stt`).
3. **Tauri Bundler**: Combines the React GUI, server onedir, STT binary, and window manager into:
   - **Linux**: `.AppImage` (Universal) and `.deb` (Debian/Ubuntu).
   - **Windows**: NSIS Setup `.exe` and `.msi`.
   - **macOS**: Signed and notarized `.dmg` for Apple Silicon (`aarch64`) and Intel (`x86_64`).

Public distribution is temporarily paused during development. The manual workflow still builds Actions artifacts, but its `release` job is unconditionally skipped: no GitHub release, updater manifest, or Vercel Blob upload is published. The landing download buttons are disabled, and both `/downloads/*` and `/updates/latest.json` return `503` with `Cache-Control: no-store` before any Blob access (guarded by `DISTRIBUTION_ENABLED` in `landing/api/_distribution.js`). Existing remote releases and Blob objects are not deleted or revoked by this source change. Before resuming, configure the repository variable `ASTRA_DOWNLOAD_BASE_URL` (the new service's HTTPS base ending in `/downloads`), verify the Blob credentials and store, restore the landing buttons, enable the API guard, and re-enable the release job. Automatic updates are temporarily disabled during development: `UPDATES_ENABLED` in `surfaces/gui/src-tauri/src/lib.rs` is false and the updater endpoints in `tauri.conf.json` are empty. The shell rejects check/download/install commands, the banner does not schedule checks, and Settings shows a development notice. Before re-enabling, configure a new update endpoint and the release workflow download URL, verify control of the Blob store and matching signing key, then enable the shell flag. The retained updater uses `latest.json`; macOS uses signed `.app.tar.gz`, Windows uses the signed NSIS installer, and Linux uses a signed AppImage. Debian packages remain first-install downloads.
