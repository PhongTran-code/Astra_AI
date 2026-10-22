# Team Mini Rail Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic Team mini-sidebar glyphs with a coherent semantic outline icon set while preserving the existing rail behavior and layout.

**Architecture:** Keep `Icon.tsx` as the single inline SVG renderer and add five reusable glyph cases to its existing 24px/current-color/1.7px-stroke contract. Change only the view-to-icon mapping owned by `TeamSurface.tsx`; do not alter Team state, navigation callbacks, agent avatars, or backend contracts.

**Tech Stack:** React 18, TypeScript, Vite, Tailwind utility classes, inline SVG.

## Global Constraints

- Keep the Team mini rail at `72px`, keep each control at `40px`, and keep the icon size at `18px`.
- Preserve each button's translated `aria-label`, `title`, keyboard behavior, active tile, active indicator, and `setActiveView` transition.
- Use one outline language: the existing `Icon` renderer's `viewBox="0 0 24 24"`, `currentColor`, `strokeWidth: 1.7`, round caps, and round joins.
- Do not add an icon dependency, change agent avatars, or change Team API/runtime behavior.
- Do not modify unrelated working-tree changes.

---

### Task 1: Add semantic Team rail glyphs

**Files:**
- Modify: `surfaces/gui/src/components/Icon.tsx:4-53` for the `IconName` union and the switch cases near the existing communication/status/people/file icons.
- Test: `surfaces/gui/src/components/TeamSurface.test.tsx` through the existing Team render suite; no new snapshot or DOM contract is required for path geometry.

**Interfaces:**
- Consumes: the existing `Icon` props and shared SVG style object `s`.
- Produces: five new `IconName` values usable by `TeamSurface`: `messageCircle`, `listChecks`, `activity`, `bot`, and `files`.

- [x] **Step 1: Extend the icon name contract**

Add these five literals to the existing `IconName` union without renaming or removing any existing literal:

```ts
  | "messageCircle"
  | "listChecks"
  | "activity"
  | "bot"
  | "files"
```

- [x] **Step 2: Add the message-circle glyph**

Add a switch case that uses the shared `s` object and draws a rounded message circle with a tail:

```tsx
    case "messageCircle":
      return (
        <svg {...s}>
          <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 8.5 8.5 0 0 1-4.1-1L3 21l2-5.4A8.5 8.5 0 1 1 21 11.5z" />
        </svg>
      );
```

- [x] **Step 3: Add the checklist glyph**

Add a switch case with three check marks and three aligned rows:

```tsx
    case "listChecks":
      return (
        <svg {...s}>
          <path d="m4 6 1.5 1.5L8 5M11 6h9M4 12l1.5 1.5L8 11M11 12h9M4 18l1.5 1.5L8 17M11 18h9" />
        </svg>
      );
```

- [x] **Step 4: Add the activity-pulse glyph**

Add a switch case that communicates live activity rather than time:

```tsx
    case "activity":
      return (
        <svg {...s}>
          <path d="M3 12h4l2.2-6 4.1 12 2.2-6H21" />
        </svg>
      );
```

- [x] **Step 5: Add the bot glyph**

Add a switch case that identifies AI agents without using a filled avatar or emoji:

```tsx
    case "bot":
      return (
        <svg {...s}>
          <rect x="4" y="7" width="16" height="13" rx="3" />
          <path d="M12 3v4M8.5 12h.01M15.5 12h.01M8.5 16h7" />
        </svg>
      );
```

- [x] **Step 6: Add the stacked-files glyph**

Add a switch case that communicates a file workspace rather than one generic document:

```tsx
    case "files":
      return (
        <svg {...s}>
          <path d="M8 3.5h7l4 4v13H8a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2z" />
          <path d="M15 3.5v4h4M4 7.5v11a2 2 0 0 0 2 2h9M10 12h6M10 15.5h4" />
        </svg>
      );
```

- [x] **Step 7: Run the affected component suite**

Run from `surfaces/gui/`:

```bash
npm test -- TeamSurface.test.tsx
```

Expected: Vitest completes successfully; existing Team behaviors remain covered, and no TypeScript/runtime icon-name error appears.

### Task 2: Wire semantic glyphs into the Team mini rail

**Files:**
- Modify: `surfaces/gui/src/components/TeamSurface.tsx:1635-1650`.

**Interfaces:**
- Consumes: the five new `IconName` values from `Icon.tsx`.
- Produces: the existing five Team view buttons render `messageCircle`, `listChecks`, `activity`, `bot`, and `files` respectively.

- [x] **Step 1: Replace only the rail mapping**

Change the existing tuple list to this exact mapping and leave the button markup unchanged:

```tsx
            {([
              ["chat", "messageCircle"],
              ["tasks", "listChecks"],
              ["activity", "activity"],
              ["agents", "bot"],
              ["files", "files"],
            ] as const).map(([view, icon]) => (
```

The existing button must retain its `key`, classes, translated `aria-label`, translated `title`, `onClick={() => setActiveView(view)}`, and `<Icon name={icon} size={18} />` call.

- [ ] **Step 2: Run the GUI typecheck/build**

Run from `surfaces/gui/`:

```bash
npm run build
```

Expected: `tsc` and the Vite production build both exit 0, with no unknown `IconName` or JSX errors.

### Task 3: Verify the visual contract in the running Team surface

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: the running GUI and the Team mini rail rendered by `TeamSurface`.
- Produces: visual evidence that the five glyphs are coherent, legible, and behaviorally unchanged.

- [ ] **Step 1: Open the active GUI surface**

Use the existing local GUI process at `http://127.0.0.1:5173/` for this worktree; the source-configured default remains `localhost:1420`, but this process was intentionally started with the explicit `5173` override.

- [ ] **Step 2: Navigate to Team and inspect the mini rail**

Confirm all five controls are visible when the Team surface is active: Chat, Tasks, Activity, Agents, and Files.
Confirm the glyphs use the same outline weight and current-color behavior, the active tile and left accent indicator remain present, and the rail width/spacing do not shift.

- [ ] **Step 3: Exercise each navigation control**

Click each control once and confirm the corresponding view changes while the translated tooltip/title and accessible label remain present.

- [ ] **Step 4: Capture final browser evidence**

Take a screenshot of the Team mini rail in its final state and inspect both the selected and unselected controls at the running viewport.

- [x] **Step 5: Run whitespace validation**

Run from the repository root:

```bash
git diff --check
```

Expected: no whitespace errors in task-owned changes.

## Execution status

- [x] Added the five semantic outline glyphs to `Icon.tsx` and mapped them in `TeamSurface.tsx`.
- [x] `npm test -- TeamSurface.test.tsx` passed: 1 file and 19 tests.
- [x] `git diff --check` passed.
- [ ] `npm run build` remains blocked by the pre-existing duplicate `initTheme` export in `surfaces/gui/src/theme.ts:130` and `:138`.
- [ ] Browser visual verification remains blocked because the running Vite page returns HTTP 500 for that same `theme.ts` transform error, leaving the Team surface blank.
