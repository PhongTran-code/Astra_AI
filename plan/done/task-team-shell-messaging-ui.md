# T13 — Team shell and messaging UI

Status: complete
Dependencies: T06, T07, T12  
Unlocks: T14, T15

## Outcome

Add the top-level Team navigation and complete direct/group/thread messaging
experience without duplicating the app sidebar or design system.

## Checklist

- [x] Add `Team` directly below `Automations` with active and unread states.
- [x] Add `team` to the app surface router.
- [x] Change sidebar body to Team navigation while Team is active.
- [x] Add Unread, Direct, Groups, and Threads tabs.
- [x] Add per-member pinned conversations and status-aware agent rows.
- [x] Add Team home and first-run empty state.
- [x] Add conversation header, message list, composer, and optional inspector.
- [x] Add mention autocomplete using actor IDs.
- [x] Add attachment, translate, convert-to-task, and send-as-task actions.
      [x] Pin, reaction, quote, edit, delete, and copy-link actions are wired through
      the canonical message API; conversation pins are per-member, message pinning is
      admin-only, and edits use revision-aware conflict checks with tombstone semantics.
- [x] Add durable thread panel and `View in group` navigation with reply-in-thread
      flow.
- [x] Add optimistic send, draft persistence, retry, offline, and reconnect states.
- [x] Add run/progress/error/approval/artifact cards with expandable detail.
- [x] Add keyboard navigation, focus management, screen-reader labels, and
      non-color status indicators.
- [x] Reuse existing tokens/components; do not create a separate Team theme.
- [x] Retire the polling TeamChat overlay after the new surface reaches parity.

## Acceptance criteria

- [x] Team is one click from the global sidebar.
- [x] The UI does not render two competing chat sidebars.
- [x] User can create/open DM and group, mention an agent, open a thread, attach a
      file, and recover a failed send without leaving Team.
- [x] Every view covers loading, empty, partial, full, offline, denied, and error.
- [x] Sidebar collapse, Tauri titlebar, and narrow-window behavior remain correct.

## Verification

- Component tests, Playwright flows, keyboard-only walkthrough, screen-reader
  labels, reconnect, long messages, large histories, Unicode, and narrow windows.

## Likely code surfaces

- `surfaces/gui/src/App.tsx`
- `surfaces/gui/src/components/Sidebar.tsx`
- `surfaces/gui/src/components/TeamChatView.tsx`
- `surfaces/gui/src/styles.css`
- `surfaces/gui/src/locales/*`
- New `features/team/*` components and hooks

## Evidence

`npm run build` passes TypeScript plus the production bundle. The Team
surface is wired to canonical Team REST APIs and the focused backend Team/server
suite passes. The current GUI has Chat/Tasks/Activity/Agents/Files views, optimistic send, draft persistence,
Team event reconnect wiring, mention autocomplete, presence, notification tray/deep
links, Team-only sidebar routing, a durable thread panel with reply-in-thread messages,
`View in group` navigation, durable conversation pins, durable reaction and
admin-only message-pin toggles, and revision-aware quote/edit/delete/copy-link
message actions. The Activity view consumes the cursor-backed high-signal Team
projection and can open the originating conversation/message. Message
attachments use validated content-addressed blobs with ACL-checked download,
translation uses the tool-less provider path with durable per-language caching,
and message-to-task actions create or dispatch through the canonical task API.

Latest verification: full GUI suite `24 passed` files / `156 passed` tests; backend Team
suite `323 passed` in 24.12s; production build and Python compilation pass.
