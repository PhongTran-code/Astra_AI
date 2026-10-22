# T12 — Realtime, presence, and notifications

Status: complete
Dependencies: T04, T06, T07  
Unlocks: T13–T15

## Outcome

Replace Team polling with durable-query plus realtime-event behavior, expose
truthful agent/sandbox status, and deliver actionable notifications.

## Checklist

- [x] Add a typed Team WebSocket stream alongside the app-wide event stream.
- [x] Add event sequence/cursor and REST catch-up after reconnect.
- [x] Reconcile optimistic messages by client request ID.
- [x] Derive agent presence from sandbox, run, delivery, and approval state.
- [x] Expose ready, working, waiting, paused, offline, error, and archived states.
- [x] Add conversation/thread unread and read-cursor updates.
- [x] Add a durable high-signal Activity projection for conversation/message, task,
      approval, automation, connector, context, sandbox, notification, handoff,
      integration, memory, and terminal run outcomes; keep raw run/tool chatter out.
- [x] Add mute, mention-only, quiet-hours, and per-event preferences.
- [x] Add desktop notification; [x] add recipient-safe deep-link handling.
- [x] Add notification dedupe, read, and cursor pagination.
- [x] Keep raw tool chatter out of notifications.
- [x] Emit canonical worker reply events only after the reply is durably persisted.

## Acceptance criteria

- [x] No Team screen polls every few seconds.
- [x] Reconnect catches up every missed durable event exactly once in the UI.
- [x] Presence never shows ready when the sandbox is unavailable.
- [x] Unread counts remain correct across multiple windows and restarts.
- [x] Notification links open the exact message, task, approval, or run.
- [x] Muted groups still preserve data and explicit mentions according to policy.

## Verification

- Disconnect/reconnect, duplicate/out-of-order event, multiple windows, stale
  cursor, archived object, quiet hours, and desktop deep-link tests.

## Likely code surfaces

- `/ws/events` in `coworker/server/app.py`
- Event broadcasting in `coworker/server/manager.py`
- `surfaces/gui/src/api.ts`
- New Team client state and notification components

## Evidence

`/ws/team` sends durable cursor catch-up followed by typed live events; the GUI
deduplicates by sequence and reconnects from its last cursor. Canonical worker
replies emit durable message/notification events only after output persistence, and
provider failures do not emit a successful delivery. Notifications are durable,
recipient-scoped, deduplicated, cursor-paginated, individually readable, and emitted
without raw message/tool bodies. The Activity projection is cursor-backed, redacts
secrets while preserving safe domain IDs for deep links, and is available through
`/v1/team/activity` and the Team Activity view. Quiet-hours evaluation handles timezone
conversion and overnight windows, while explicit mentions bypass suppression. The focused
Team/server tests cover catch-up, notification read/list behavior, unread cursors,
custom-worker output, and duplicate command delivery. Presence derives from installation,
sandbox, queue, and active-run state and never reports ready without a usable sandbox.

Latest verification: full Python suite passes; focused Activity/service/server
and per-member event ACL tests pass.
