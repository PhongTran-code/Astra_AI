# OAuth-Preserving Cloud Login Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle Astra's existing right-hand Cloud Login panel with the supplied form-card visual language without changing authentication behavior.

**Architecture:** Keep `CloudLoginScreen` as the owner of the existing OAuth state machine and keep `cloudLogin()` as the only sign-in action. Apply the form reference through the existing login CSS block: a token-backed card, compact form rhythm, full-width primary action, status copy, divider, and signed-out continuation link; do not add credential fields or provider buttons.

**Tech Stack:** React 18, TypeScript, existing CSS variables, inline SVG `Icon`, Vite.

## Global Constraints

- Preserve browser OAuth through `cloudLogin()` and `waitForCloudSignIn()` exactly as implemented.
- Do not add Email/Password, Google, Apple, fake credential fields, new API routes, or authentication dependencies.
- Preserve translated copy, `aria-live` status, keyboard focus, retry/error state, success state, skip action, and the existing arcade artwork.
- Use existing semantic tokens (`--paper`, `--panel`, `--ink`, `--muted`, `--faint`, `--line`, `--accent`) instead of new raw palette values.
- Keep the desktop split layout and mobile rule that hides the decorative artwork.
- Do not modify unrelated working-tree changes, including the existing duplicate `initTheme` definitions in `surfaces/gui/src/theme.ts`.

---

### Task 1: Apply the form-card presentation

**Files:**
- Modify: `surfaces/gui/src/styles.css:520-960` in the existing Astra Connect sign-in block.
- Test: `surfaces/gui/src/components/CloudLoginScreen.test.tsx` remains the behavioral contract and should not need markup changes.

**Interfaces:**
- Consumes: the existing `CloudLoginScreen` class hooks and CSS custom properties.
- Produces: a right panel that visually follows the supplied form reference while retaining all current DOM semantics and actions.

- [ ] **Step 1: Re-style the outer right panel**

Keep the existing grid and breakpoint behavior, but make the right side a subtle token-backed surface so the card reads as the focused form:

```css
.cloud-login-panel {
  display: grid;
  min-width: 0;
  place-items: center;
  padding: clamp(28px, 5vw, 72px);
  background: var(--paper);
}
```

Do not change `.cloud-login-overlay` columns or `.cloud-login-art`.

- [ ] **Step 2: Turn the existing card into the form container**

Replace only the existing `.cloud-login-card` declarations with a bordered, rounded form card using the existing panel token:

```css
.cloud-login-card {
  position: relative;
  width: min(100%, 450px);
  padding: 30px;
  border: 1px solid var(--line);
  border-radius: 20px;
  background: var(--panel);
}
```

Do not add a shadow or a second wrapper; the card remains the existing dialog element.

- [ ] **Step 3: Match the reference's vertical rhythm**

Adjust only the existing login child selectors so the current brand, eyebrow, title, body, action, status, divider, and skip link read as one form:

```css
.cloud-login-brand { margin-bottom: 38px; }
.cloud-login-copy { max-width: none; }
.cloud-login-primary {
  min-height: 50px;
  margin-top: 26px;
  border-radius: 10px;
}
.cloud-login-status {
  min-height: 40px;
  margin: 12px 0 0;
}
.cloud-login-divider { margin: 24px 0 14px; }
.cloud-login-skip { min-height: 40px; }
```

Preserve the existing typography, accent colors, hover/focus states, disabled spinner, and translated text.

- [ ] **Step 4: Preserve compact mobile behavior**

Extend the existing mobile rule with card padding while retaining artwork hiding and full-height panel behavior:

```css
@media (max-width: 760px) {
  .cloud-login-overlay { display: block; overflow-y: auto; }
  .cloud-login-art { display: none; }
  .cloud-login-panel { min-height: 100%; padding: 24px 16px; }
  .cloud-login-card { padding: 24px; }
  .cloud-login-brand { margin-bottom: 34px; }
}
```

Keep the existing reduced-motion rule unchanged.

### Task 2: Verify the preserved login behavior

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: the unchanged `CloudLoginScreen` state machine and its existing test mocks.
- Produces: evidence that styling did not change OAuth initiation, callback completion, retry, success, or signed-out continuation behavior.

- [ ] **Step 1: Run the focused login tests**

Run from `surfaces/gui/`:

```bash
npm test -- CloudLoginScreen.test.tsx
```

Expected: both existing tests pass, including browser-flow polling and retry after `cloudLogin()` failure.

- [ ] **Step 2: Run the GUI typecheck/build**

Run from `surfaces/gui/`:

```bash
npm run build
```

Expected: `tsc` and Vite exit 0. If the known pre-existing duplicate `initTheme` export in `src/theme.ts` still blocks the build, record that exact baseline failure and do not edit that unrelated file.

### Task 3: Verify the visual contract in a real browser

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: the running GUI at `http://127.0.0.1:5173/` or the source-configured Vite port `http://localhost:1420/`.
- Produces: desktop and mobile screenshots plus accessibility/runtime observations for the login panel.

- [ ] **Step 1: Open the login surface**

Use the local GUI process and open the existing Cloud Login screen without navigating to external URLs or entering credentials.

- [ ] **Step 2: Check desktop presentation**

Confirm the left arcade artwork remains intact and the right side shows a centered bordered card with the Astra brand, `Welcome back`, body copy, full-width `Continue in browser`, security status, divider, and `Continue without signing in`.

- [ ] **Step 3: Check behavior and accessibility**

Confirm the primary button remains keyboard focusable, the dialog retains its accessible title, the status remains `aria-live`, and the skip action remains reachable without exposing credential fields.

- [ ] **Step 4: Check mobile presentation**

At a viewport no wider than 760px, confirm artwork hides, the card remains readable with 24px padding, and the panel can scroll if content exceeds the viewport.

- [ ] **Step 5: Capture final evidence and whitespace check**

Capture a desktop and mobile screenshot, then run from the repository root:

```bash
git diff --check
```

Expected: the screenshots show the approved form-card treatment and `git diff --check` reports no whitespace errors.
