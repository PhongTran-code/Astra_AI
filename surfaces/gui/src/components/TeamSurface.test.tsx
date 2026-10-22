import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { TeamSurface } from "./TeamSurface";
import type { TeamMessage } from "../api";

type Call = { url: string; method: string; body?: Record<string, unknown> };

const CONVERSATION = {
  conversation_id: "conversation-1",
  workspace_id: "workspace-1",
  conversation_kind: "direct",
  title: "",
  status: "active",
  reply_mode: "coordinator",
  coordinator_agent_id: "agent-1",
  allow_at_all: false,
  max_fanout: 8,
  members: [{ actor_id: "human-1", member_role: "owner", left_at: null, display_name: "You" }],
  agent_member_ids: ["agent-1"],
};

const MESSAGE = {
  message_id: "message-1",
  conversation_id: "conversation-1",
  author_actor_id: "human-1",
  author_kind: "human",
  body: "Review this release",
  status: "active",
  created_at: "2026-08-30T10:00:00Z",
  edited_at: null,
  revision_number: 1,
  mentions: [],
  reactions: [],
  attachments: [],
  translations: [],
};

const AGENT = {
  agent_id: "agent-1",
  actor_id: "agent-actor-1",
  display_name: "Builder",
  handle: "builder",
  description: "Builds safe changes.",
  base_role: "builder",
  status: "ready",
  avatar_id: null as string | null,
};
const SECOND_AGENT = {
  agent_id: "agent-2",
  actor_id: "agent-actor-2",
  display_name: "Reviewer",
  handle: "reviewer",
  description: "Reviews safe changes.",
  base_role: "reviewer",
  status: "ready",
  avatar_id: null as string | null,
};


const AGENT_PACKAGE = {
  format: "astra-agent-package",
  version: 1,
  definition: { display_name: "Builder", handle: "builder", description: "Builds safe changes.", base_role: "builder", avatar_id: null as string | null },
  revision: {
    instructions: "Build safely.",
    model_policy: { model: "test-model" },
    sandbox_policy: { network: false },
    memory_policy: {},
    reply_policy: {},
    budget_policy: {},
    approval_policy: { required: false },
    capabilities: [{ capability_kind: "connector", capability_ref: "github", required: false, config: {} }],
  },
  installation: { max_concurrency: 1 },
  sandbox: { isolation_level: "workspace", mounts: [] },
};

const COMPUTER = {
  computer_id: "computer-1",
  workspace_id: "workspace-1",
  agent_id: "agent-1",
  provider_kind: "playwright_browser",
  status: "ready",
  capabilities: { browser: true },
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:03:00Z",
};

const TASK = {
  task_id: "task-1",
  workspace_id: "workspace-1",
  title: "Ship release",
  description: "Prepare the release safely.",
  acceptance_criteria: "The release is verified.",
  status: "open",
  owner_agent_id: "agent-1",
  reviewer_actor_id: null,
  priority: 1,
  dependencies: [],
  watchers: [],
  labels: [],
  updated_at: "2026-08-30T10:02:00Z",
};

class FakeWebSocket {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(_url: string, _protocols?: string | string[]) {}
  close() {}
}

function stubTeamFetch(group = false, review = false, withRun = false, withDuplicateDirects = false, withSecondAgent = false) {
  const calls: Call[] = [];
  let message: TeamMessage = { ...MESSAGE };
  let pinned = false;
  let conversationPinned = false;
  let agentStatus = AGENT.status;
  let agentAvatarId: string | null = AGENT.avatar_id;
  let agentPackage = structuredClone(AGENT_PACKAGE);
  const visibleTask = { ...TASK, status: review ? "review" : TASK.status };
  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined;
    calls.push({ url, method, body });

    if (method === "GET" && url.endsWith("/v1/team")) {
      const conversations = group
        ? [{ ...CONVERSATION, conversation_kind: "group", title: "Research", pinned: conversationPinned }]
        : [
            { ...CONVERSATION, updated_at: "2026-08-30T10:02:00Z", pinned: conversationPinned },
            ...(withDuplicateDirects
              ? [{ ...CONVERSATION, conversation_id: "conversation-old", updated_at: "2026-08-29T10:02:00Z", pinned: false }]
              : []),
          ];
      return { ok: true, json: async () => ({ workspace_id: "workspace-1", actor_id: "human-1", agents: [{ ...AGENT, avatar_id: agentAvatarId, status: agentStatus }, ...(withSecondAgent ? [{ ...SECOND_AGENT }] : [])], conversations }) } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/presence")) {
      return { ok: true, json: async () => ({ agents: [] }) } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/notifications")) {
      return { ok: true, json: async () => ({ notifications: [], has_more: false, next_cursor: null }) } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/activity")) {
      return { ok: true, json: async () => ({ activities: [], has_more: false, next_sequence: 0 }) } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/tasks/task-1")) {
      return { ok: true, json: async () => visibleTask } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/tasks/task-1/thread")) {
      return { ok: true, json: async () => ({
        task: visibleTask,
        thread: { thread_id: "thread-task-1", conversation_id: "conversation-1", root_message_id: "message-1", created_at: "2026-08-30T10:00:00Z" },
        messages: [{ ...MESSAGE, thread_id: "thread-task-1" }],
        has_more: false,
      }) } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/tasks")) {
      return { ok: true, json: async () => ({ tasks: [visibleTask], has_more: false, next_cursor: null }) } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/agents/agent-1/export")) {
      return { ok: true, json: async () => agentPackage } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/computers")) {
      return { ok: true, json: async () => ({ computers: [COMPUTER] }) } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/agents/agent-1/sandbox/health")) {
      return { ok: true, json: async () => ({ agent_id: "agent-1", provider_kind: "test", home_exists: true }) } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/runs/run-1")) {
      return { ok: true, json: async () => ({ run: { run_id: "run-1", status: "succeeded", budget: { tokens: 1000, cost_micros: 500000 } }, usage: { tokens: 12, cost_micros: 250000, tool_calls: 1 }, events: [], delivery: null }) } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/runs")) {
      return { ok: true, json: async () => ({ runs: withRun ? [{ run_id: "run-1", task_id: "task-1", status: "running", budget: { tokens: 1000, cost_micros: 500000 }, usage: { tokens: 12, cost_micros: 250000, tool_calls: 1 } }] : [], has_more: false }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/agents/agent-1/playground")) {
      return { ok: true, json: async () => ({ exit_code: 0, stdout: "hello-from-sandbox" }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/agents")) {
      return { ok: true, json: async () => ({ ...AGENT, display_name: body?.display_name || "Custom agent", handle: body?.handle || "custom-agent", base_role: body?.base_role || "builder" }) } as Response;
    }
    if (method === "PATCH" && url.endsWith("/v1/team/agents/agent-1")) {
      if (typeof body?.instructions === "string") agentPackage = { ...agentPackage, revision: { ...agentPackage.revision, instructions: body.instructions } };
      if (typeof body?.avatar_id === "string") {
        agentAvatarId = body.avatar_id;
        agentPackage = { ...agentPackage, definition: { ...agentPackage.definition, avatar_id: agentAvatarId } };
      }
      return { ok: true, json: async () => ({ agent_id: "agent-1" }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/agents/agent-1/pause")) {
      agentStatus = "paused";
      return { ok: true, json: async () => ({ agent_id: "agent-1", status: agentStatus }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/agents/agent-1/resume")) {
      agentStatus = "ready";
      return { ok: true, json: async () => ({ agent_id: "agent-1", status: agentStatus }) } as Response;
    }
    if (method === "POST" && url.includes("/v1/team/agents/agent-1/")) {
      return { ok: true, json: async () => ({ agent_id: "agent-1", status: agentStatus }) } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/conversations/conversation-1")) {
      return { ok: true, json: async () => ({ ...CONVERSATION, conversation_kind: group ? "group" : "direct", title: group ? "Research" : "", agent_member_ids: group ? ["agent-1"] : [], pinned: conversationPinned }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/conversations")) {
      return { ok: true, json: async () => ({
        ...CONVERSATION,
        conversation_id: "conversation-new",
        conversation_kind: "group",
        title: String(body?.title || "AI Research"),
        reply_mode: "mention_only",
        agent_member_ids: Array.isArray(body?.agent_ids) ? body.agent_ids : [],
      }) } as Response;
    }
    if (method === "GET" && url.includes("/v1/team/conversations/conversation-1/messages")) {
      return { ok: true, json: async () => ({ messages: [message], has_more: false, next_cursor: null }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/conversations/conversation-1/messages")) {
      return { ok: true, json: async () => ({ message, request: null, deliveries: [], routing: null }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/messages/message-1/attachments")) {
      message = {
        ...message,
        attachments: [{
          attachment_id: "attachment-1",
          message_id: "message-1",
          storage_ref: "attachment://hash.png#release.png",
          filename: "release.png",
          media_type: "image/png",
          size_bytes: 8,
          sha256: "a".repeat(64),
          created_at: "2026-08-30T10:02:00Z",
        }],
      };
      return { ok: true, json: async () => message.attachments[0] } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/messages/message-1/translate")) {
      const translated = {
        language: "vi",
        body: "Xem bản phát hành",
        model: "test-model",
        created_at: "2026-08-30T10:03:00Z",
      };
      message = { ...message, translations: [translated] };
      return { ok: true, json: async () => translated } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/tasks")) {
      return { ok: true, json: async () => ({
        task_id: "task-1",
        workspace_id: "workspace-1",
        title: "Review this release",
        description: "Review this release",
        acceptance_criteria: "Deliver a verified result for this request.",
        status: "open",
        owner_agent_id: null,
        reviewer_actor_id: null,
        priority: 0,
        dependencies: [],
        watchers: [],
        labels: [],
        updated_at: "2026-08-30T10:02:00Z",
      }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/tasks/task-1/dispatch")) {
      return { ok: true, json: async () => ({ request: {}, deliveries: [] }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/tasks/task-1/assign")) {
      return { ok: true, json: async () => visibleTask } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/tasks/task-1/dependencies")) {
      return { ok: true, json: async () => ({ task_id: "task-1", depends_on_task_id: "task-1" }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/tasks/task-1/review")) {
      return { ok: true, json: async () => ({ ...visibleTask, status: body?.approved ? "done" : "in_progress" }) } as Response;
    }
    if (method === "PATCH" && url.endsWith("/v1/team/conversations/conversation-1")) {
      return { ok: true, json: async () => ({ ...CONVERSATION, conversation_kind: "group", title: String(body?.title || "Research"), reply_mode: String(body?.reply_mode || "mention_only"), coordinator_agent_id: body?.coordinator_agent_id || null, allow_at_all: Boolean(body?.allow_at_all), max_fanout: Number(body?.max_fanout || 8), pinned: conversationPinned }) } as Response;
    }
    if (method === "GET" && url.endsWith("/v1/team/conversations/conversation-1/pins")) {
      return { ok: true, json: async () => ({ pins: pinned ? [{ message_id: "message-1" }] : [] }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/conversations/conversation-1/read")) {
      return { ok: true, json: async () => ({ ok: true }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/messages/message-1/edit")) {
      message = { ...message, body: String(body?.body || ""), edited_at: "2026-08-30T10:01:00Z", revision_number: 2 };
      return { ok: true, json: async () => message } as Response;
    }
    if (method === "DELETE" && url.endsWith("/v1/team/messages/message-1")) {
      message = { ...message, body: "", status: "tombstoned" };
      return { ok: true, json: async () => message } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/conversations/conversation-1/messages/message-1/pin")) {
      pinned = true;
      return { ok: true, json: async () => ({ message_id: "message-1" }) } as Response;
    }
    if (method === "DELETE" && url.endsWith("/v1/team/conversations/conversation-1/messages/message-1/pin")) {
      pinned = false;
      return { ok: true, json: async () => ({ message_id: "message-1", removed: true }) } as Response;
    }
    if (method === "POST" && url.endsWith("/v1/team/conversations/conversation-1/pin")) {
      conversationPinned = true;
      return { ok: true, json: async () => ({ conversation_id: "conversation-1", pinned: true }) } as Response;
    }
    if (method === "DELETE" && url.endsWith("/v1/team/conversations/conversation-1/pin")) {
      conversationPinned = false;
      return { ok: true, json: async () => ({ conversation_id: "conversation-1", pinned: false }) } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchStub);
  vi.stubGlobal("WebSocket", FakeWebSocket);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("TeamSurface message actions", () => {
  it("quotes, edits with the current revision, and tombstones the author's message", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Quote" }));
    expect(screen.getByText("Replying to: Review this release")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel quote" }));
    expect(screen.queryByText("Replying to: Review this release")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Unpin" })).toBeTruthy());
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations/conversation-1/messages/message-1/pin"))).toBe(true);

    fireEvent.click(screen.getByRole("tab", { name: "Direct" }));
    fireEvent.click(screen.getByRole("button", { name: "Pin conversation" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Unpin conversation" })).toBeTruthy());
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations/conversation-1/pin"))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const editor = screen.getByDisplayValue("Review this release");
    fireEvent.change(editor, { target: { value: "Review this release now" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Review this release now")).toBeTruthy());
    const editCall = calls.find((call) => call.url.endsWith("/v1/team/messages/message-1/edit"));
    expect(editCall?.body).toMatchObject({ body: "Review this release now", expected_revision: 1 });

    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByText("Message deleted")).toBeTruthy());
    expect(calls.some((call) => call.method === "DELETE" && call.url.endsWith("/v1/team/messages/message-1"))).toBe(true);
  });
  it("opens the reaction picker and sends the selected emoji", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "React to message" }));

    const picker = screen.getByRole("menu", { name: "Reaction picker" });
    expect(within(picker).getByRole("menuitem", { name: "Cheer" })).toBeTruthy();
    fireEvent.click(within(picker).getByRole("menuitem", { name: "Cheer" }));

    await waitFor(() => {
      const reactionCall = calls.find(
        (call) => call.method === "POST" && call.url.endsWith("/v1/team/messages/message-1/reaction"),
      );
      expect(reactionCall?.body).toMatchObject({ emoji: "👏🏻", active: true });
    });
    expect(screen.queryByRole("menu", { name: "Reaction picker" })).toBeNull();
  });


  it("loads the durable activity view", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(await screen.findByText("No activity yet.")).toBeTruthy();
    expect(calls.some((call) => call.method === "GET" && call.url.includes("/v1/team/activity"))).toBe(true);
  });

  it("opens an agent profile, saves instructions, and runs a lifecycle action", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Builder" }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByRole("button", { name: "Builder" })[0]);
    expect(await screen.findByText("Agent profile")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Instructions" }));
    const editor = screen.getByLabelText("Instructions");
    fireEvent.change(editor, { target: { value: "Build safely and verify tests." } });
    fireEvent.click(screen.getByRole("button", { name: "Save instructions" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PATCH" && call.url.endsWith("/v1/team/agents/agent-1"))).toBe(true));
    fireEvent.click(screen.getByRole("tab", { name: "Sandbox" }));
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByText("hello-from-sandbox")).toBeTruthy());
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/agents/agent-1/playground"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/agents/agent-1/pause"))).toBe(true));
  });
  it("shows an operational agent workspace with identity and overview cards", async () => {
    stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Builder" }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByRole("button", { name: "Builder" })[0]);

    expect(await screen.findByRole("heading", { name: "Builder", level: 3 })).toBeTruthy();
    expect(screen.getByText("Builds safe changes.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send message" })).toBeTruthy();
    expect(screen.getByText("Operational snapshot")).toBeTruthy();
    expect(screen.getByText("Direct messages")).toBeTruthy();
    expect(screen.queryByText("team_surface.agent_overview_tab")).toBeNull();
  });

  it("projects the durable Computer state for an agent", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Builder" }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByRole("button", { name: "Builder" })[0]);
    expect(await screen.findByText("Agent profile")).toBeTruthy();
    const agentTabs = screen.getByRole("tablist", { name: "Agent profile" });
    fireEvent.click(within(agentTabs).getByRole("tab", { name: "Computer" }));

    expect(await screen.findByText("computer-1")).toBeTruthy();
    expect(screen.getByText(/playwright_browser/)).toBeTruthy();
    expect(calls.some((call) => call.method === "GET" && call.url.includes("/v1/team/computers"))).toBe(true);
  });

  it("selects and persists one of the bot avatars", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Builder" }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByRole("button", { name: "Builder" })[0]);
    expect(await screen.findByText("Agent profile")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Change avatar" }));
    expect(screen.getByRole("dialog", { name: "Choose bot avatar" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "Bot avatar 07" }));

    await waitFor(() => {
      const avatarCall = calls.find(
        (call) => call.method === "PATCH" && call.url.endsWith("/v1/team/agents/agent-1") && call.body?.avatar_id === "07",
      );
      expect(avatarCall).toBeTruthy();
    });
    const hasSelectedAvatar = screen.getAllByRole("img", { name: "Builder" }).some(
      (avatar) => avatar.querySelector("img")?.getAttribute("src") === "/assets/astra-bot-avatars/07.png",
    );
    expect(hasSelectedAvatar).toBe(true);
  });


  it("reviews a custom agent before the create request is sent", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "New custom agent" }));
    fireEvent.change(screen.getByPlaceholderText("Agent name"), { target: { value: "Verifier" } });
    fireEvent.change(screen.getByPlaceholderText("Mention handle"), { target: { value: "verifier" } });
    fireEvent.change(screen.getByLabelText("Base role"), { target: { value: "reviewer" } });
    fireEvent.change(screen.getByPlaceholderText("What should this agent do?"), { target: { value: "Verify every result." } });
    fireEvent.click(screen.getByRole("button", { name: "Review agent" }));

    expect(screen.getByText("@verifier · reviewer")).toBeTruthy();
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/agents"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/agents"))).toBe(true));
    expect(calls.find((call) => call.method === "POST" && call.url.endsWith("/v1/team/agents"))?.body).toMatchObject({ base_role: "reviewer" });
  });

  it("starts a direct chat when one agent is selected", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "New conversation" })[0]);
    fireEvent.click(screen.getByRole("checkbox", { name: "Builder" }));
    expect(screen.getByText("One agent starts a direct chat. Select at least two agents for a group.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start direct chat" }));

    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations"))).toBe(true));
    expect(calls.find((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations"))?.body).toMatchObject({
      kind: "direct",
      title: "",
      agent_ids: ["agent-1"],
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New chat" })).toBeNull());
  });

  it("creates a group conversation only after selecting two agents", async () => {
    const calls = stubTeamFetch(false, false, false, false, true);
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "New conversation" })[0]);
    fireEvent.click(screen.getByRole("checkbox", { name: "Builder" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Reviewer" }));
    fireEvent.change(screen.getByLabelText(/Group name/), { target: { value: "AI Research" } });
    fireEvent.click(screen.getByRole("button", { name: "Create group chat" }));

    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations"))).toBe(true));
    expect(calls.find((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations"))?.body).toMatchObject({
      kind: "group",
      title: "AI Research",
      agent_ids: ["agent-1", "agent-2"],
      reply_mode: "coordinator",
      coordinator_agent_id: "agent-1",
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New chat" })).toBeNull());
  });

  it("loads the Team Files view with a truthful empty state", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    expect(await screen.findByText("No Team artifacts yet.")).toBeTruthy();
    expect(calls.some((call) => call.method === "GET" && call.url.includes("/v1/team/artifacts"))).toBe(true);
  });

  it("opens task details and uses canonical assignment", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Tasks" }));
    expect(await screen.findByRole("heading", { name: "Ship release" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Task details" }));
    expect(await screen.findByText("The release is verified.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Assign task" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/tasks/task-1/assign"))).toBe(true));
    expect(calls.some((call) => call.method === "GET" && call.url.endsWith("/v1/team/tasks/task-1"))).toBe(true);
    expect(calls.some((call) => call.method === "GET" && call.url.endsWith("/v1/team/tasks/task-1/thread"))).toBe(true);
  });

  it("renders the canonical task thread and sends a reply into it", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Tasks" }));
    fireEvent.click(await screen.findByRole("button", { name: "Task details" }));
    await screen.findByText("The release is verified.");
    fireEvent.click(screen.getByRole("button", { name: "Thread" }));
    expect(await screen.findByText("Task thread")).toBeTruthy();
    expect(screen.getAllByText("Review this release").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByPlaceholderText("Reply in the task thread…"), { target: { value: "I verified the result." } });
    fireEvent.click(screen.getByRole("button", { name: "Reply in thread" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations/conversation-1/messages"))).toBe(true));
    expect(calls.find((call) => call.method === "POST" && call.url.endsWith("/v1/team/conversations/conversation-1/messages"))?.body).toMatchObject({ thread_id: "thread-task-1", parent_message_id: "message-1" });
  });

  it("uses the independent review endpoint for approval", async () => {
    const calls = stubTeamFetch(false, true);
    render(<TeamSurface />);

    fireEvent.click(screen.getByRole("tab", { name: "Tasks" }));
    expect(await screen.findByRole("heading", { name: "Ship release" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/tasks/task-1/review"))).toBe(true));
  });

  it("edits group policy and exposes destructive recovery wording", async () => {
    const calls = stubTeamFetch(true);
    render(<TeamSurface />);

    expect(await screen.findByText("Research")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Group settings" }));
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "Research Core" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PATCH" && call.url.endsWith("/v1/team/conversations/conversation-1"))).toBe(true));
    expect(calls.find((call) => call.method === "PATCH" && call.url.endsWith("/v1/team/conversations/conversation-1"))?.body).toMatchObject({ title: "Research Core" });
  });

  it("queues a file with the message and uploads it through the Team attachment API", async () => {
    const calls = stubTeamFetch();
    const { container } = render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("Message your Team…"), { target: { value: "See this file" } });
    const file = new File(["attachment"], "release.png", { type: "image/png" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new TextEncoder().encode("attachment").buffer });
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    expect(screen.getByText("release.png")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/messages/message-1/attachments"))).toBe(true));
    expect(await screen.findByText("release.png")).toBeTruthy();
  });

  it("converts a message into a durable Team task", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/tasks"))).toBe(true));
    expect(screen.getByRole("tab", { name: "Tasks" }).getAttribute("aria-selected")).toBe("true");
  });

  it("translates a message and renders the durable result", async () => {
    const calls = stubTeamFetch();
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Translate" }));
    expect(await screen.findByText("Xem bản phát hành")).toBeTruthy();
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/v1/team/messages/message-1/translate"))).toBe(true);
  });

  it("keeps conversation chat free of the run status notice", async () => {
    stubTeamFetch(false, false, true);
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    expect(screen.queryByText("Run status")).toBeNull();
    expect(screen.queryByRole("button", { name: "Expand run details" })).toBeNull();
  });

  it("shows one direct history row per agent", async () => {
    stubTeamFetch(false, false, false, true);
    render(<TeamSurface />);

    expect(await screen.findByText("Review this release")).toBeTruthy();
    const chats = screen.getByRole("region", { name: "Chats" });
    expect(within(chats).getAllByRole("listitem")).toHaveLength(1);
    expect(within(chats).getByText("Builder")).toBeTruthy();
  });

  it("filters conversations across All, Unread, Direct, Groups, and Threads tabs", async () => {
    const calls: Call[] = [];
    const testConversations = [
      {
        ...CONVERSATION,
        conversation_id: "conv-unread-direct",
        conversation_kind: "direct",
        title: "",
        unread_count: 2,
        thread_unread: {},
        updated_at: "2026-08-30T10:02:00Z",
        agent_member_ids: ["agent-1"],
      },
      {
        ...CONVERSATION,
        conversation_id: "conv-read-direct",
        conversation_kind: "direct",
        title: "",
        unread_count: 0,
        thread_unread: {},
        updated_at: "2026-08-30T10:01:00Z",
        agent_member_ids: ["agent-2"],
      },
      {
        ...CONVERSATION,
        conversation_id: "conv-group-read",
        conversation_kind: "group",
        title: "Group Read",
        unread_count: 0,
        thread_unread: {},
        updated_at: "2026-08-30T10:00:00Z",
      },
      {
        ...CONVERSATION,
        conversation_id: "conv-thread-unread",
        conversation_kind: "group",
        title: "Group With Thread Unread",
        unread_count: 0,
        thread_unread: { "message-thread-1": 1 },
        updated_at: "2026-08-30T09:59:00Z",
      },
    ];

    const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method });

      if (method === "GET" && url.endsWith("/v1/team")) {
        return {
          ok: true,
          json: async () => ({
            workspace_id: "workspace-1",
            actor_id: "human-1",
            agents: [AGENT, SECOND_AGENT],
            conversations: testConversations,
          }),
        } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/presence")) {
        return { ok: true, json: async () => ({ agents: [] }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/notifications")) {
        return { ok: true, json: async () => ({ notifications: [], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/activity")) {
        return { ok: true, json: async () => ({ activities: [], has_more: false, next_sequence: 0 }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/tasks")) {
        return { ok: true, json: async () => ({ tasks: [], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/computers")) {
        return { ok: true, json: async () => ({ computers: [] }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/runs")) {
        return { ok: true, json: async () => ({ runs: [], has_more: false }) } as Response;
      }
      if (method === "GET" && url.includes("/messages")) {
        return { ok: true, json: async () => ({ messages: [MESSAGE], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/pins")) {
        return { ok: true, json: async () => ({ pins: [] }) } as Response;
      }
      if (method === "POST" && url.includes("/read")) {
        return { ok: true, json: async () => ({ ok: true }) } as Response;
      }
      if (method === "POST" && url.includes("/threads")) {
        return {
          ok: true,
          json: async () => ({
            thread: {
              thread_id: "thread-1",
              conversation_id: "conv-thread-unread",
              root_message_id: "message-thread-1",
              created_at: "2026-08-30T10:00:00Z",
            },
            messages: [MESSAGE],
            has_more: false,
          }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          conversation_id: "conv-unread-direct",
          workspace_id: "workspace-1",
          conversation_kind: "direct",
          title: "",
          status: "active",
          reply_mode: "coordinator",
          allow_at_all: false,
          max_fanout: 8,
          members: [{ actor_id: "human-1", member_role: "owner", left_at: null, display_name: "You" }],
          agent_member_ids: ["agent-1"],
        }),
      } as Response;
    });

    vi.stubGlobal("fetch", fetchStub);
    vi.stubGlobal("WebSocket", FakeWebSocket);

    render(<TeamSurface />);
    // Default tab is "all", with unread badge 2 and threads badge 1
    expect(await screen.findByRole("tab", { name: /^All$/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Unread 2/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Threads 1/i })).toBeTruthy();

    const chats = screen.getByRole("region", { name: "Chats" });

    // On All tab: all conversations visible
    expect(within(chats).getByText("Builder")).toBeTruthy();
    expect(within(chats).getByText("Reviewer")).toBeTruthy();
    expect(within(chats).getByText("Group Read")).toBeTruthy();
    expect(within(chats).getByText("Group With Thread Unread")).toBeTruthy();

    // Switch to Unread tab: conv-unread-direct (Builder) and conv-thread-unread (Group With Thread Unread) should be visible
    fireEvent.click(screen.getByRole("tab", { name: /Unread/i }));
    expect(within(chats).getByText("Builder")).toBeTruthy();
    expect(within(chats).queryByText("Reviewer")).toBeNull();
    expect(within(chats).getByText("Group With Thread Unread")).toBeTruthy();
    expect(within(chats).queryByText("Group Read")).toBeNull();

    // Switch to Direct tab: only direct conversations visible
    fireEvent.click(screen.getByRole("tab", { name: /^Direct$/i }));
    expect(within(chats).getByText("Builder")).toBeTruthy();
    expect(within(chats).queryByText("Group Read")).toBeNull();
    expect(within(chats).queryByText("Group With Thread Unread")).toBeNull();

    // Switch to Groups tab: only group conversations visible
    fireEvent.click(screen.getByRole("tab", { name: /^Groups$/i }));
    expect(within(chats).queryByText("Builder")).toBeNull();
    expect(within(chats).getByText("Group Read")).toBeTruthy();
    expect(within(chats).getByText("Group With Thread Unread")).toBeTruthy();

    // Switch to Threads tab: only conversation with thread unread visible
    fireEvent.click(screen.getByRole("tab", { name: /Threads/i }));
    expect(within(chats).queryByText("Builder")).toBeNull();
    expect(within(chats).queryByText("Group Read")).toBeNull();
    expect(within(chats).getByText("Group With Thread Unread")).toBeTruthy();
  });

  it("renders contextual empty states for each tab when no matching conversations exist", async () => {
    const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();

      if (method === "GET" && url.endsWith("/v1/team")) {
        return {
          ok: true,
          json: async () => ({
            workspace_id: "workspace-1",
            actor_id: "human-1",
            agents: [AGENT],
            conversations: [],
          }),
        } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/presence")) {
        return { ok: true, json: async () => ({ agents: [] }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/notifications")) {
        return { ok: true, json: async () => ({ notifications: [], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/activity")) {
        return { ok: true, json: async () => ({ activities: [], has_more: false, next_sequence: 0 }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/tasks")) {
        return { ok: true, json: async () => ({ tasks: [], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/computers")) {
        return { ok: true, json: async () => ({ computers: [] }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/runs")) {
        return { ok: true, json: async () => ({ runs: [], has_more: false }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });

    vi.stubGlobal("fetch", fetchStub);
    vi.stubGlobal("WebSocket", FakeWebSocket);

    render(<TeamSurface />);

    // Default "All" tab empty state
    expect(await screen.findByText("No conversations yet. Create a direct message or group to start.")).toBeTruthy();

    // "Unread" tab empty state
    fireEvent.click(screen.getByRole("tab", { name: /^Unread$/i }));
    expect(screen.getByText("No unread messages. You're all caught up!")).toBeTruthy();

    // "Direct" tab empty state
    fireEvent.click(screen.getByRole("tab", { name: /^Direct$/i }));
    expect(screen.getByText("No direct agent chats yet.")).toBeTruthy();

    // "Groups" tab empty state
    fireEvent.click(screen.getByRole("tab", { name: /^Groups$/i }));
    expect(screen.getByText("No group conversations yet.")).toBeTruthy();

    // "Threads" tab empty state
    fireEvent.click(screen.getByRole("tab", { name: /^Threads$/i }));
    expect(screen.getByText("No active threads.")).toBeTruthy();
  });

  it("renders model reasoning trace and tool calls in bot messages", async () => {
    const messageWithThinkingAndTools: TeamMessage = {
      ...MESSAGE,
      message_id: "message-bot-1",
      author_actor_id: "agent-actor-1",
      author_kind: "agent",
      body: "Here is the result after searching and reading files.",
      reasoning: "I should first run web_search to find information, then inspect the repo.",
      metadata: {
        tool_calls: [
          {
            name: "web_search",
            arguments: { query: "Astra release notes" },
            result: { results: [{ title: "Astra v2", url: "https://example.com" }] },
          },
          {
            name: "read_file",
            arguments: { path: "README.md" },
            result: "# Astra Documentation",
          },
        ],
      },
    };

    const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();

      if (method === "GET" && url.endsWith("/v1/team")) {
        return {
          ok: true,
          json: async () => ({
            workspace_id: "workspace-1",
            actor_id: "human-1",
            agents: [AGENT],
            conversations: [CONVERSATION],
          }),
        } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/presence")) {
        return { ok: true, json: async () => ({ agents: [] }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/notifications")) {
        return { ok: true, json: async () => ({ notifications: [], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/activity")) {
        return { ok: true, json: async () => ({ activities: [], has_more: false, next_sequence: 0 }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/tasks")) {
        return { ok: true, json: async () => ({ tasks: [], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/computers")) {
        return { ok: true, json: async () => ({ computers: [] }) } as Response;
      }
      if (method === "GET" && url.includes("/v1/team/runs")) {
        return { ok: true, json: async () => ({ runs: [], has_more: false }) } as Response;
      }
      if (method === "GET" && url.includes("/messages")) {
        return { ok: true, json: async () => ({ messages: [messageWithThinkingAndTools], has_more: false, next_cursor: null }) } as Response;
      }
      if (method === "GET" && url.includes("/pins")) {
        return { ok: true, json: async () => ({ pins: [] }) } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          ...CONVERSATION,
        }),
      } as Response;
    });

    vi.stubGlobal("fetch", fetchStub);
    vi.stubGlobal("WebSocket", FakeWebSocket);

    render(<TeamSurface />);

    // Verify bot message body renders
    expect(await screen.findByText("Here is the result after searching and reading files.")).toBeTruthy();

    // Verify Thinking disclosure toggle renders
    const thinkingToggle = screen.getByTestId("thinking-toggle");
    expect(thinkingToggle).toBeTruthy();
    expect(screen.queryByText("I should first run web_search to find information, then inspect the repo.")).toBeNull();

    // Expanding thinking toggle reveals reasoning text
    fireEvent.click(thinkingToggle);
    expect(screen.getByText("I should first run web_search to find information, then inspect the repo.")).toBeTruthy();

    // Verify tool calls render with tool names
    const toolContainer = screen.getByTestId("tool-calls-message-bot-1");
    expect(toolContainer).toBeTruthy();
    expect(within(toolContainer).getByText("web_search")).toBeTruthy();
    expect(within(toolContainer).getByText("read_file")).toBeTruthy();
    expect(within(toolContainer).getAllByText("done")).toHaveLength(2);
  });
});
