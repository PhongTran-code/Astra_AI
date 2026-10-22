# T09 — Artifacts, context, memory, and search

Status: complete
Dependencies: T05, T07, T08  
Unlocks: T14, T15

## Outcome

Publish agent outputs safely, build scoped context without replaying entire chats,
store provenance-aware memory, and provide permission-filtered search.

## Checklist

- [x] Separate sandbox files, shared files, and published artifacts.
- [x] Add artifact ID, revision, hash, creator, source run/task, MIME, size,
      provenance, ACL, and retention fields.
- [x] Add upload, preview, download, publish, share, compare, reveal, quarantine,
      archive, and delete operations.
- [x] Link artifacts to messages, tasks, approvals, and runs.
- [x] Implement context package builder with recent messages, summary, pinned
      decisions, task contract, relevant artifacts, and permitted memory.
- [x] Add private-agent, DM, group, task, workspace, and run memory scopes.
- [x] Record memory source, confidence, timestamp, owner, and retention.
- [x] Add explicit share/move between scopes with permission checks.
- [x] Let users inspect, edit, pin, or forget memory.
- [x] Add full-text indexes for messages, tasks, and artifact metadata.
- [x] Add permission-filtered global search and structured filters.
- [x] Add optional semantic retrieval behind the same ACL boundary.

## Acceptance criteria

- [x] Raw sandbox paths are never treated as shared links.
- [x] Every artifact resolves back to its creating run and task.
- [x] DM memory never appears in group context or search results.
- [x] Context size is bounded and does not grow linearly with conversation history.
- [x] Removed members lose access to future queries and events immediately.
- [x] Search exact-match behavior works without semantic infrastructure.

## Verification

- Large files, duplicate hashes, malicious MIME, quarantined download, deleted
  source run, permission changes, memory leakage, and long-history context tests.

## Likely code surfaces

- Existing artifact and attachment modules
- `coworker/memory/*`
- `coworker/compaction.py`
- `coworker/agent.py`
- Artifact, Files, Memory, and Search GUI surfaces

## Evidence

The context builder bounds recent history and applies conversation/agent scope
rules; explicit cross-boundary shares are persisted with provenance and ACL checks.
Artifact publication now validates opaque non-sandbox storage references, enforces
run/task provenance, supports preview/download descriptors, revision comparison,
links, archive/delete, quarantine/release, and ACL-filtered listing. Memory records
support scoped list/edit/forget and idempotent pins. Exact FTS remains authoritative
without a semantic provider; deployments may inject an optional semantic adapter
only with the already-filtered conversation/artifact allow-lists. `tests/test_team_context.py`,
`tests/test_team_sharing.py`, `tests/test_team_persistence.py`, and
`tests/test_server.py` cover the privacy and lifecycle paths.
