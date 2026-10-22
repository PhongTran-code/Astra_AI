# T03 — Permissions and governance

Status: complete
Dependencies: T01, T02  
Unlocks: T05–T15

## Outcome

Make every Team action requester-aware and enforce capability, connector,
conversation, task, sandbox, and approval boundaries in one backend policy path.

## Checklist

- [x] Introduce stable actor identity for humans, agents, and system actions.
- [x] Implement workspace and group roles.
- [x] Implement the effective-authority intersection from `target-state.md`.
- [x] Bind each WorkRequest to its requester and target agent.
- [x] Separate agent-owner authority from teammate request authority.
- [x] Define everyday actions versus external-identity actions.
- [x] Preserve current hard-deny and human-only floors.
- [x] Scope filesystem mounts and network egress per sandbox/run.
- [x] Scope connector grants by agent, requester, action, and resource.
- [x] Add approval expiry, cancellation, revalidation, and audit attribution.
- [x] Reject prompt-based attempts to alter grants, budget, mounts, or role.
- [x] Redact secrets and sensitive payloads from logs and notifications.
- [x] Add import/install permission review for plugins and agent packages.

## Acceptance criteria

- [x] UI manipulation cannot bypass a backend policy.
- [x] A group member cannot act through the creator's email/social identity without
      explicit authority.
- [x] Revoked grants fail on the next consequential action.
- [x] A run cannot expand its own sandbox mounts, network policy, or budget.
- [x] DM and private artifacts cannot be read through group membership.
- [x] Every allow, deny, and approval records actor, requester, reason, and scope.

## Verification

- Permission matrix tests across owner, member, agent, system, DM, group, task,
  connector, and sandbox combinations.
- Prompt injection, confused-deputy, stale grant, forged actor, and replay tests.

## Likely code surfaces

- `coworker/permissions.py`
- `coworker/reviewer.py`
- `coworker/audit.py`
- `coworker/server/app.py`
- `coworker/server/manager.py`
- Connector and sandbox policy modules

## Completion evidence

- `coworker/teams/permissions.py` adds finite workspace/agent roles, a literal
  ten-layer effective-authority intersection, exact requester/agent/resource connector
  grants, expiring/revocable approvals, immutable sandbox policy, policy audit records,
  prompt-policy mutation rejection, recursive secret redaction, and capability-install
  review.
- `TeamPermissionService` is the backend entry point and composes the existing
  `PermissionEngine` tool hard-deny/human-only floor.
- `GovernanceRepository` provides approval expiry/cancel/revalidation and grant revoke;
  `WorkRepository` binds every durable request to requester and target installations.
- `12 passed`: role matrix, connector scope and approval, requester/target binding,
  sandbox policy, legacy tool floor, policy audit, prompt injection, redaction, and
  capability-install review.
