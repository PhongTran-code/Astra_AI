---
format: 1920x1080
duration: 45s
message: "Astra carries a real task from ask to finished result, with human approval at the boundary."
arc: Ask → Plan → Act → Pause → Approve → Finish → CTA
audience: engineers and security-minded teams evaluating a local AI coworker
mode: autonomous
---

# STORYBOARD — Astra script-led landing cut

This is the locked visual script for the silent landing-page video. The
on-screen words are paced as narration, so the story remains understandable
without audio and can be localized with the page's language switch.

## Frame 01 — The ask

- status: animated
- duration: 5.2s
- transition_in: cut
- scene: A specific task replaces the vague promise of an AI chat.
- blueprint: kinetic-type-beats
- asset_candidates: typography + task card; no external media

**On-screen script:** “A reply is not the finish line. Give Astra a real outcome: fix the bug, run the test, leave the trail.”

The task card makes the request concrete: find the insecure session-token
fallback, patch it, and run the test.

## Frame 02 — The plan

- status: animated
- duration: 6s
- transition_in: cut
- scene: Astra turns the ask into inspectable steps before action.
- blueprint: prompt-type-submit-generate
- asset_candidates: `assets/astra-app-real.png`

**On-screen script:** “First, Astra makes a plan. One clear ask becomes steps you can inspect before anything runs.”

The real workstation surface is evidence that the product starts from an
outcome, not an empty generic chat card.

## Frame 03 — The work

- status: animated
- duration: 6s
- transition_in: cut
- scene: The plan becomes real work across the local desktop loop.
- blueprint: agent-progress-theater
- asset_candidates: `assets/astra-showcase-ui-source.png`

**On-screen script:** “Then it does the work. It reads the repo, runs the tools, and checks the result in one local session.”

The three-step list is the beat order: read, run, check.

## Frame 04 — The pause

- status: animated
- duration: 6.6s
- transition_in: cut
- scene: A consequential action reaches the human approval boundary.
- blueprint: titlecard-reveal
- asset_candidates: `assets/astra-showcase-poster-boundary.png`

**On-screen script:** “When it matters, Astra stops. A consequential action cannot skip the human approval gate.”

The approval screenshot and amber annotation are the proof. The decision is
visible rather than hidden inside a prompt.

## Frame 05 — The approval

- status: animated
- duration: 7.5s
- transition_in: cut
- scene: The approval is a step in the same durable loop, not a detour.
- blueprint: spatial-pan-stations
- asset_candidates: pipeline graphic + `assets/astra-showcase-poster-boundary.png`

**On-screen script:** “You approve once. The work resumes from the same request — with the decision visible and recorded.”

The pipeline spells out the causal order: ask, plan, act, approve, finish.

## Frame 06 — The finish

- status: animated
- duration: 7.5s
- transition_in: cut
- scene: Astra hands back a usable result and its evidence.
- blueprint: transcript-scroll-artifact-reveal
- asset_candidates: `assets/astra-app-real.png`

**On-screen script:** “You get the result: a patch, a check, and a trace — not a promise to finish later.”

The checks land one at a time: patch ready, tests run, audit saved.

## Frame 07 — The point

- status: animated
- duration: 6.2s
- transition_in: cut
- scene: The thesis resolves into the product name and call to action.
- blueprint: cta-close
- asset_candidates: `assets/astra-mark.svg`

**On-screen script:** “Your AI coworker that finishes. Runs locally. Asks before it acts. Leaves a trail.”

Close on the Astra mark and the existing landing-page CTA: download free,
open source.

## Video direction

Use one editorial sentence per scene, with the bottom rail showing the current
step. Every UI screenshot must answer the sentence on screen. Keep transitions
as clean cuts between paper and dark approval beats; use motion only to reveal
the next clause or evidence. No generic chat bubbles, stock footage, or
unsupported product claims.
