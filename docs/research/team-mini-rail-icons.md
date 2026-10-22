# Decision: Team mini rail icon language

Status: accepted
Start time: 2026-09-01T15:54:08+07:00

## Initial purpose

Replace the generic-looking icons in the Team mini sidebar without changing navigation behavior, layout ownership, or the separate agent avatar language.
The supplied screenshot identifies the mini rail as the target surface; the change is intentionally narrower than a general Team visual refresh.

## Strategy

1. Trace the mini rail markup, shared SVG renderer, labels, and rail CSS before choosing an implementation.
2. Compare reusing the existing renderer, adding a new icon dependency, and changing CSS without changing glyphs.
3. Keep the supplied screenshot as the visual baseline; a second mockup is unnecessary for this focused icon-language change.
4. Use the existing inline SVG renderer and add only the semantic outline glyphs the Team rail needs.
5. Preserve the 72px rail, 40px controls, 18px icons, tooltips, keyboard semantics, active indicator, and view state transitions.

## Checklist

- Read `surfaces/gui/src/components/TeamSurface.tsx` to locate the mini rail and its five view mappings.
- Read `surfaces/gui/src/components/Icon.tsx` to confirm the shared 24px SVG grid and single 1.7px stroke contract.
- Read `surfaces/gui/src/styles.css` to confirm rail width, control size, spacing, and active-state ownership.
- Read `surfaces/gui/package.json` to confirm that no general-purpose UI icon dependency is installed.
- Record the user's chosen direction: line icons, not filled or custom image assets.
- Define a no-dependency implementation that changes only Team rail glyph selection and shared icon cases.

## Result

The mini rail is owned by `TeamSurface.tsx:1629-1653` and currently maps Chat, Tasks, Activity, Agents, and Files to the generic `chat`, `check`, `clock`, `users`, and `file` glyphs.
Those glyphs come from the shared `Icon` renderer, which already guarantees a consistent current-color outline style, so a second icon library would add weight and a competing visual language without solving the semantic mismatch.
The accepted design uses five role-clear outline glyphs: message bubble, checklist, activity pulse, AI agent, and stacked files.
The implementation changes only the rail's glyph mapping and the corresponding cases in `Icon.tsx`; it does not alter agent avatars, conversation rows, Team state, routing, or backend behavior.

## Verification

The component, renderer, CSS, and dependency facts above were confirmed by direct source inspection.
The screenshot is the baseline for the visual acceptance check; runtime visual verification remains required after implementation.
No external icon package is required by the accepted approach.

## Decision

### Action

Implement the accepted design in `surfaces/gui/src/components/TeamSurface.tsx` and `surfaces/gui/src/components/Icon.tsx`.
Verify the final mini rail in the running browser at the active Team surface and run the affected GUI checks.

### Alternatives rejected

#### Add a general-purpose icon dependency

Rejected because the repository already owns a shared inline SVG renderer and `package.json` has no UI icon library to extend; adding one would increase bundle and maintenance surface for five controls.

#### Change only spacing, colors, or the active tile

Rejected because the inspected issue is primarily glyph semantics: a clock communicates time rather than activity, a lone check communicates completion rather than a task list, and a generic file shape is weak for a file workspace.

#### Use emoji, raster assets, or bespoke image icons

Rejected because they would break the existing current-color outline language and reduce crispness across light and dark themes.

### Cross-references

- `plan/task-team-mini-rail-icons.md` — implementation sequence and verification contract.
- `surfaces/gui/src/components/TeamSurface.tsx` — Team mini rail owner.
- `surfaces/gui/src/components/Icon.tsx` — shared SVG icon owner.
