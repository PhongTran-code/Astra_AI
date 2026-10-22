# Astra brand migration

Status: completed

## Goal

Make Astra the canonical product identity across user-facing surfaces and
transport boundaries.

The internal Python package name `coworker` remains unchanged in this plan. It
is an implementation namespace, not product branding, and changing it would
create unnecessary import, packaging, and persisted-state risk.

## Canonical contract

| Boundary | Astra canonical |
|---|---|
| CLI | `astra`, `astra-server`, `astra-connectors` |
| Feature flags | `ASTRA_*` |
| REST sidecar auth | `X-Astra-Token` |
| WebSocket protocol | `astra` |
| Python imports | `coworker` |

