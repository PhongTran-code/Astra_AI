# T01 — Product and domain contracts

Status: complete
Dependencies: none
Unlocks: all remaining tasks

## Outcome

Freeze the language, entity boundaries, state machines, and API-level invariants
that every Team subsystem implements.

## Scope

- Canonical product vocabulary and IDs
- Actor and workspace model
- Agent definition/revision/installation model
- Conversation, thread, task, crew, run, delivery, sandbox, and artifact contracts
- `WorkRequest` and event envelope
- State machines and transition authority
- Permission-intersection contract
- Privacy and memory scopes
- Error taxonomy and idempotency contract

Frozen specification: [Astra Team domain contract v1](domain-contracts.md)

## Checklist

- [x] Define stable IDs and prohibit ambiguous `team_id` reuse.
- [x] Define actor types: human, agent, system.
- [x] Define DM and group semantics.
- [x] Define deterministic mention and coordinator routing.
- [x] Define `@all`, bot-to-bot handoff, fan-out, and loop limits.
- [x] Define task ownership, dependency readiness, and independent review rules.
- [x] Define WorkRequest fields and source types.
- [x] Define delivery, run, sandbox, task, approval, and agent state transitions.
- [x] Define REST command/query and realtime event envelopes.
- [x] Define context and memory scopes, including explicit-share rules.
- [x] Define artifact publication and visibility semantics.
- [x] Define compatibility mapping from current sessions, teams, boards, and chats.

## Acceptance criteria

- [x] Every full product flow can be described without an undefined entity.
- [x] Two modules never use the same ID name for different meanings.
- [x] Every mutable business object has one authoritative state owner.
- [x] Every transition identifies who may perform it.
- [x] Every side effect carries requester, correlation, and idempotency identity.
- [x] The contracts cover loading, retry, cancellation, offline, and deletion paths.

## Verification

- Contract walkthrough: DM, group mention, group coordinator, task assignment,
  automation, external connector event, handoff, approval, failure, and restart.
- Adversarial walkthrough: duplicate frame, stale revision, unauthorized requester,
  canceled parent run, sandbox offline, and dependency cycle.

## Completion evidence

- `plan/domain-contracts.md` is the frozen v1 specification, including the current
  compatibility mapping and recovery/deletion behavior.
- `coworker/teams/contracts.py`, `routing.py`, `lifecycle.py`, `sharing.py`, and
  `api_contracts.py` encode the executable invariants.
- `109 passed`: isolated domain-contract test suite.
- `227 passed`: all Team tests, including existing board/chat/wake/store regressions.
- `python -m compileall -q coworker/teams` and `git diff --check` passed.

## Likely code surfaces

- `coworker/sessions.py`
- `coworker/teams/model.py`
- `coworker/events.py`
- `coworker/personas/manifest.py`
- `surfaces/gui/src/types.ts`
- New Team domain modules and API schemas
