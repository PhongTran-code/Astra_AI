# Decision: OAuth-preserving cloud login panel

Status: accepted
Start time: 2026-09-01T19:18:14+07:00

## Initial purpose

Adapt the Astra Connect right-hand login panel to the supplied form-style reference while preserving Astra's existing browser-based OAuth flow and security boundary.
The target is presentation only: no Email/Password credentials, fake provider buttons, or new authentication endpoint.

## Strategy

1. Trace the current login component, API boundary, backend sign-in route, existing tests, and responsive CSS.
2. Compare restyling the current component, introducing `styled-components`, and adding visual-only credential fields.
3. Keep the existing split surface and arcade artwork as the product identity; apply the reference's form rhythm to the right panel.
4. Use the repository's existing CSS variables and CSS file instead of adding a styling dependency.
5. Preserve translated copy, focus behavior, error/retry state, success state, skip action, and browser OAuth polling.
6. Verify with the existing login component tests, GUI typecheck/build where the baseline permits, and a browser screenshot at desktop and mobile widths.

## Checklist

- Read `surfaces/gui/src/components/CloudLoginScreen.tsx` to identify the right-panel markup and state transitions.
- Read `surfaces/gui/src/api.ts` to confirm `cloudLogin()` starts the browser flow and `waitForCloudSignIn()` completes it.
- Read `coworker/server/app.py` and `coworker/cloud.py` to confirm the backend owns Auth0 Authorization Code + PKCE and local SecretStore handling.
- Read `surfaces/gui/src/components/CloudLoginScreen.test.tsx` to preserve OAuth, retry, and success behavior.
- Read the login section of `surfaces/gui/src/styles.css` to preserve split layout and mobile behavior.
- Record the user decision to keep OAuth and change presentation only.
- Present and receive approval for the card-based form-style design.

## Result

The canonical owner is the existing `CloudLoginScreen` component; its right panel already contains the Astra brand, eyebrow, title, body, browser sign-in button, live status, divider, and signed-out continuation action.
The accepted change restyles that existing structure as a focused form-like card using `var(--panel)`, existing borders, existing typography, and the current accent token, without adding credential inputs or provider buttons.
The left arcade artwork, split layout, OAuth callback polling, security copy, translated labels, success state, error state, and mobile artwork-hiding rule remain unchanged.
The implementation scope is `CloudLoginScreen.tsx` only if class hooks need adjustment and the login block in `styles.css`; no API, backend, dependency, or auth state changes are required.

## Verification

The component/API/backend/test facts above were confirmed by direct source inspection.
The current login tests assert browser OAuth initiation, callback completion, and retry behavior; those contracts must remain green after the style change.
The final visual check is not yet performed at the time of this record and remains an implementation acceptance step.

## Decision

### Action

Apply the approved presentation-only design to the current `CloudLoginScreen` surface and its login CSS.
Run the affected login test, the narrowest available GUI typecheck/build, and browser visual checks without changing authentication behavior.

### Alternatives rejected

#### Port the supplied `styled-components` component directly

Rejected because the GUI currently uses React with its existing CSS layers and has no `styled-components` dependency; introducing a second styling system would add bundle and maintenance cost for one panel.

#### Add Email/Password, Google, and Apple controls as visual placeholders

Rejected because controls that cannot authenticate would misrepresent the security contract and create a dead-end user flow.

#### Replace Astra's browser OAuth flow

Rejected because the existing backend owns Auth0 Authorization Code + PKCE, the sidecar opens the system browser, and tokens remain in the local SecretStore; changing that boundary is a separate authentication project.

### Cross-references

- `surfaces/gui/src/components/CloudLoginScreen.tsx` — right-panel component and OAuth state machine.
- `surfaces/gui/src/styles.css` — split login surface and responsive presentation rules.
- `surfaces/gui/src/components/CloudLoginScreen.test.tsx` — behavioral OAuth contract.
- `docs/research/ai-first-engineering-protocols.md` — repository execution and verification protocol.
