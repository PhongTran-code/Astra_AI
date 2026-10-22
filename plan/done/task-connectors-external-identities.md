# T10 — Connectors and external identities

Status: complete
Dependencies: T03, T06, T07  
Unlocks: T11, T14, T15

## Outcome

Allow agents and group members to use external systems without turning an agent
into a confused deputy for its creator.

## Checklist

- [x] Model external accounts/workspaces separately from Astra workspace IDs.
- [x] Bind connector grants to owner, agent, requester classes, actions, and resources.
- [x] Split read/research actions from actions performed as a person's identity.
- [x] Route inbound connector events to a conversation or agent through WorkRequest.
- [x] Preserve external message/thread identity and idempotency.
- [x] Route outbound results back to the originating external thread when allowed.
- [x] Revalidate grants immediately before consequential actions.
- [x] Keep raw credentials outside agent prompts, messages, logs, and exports.
- [x] Add connector health, reconnect, revoke, and degraded-mode states.
- [x] Add external runtime registration and authentication.
- [x] Audit requester, external identity, resource, action, and result.

## Acceptance criteria

- [x] A teammate can ask for ordinary research but cannot send as the owner without
      authority.
- [x] Revoked OAuth or connector grants stop consequential actions.
- [x] Duplicate inbound events do not create duplicate messages or runs.
- [x] External thread replies map to the correct Astra conversation/thread.
- [x] Connector outage does not lose the source event silently.

## Verification

- Multi-user requester matrix, revoked token, expired token, duplicate webhook,
  delayed event, external thread mapping, and outbound approval tests.

## Evidence

Connector identities, exact requester/agent/action/resource grants, durable inbound
events, external thread mapping, outbound outbox delivery, health fencing, reconnect
state, and audit-safe payloads are implemented. Unauthorized inbound events remain
durably recorded as ignored and replay safely. Outbound write operations enforce
requester-aware authority checks and approval governance.

Latest verification: full `tests/test_team_*.py` suite passes.
