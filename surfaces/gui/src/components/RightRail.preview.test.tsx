// The rail's preview notification must be edge-triggered: a new onPreviewChange
// identity (App re-renders whenever the nav toggles) must NOT replay "open" while
// the viewer sits open — that re-collapsed a sidebar the user had just expanded
// (owner-hit 2026-08-21).
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getArtifacts, readArtifact } from "../api";
import { RightRail } from "./RightRail";
import { OPEN_ARTIFACT_EVENT } from "./Markdown";

afterEach(cleanup);

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getArtifacts: vi.fn().mockResolvedValue([]),
    getRoots: vi.fn().mockResolvedValue([]),
    getJournalCases: vi.fn().mockResolvedValue([]),
    readArtifact: vi.fn().mockResolvedValue({ ok: true, path: "r.md", kind: "markdown", content: "x" }),
    revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
  };
});

function rail(onPreviewChange: (open: boolean) => void) {
  return (
    <RightRail
      active
      sessionId="s1"
      refreshKey={0}
      toolNames={[]}
      todo={[]}
      running={false}
      onPreviewChange={onPreviewChange}
    />
  );
}

describe("RightRail preview notification", () => {
  it("fires only on open/close transitions, not on callback identity changes", async () => {
    const first = vi.fn();
    const { rerender } = render(rail(first));
    await act(async () => {});
    expect(first).not.toHaveBeenCalled(); // closed at mount: no "closed" replay either

    // Open the viewer via a transcript chip event.
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "r.md" } }),
      );
    });
    expect(first).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenLastCalledWith(true);

    // App re-renders with a NEW callback identity (e.g. the user expanded the nav).
    const second = vi.fn();
    rerender(rail(second));
    await act(async () => {});
    // The viewer never transitioned, so the new callback must not be told "open".
    expect(second).not.toHaveBeenCalled();
  });

  it("renders video artifacts with metadata-only native playback", async () => {
    vi.mocked(getArtifacts).mockResolvedValueOnce([
      { path: "clip.mp4", name: "clip.mp4", kind: "video", size: 10, modified_at: 1 },
    ]);
    vi.mocked(readArtifact).mockResolvedValueOnce({
      ok: true,
      path: "clip.mp4",
      kind: "video",
    });

    render(rail(vi.fn()));
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "clip.mp4" } }),
      );
    });

    const video = await screen.findByTestId("artifact-video");
    expect(video.getAttribute("controls")).not.toBeNull();
    expect(video.getAttribute("preload")).toBe("metadata");
    expect(video.getAttribute("src")).toContain("artifacts/video");
  });

  it("normalizes legacy session-prefixed artifact links before reading", async () => {
    vi.mocked(getArtifacts).mockResolvedValueOnce([
      {
        path: "messi-career-dashboard.html",
        name: "messi-career-dashboard.html",
        kind: "html",
        size: 10,
        modified_at: 1,
      },
    ]);
    vi.mocked(readArtifact).mockResolvedValueOnce({
      ok: true,
      path: "messi-career-dashboard.html",
      kind: "html",
      content: "<h1>Messi</h1>",
    });

    render(
      <RightRail
        active
        sessionId="ba0450e9-c4c"
        refreshKey={0}
        toolNames={[]}
        todo={[]}
        running={false}
      />,
    );
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, {
          detail: { path: "ba0450e9-c4c/messi-career-dashboard.html" },
        }),
      );
    });

    expect(await screen.findByTestId("artifact-frame")).toBeTruthy();
    expect(readArtifact).toHaveBeenLastCalledWith(
      "ba0450e9-c4c",
      "messi-career-dashboard.html",
    );
  });
});
