import type { GroupedQuestion, QuestionOption, SessionInfo, WsEvent } from "./types";

declare const __COWORKER_DEV_TOKEN__: string;

// Endpoint resolution order: runtime-injected globals (Tauri sets `window.__COWORKER_HTTP__`
// for its dynamically-chosen sidecar port) → Vite env → the 127.0.0.1:8765 dev default. This
// keeps a single codebase: browser `npm run dev` hits 8765; the desktop shell hits its sidecar.
const httpBase = (): string =>
  (globalThis as any).__COWORKER_HTTP__ ||
  (import.meta as any).env?.VITE_COWORKER_HTTP ||
  "http://127.0.0.1:8765";
const wsBase = (): string =>
  (globalThis as any).__COWORKER_WS__ ||
  (import.meta as any).env?.VITE_COWORKER_WS ||
  "ws://127.0.0.1:8765";
const apiToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  (typeof __COWORKER_DEV_TOKEN__ === "string" ? __COWORKER_DEV_TOKEN__ : "");

// All local REST calls pass through this module, so a module-local wrapper applies launch
// authentication without asking every endpoint helper to remember the security header.
const fetch = (
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> => {
  const headers = new Headers(init.headers);
  const token = apiToken();
  if (token) headers.set("X-Astra-Token", token);
  return globalThis.fetch(input, { ...init, headers });
};

const openWebSocket = (url: string): WebSocket => {
  const token = apiToken();
  return token
    ? new WebSocket(url, ["astra", token])
    : new WebSocket(url);
};

export function getBrowserProxyUrl(targetUrl: string): string {
  const token = apiToken();
  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : "";
  return `${httpBase()}/v1/browser/proxy?url=${encodeURIComponent(targetUrl)}${tokenParam}`;
}

export interface Health {
  status: string;
  default_workspace: string | null;
  model: string;
}

export interface RecentWorkspace {
  path: string;
  name: string;
  exists: boolean;
}

export interface WorkspaceCommandTrust {
  workspace: string;
  requested_commands: string[];
  trusted: boolean;
  required: boolean;
  exists?: boolean;
}

export async function getHealth(): Promise<Health> {
  const res = await fetch(`${httpBase()}/v1/health`);
  return res.json();
}

export async function getRecentWorkspaces(): Promise<RecentWorkspace[]> {
  const res = await fetch(`${httpBase()}/v1/workspaces/recent`);
  return (await res.json()).workspaces ?? [];
}

/** Ask the LOCAL sidecar to open the OS folder picker — the browser GUI can't obtain absolute
 * paths from web file dialogs. Blocks until the user picks or cancels; null on cancel/unavailable. */
export async function pickFolderViaServer(): Promise<string | null> {
  try {
    const res = await fetch(`${httpBase()}/v1/workspaces/pick`, { method: "POST" });
    const d = await res.json();
    return d.ok && d.path ? d.path : null;
  } catch {
    return null;
  }
}

export async function openWorkspace(
  path: string,
  create = false,
): Promise<{
  path: string;
  ok: boolean;
  error?: string;
  git_branch?: string | null;
  command_trust?: WorkspaceCommandTrust;
}> {
  const res = await fetch(`${httpBase()}/v1/workspaces/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create }),
  });
  return res.json();
}

/** UX-029 "Start in a temporary folder": create the conversation's temp dir at send time
 * (git-init'd for code-family work). Idempotent. */
export async function createTempWorkspace(
  sessionId: string,
  git = true,
): Promise<{ ok: boolean; path?: string; git?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/workspaces/temp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, git }),
  });
  return res.json();
}

/** UX-029 "Save as project…": move a session's temporary folder to a real location.
 * Callers reconnect afterwards so the engine rebinds to the new path. */
export async function saveSessionAsProject(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/save-as-project`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
  return res.json();
}

export async function getTrustedWorkspaces(): Promise<WorkspaceCommandTrust[]> {
  const res = await fetch(`${httpBase()}/v1/workspaces/trusted`);
  return (await res.json()).workspaces ?? [];
}

export async function setWorkspaceTrusted(
  path: string,
  trusted: boolean,
): Promise<{ ok: boolean; error?: string } & WorkspaceCommandTrust> {
  const res = await fetch(`${httpBase()}/v1/workspaces/trust`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, trusted }),
  });
  return res.json();
}

export async function getSessions(workspace?: string): Promise<SessionInfo[]> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(`${httpBase()}/v1/sessions${q}`);
  return (await res.json()).sessions ?? [];
}

// A structured connector-delivered inbound message (§3.1). Attached to the user message it framed,
// for display only — the model still sees the framed `content`; this drives the ConnectorMessageCard.
export interface MessageSource {
  connector: string; // platform id, e.g. "slack"
  kind: "channel" | "dm";
  channel_id: string; // e.g. "C0BD7KZ1AH5"
  channel_name: string; // resolved; may equal the id (e.g. "#ocw-test")
  sender_id: string;
  sender_name: string; // resolved; may equal the id
  ts: number; // epoch seconds
  text: string; // the RAW message (what the card shows)
  // Board wakes only (connector === "board"): the digest as structured rows, so
  // the BoardWakeCard renders collapsed summaries instead of re-parsing prose.
  board?: { rows: BoardWakeRow[] };
}

// One digest event on a board wake. `note` is a UI-clamped excerpt of a hand-off
// comment (the full text lives on the board).
export interface BoardWakeRow {
  kind: "assigned" | "claimed" | "moved" | "filed" | "comment" | "chat" | string;
  item?: number | null;
  title?: string;
  actor?: string;
  to?: string;
  note?: string;
}

// A transcript message from GET /v1/sessions/{id}/messages. Kept permissive (open shape) because
// itemsFromMessages reads several role-specific fields; `source` is the optional connector sidecar.
export interface ConversationMessage {
  role: string;
  content?: any;
  tool_calls?: any[];
  tool_call_id?: string;
  source?: MessageSource;
  // Token counts for the round-trip that produced an assistant message
  // ({model, input, output, cache_read, cache_write}); absent on older servers.
  usage?: import("./types").TurnUsage;
  [key: string]: any;
}

export async function getSessionMessages(sessionId: string): Promise<ConversationMessage[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${sessionId}/messages`);
  return (await res.json()).messages ?? [];
}

export async function renameSession(sessionId: string, title: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return res.json();
}

export async function setSessionFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(flags),
  });
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  return res.json();
}

export async function deleteAllSessions(): Promise<{ ok: boolean; deleted: number; error?: string }> {
  try {
    const all = await getSessions();
    let deleted = 0;
    for (const s of all) {
      if (s.session_id && !s.session_id.startsWith("__")) {
        const res = await deleteSession(s.session_id);
        if (res.ok) deleted++;
      }
    }
    return { ok: true, deleted };
  } catch (err: any) {
    return { ok: false, deleted: 0, error: err?.message || "Failed to delete sessions" };
  }
}

// Agent teams (OPE-96): the session's board — items on the workspace-keyed space.
export interface BoardItem {
  id: number;
  title: string;
  description: string;
  criteria: string;
  state: "open" | "in_progress" | "blocked" | "review" | "done" | "canceled" | string;
  assignee: string;
  creator: string;
  refs: string[];
  links: { kind: string; item: number }[];
  // Blocked rows only: the latest blocker comment, clamped ("need tfvars…").
  blocker?: string;
}

export interface Board {
  space: string | null;
  name: string;
  items: BoardItem[];
}

export interface JournalCase {
  case: string;
  entries: number;
  last_ts: string;
}

export async function getBoard(sessionId: string): Promise<Board> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/board`);
  return res.json();
}

// One event in an item's merged timeline (the detail pane renders the item's
// whole story: filed → assigned/claimed → moves → comments, with attachments).
export interface BoardTimelineEvent {
  seq: number;
  ts: string;
  actor: string;
  kind: "created" | "assigned" | "claimed" | "moved" | "comment" | string;
  to?: string;
  assignee?: string;
  body?: string;
  refs?: string[];
}

export type BoardItemDetail = BoardItem & { timeline?: BoardTimelineEvent[] };

export async function getBoardItem(
  sessionId: string,
  id: number,
): Promise<BoardItemDetail | { error: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/board/item?id=${id}`,
  );
  return res.json();
}

// Attachment bytes → an object URL for <img>. The module fetch wrapper carries
// the sidecar token, which a bare <img src> cannot.
export async function fetchBoardAttachment(
  sessionId: string,
  stored: string,
): Promise<string | null> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/board/attachment?name=${encodeURIComponent(stored)}`,
  );
  if (!res.ok) return null;
  return URL.createObjectURL(await res.blob());
}

// A pure note on an item — never changes state; the assignee hears it via its feed.
export async function boardComment(
  sessionId: string,
  item: number,
  body: string,
): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/board/comment`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item, body }),
    },
  );
  return res.json();
}

export async function boardTransition(
  sessionId: string,
  item: number,
  to: string,
  comment = "",
): Promise<BoardItem | { error: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/board/transition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item, to, comment }),
  });
  return res.json();
}

export interface ChatMessage {
  seq: number;
  ts: string;
  author: string;
  author_role: "user" | "lead" | "worker" | string;
  text: string;
  mentions: string[];
}

export interface TeamChat {
  enabled: boolean;
  team_id?: string;
  members: { name: string; persona: string; role: string }[];
  messages: ChatMessage[];
}

export async function getTeamChat(teamId: string): Promise<TeamChat> {
  const res = await fetch(`${httpBase()}/v1/teams/${encodeURIComponent(teamId)}/chat`);
  return res.json();
}

export async function postTeamChat(teamId: string, text: string): Promise<ChatMessage | { error: string }> {
  const res = await fetch(`${httpBase()}/v1/teams/${encodeURIComponent(teamId)}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return res.json();
}

export interface TeamAgent {
  agent_id: string;
  actor_id: string;
  display_name: string;
  handle?: string | null;
  description?: string;
  base_role?: string;
  avatar_id?: string | null;
  status: string;
}

export interface TeamAgentPackage {
  format: string;
  version: number;
  definition: {
    display_name: string;
    handle: string;
    description: string;
    base_role: string;
    avatar_id?: string | null;
  };
  revision: {
    instructions: string;
    model_policy: Record<string, unknown>;
    sandbox_policy: Record<string, unknown>;
    memory_policy: Record<string, unknown>;
    reply_policy: Record<string, unknown>;
    budget_policy: Record<string, unknown>;
    approval_policy: Record<string, unknown>;
    capabilities: Record<string, unknown>[];
  };
  installation: { max_concurrency: number };
  sandbox: {
    isolation_level: string;
    mounts: { target_path: string; access_mode: string }[];
  };
}

export interface TeamComputer {
  computer_id: string;
  workspace_id: string;
  agent_id: string;
  provider_kind: string;
  status: "provisioning" | "ready" | "suspended" | "offline" | "error" | "destroyed" | string;
  capabilities: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  destroyed_at?: string | null;
}

export interface TeamConversationMember {
  conversation_id: string;
  actor_id: string;
  member_role: string;
  history_policy: string;
  joined_at: string;
  left_at?: string | null;
  muted_until?: string | null;
  actor_kind?: string;
  display_name?: string;
}

export interface TeamConversation {
  conversation_id: string;
  workspace_id: string;
  conversation_kind: "direct" | "group" | string;
  title: string;
  status: "active" | "archived" | "deleted" | string;
  reply_mode: string;
  coordinator_agent_id?: string | null;
  allow_at_all: boolean;
  max_fanout: number;
  updated_at?: string;
  members?: TeamConversationMember[];
  member_count?: number;
  agent_member_ids?: string[];
  agent_actor_ids?: string[];
  pinned?: boolean;
  unread_count?: number;
  thread_unread?: Record<string, number>;
}

export interface TeamMessage {
  message_id: string;
  conversation_id: string;
  thread_id?: string | null;
  parent_message_id?: string | null;
  quote_message_id?: string | null;
  author_actor_id: string;
  author_kind: string;
  body: string;
  status: "active" | "tombstoned" | string;
  created_at: string;
  edited_at?: string | null;
  revision_number?: number;
  mentions: string[];
  reactions: { actor_id: string; emoji: string; created_at: string }[];
  attachments: TeamMessageAttachment[];
  translations: TeamMessageTranslation[];
  reasoning?: string | null;
  metadata?: {
    tool_calls?: Array<{
      name: string;
      arguments?: Record<string, unknown>;
      result?: unknown;
    }>;
  } | null;
}

export interface TeamMessageAttachment {
  attachment_id: string;
  message_id: string;
  storage_ref: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface TeamMessageTranslation {
  language: string;
  body: string;
  model?: string;
  created_at: string;
}

export interface TeamPinnedMessage {
  conversation_id: string;
  message_id: string;
  pinned_by_actor_id: string;
  created_at: string;
  body: string;
  author_actor_id: string;
  status: string;
}

export interface TeamThread {
  thread_id: string;
  conversation_id: string;
  root_message_id: string;
  created_at: string;
  archived_at?: string | null;
}

export async function openTeamThread(
  conversationId: string,
  rootMessageId: string,
): Promise<{ thread: TeamThread; messages: TeamMessage[]; next_cursor?: string | null; has_more: boolean }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root_message_id: rootMessageId }),
  });
  return res.json();
}

export async function getTeamThread(
  threadId: string,
): Promise<{ thread: TeamThread; messages: TeamMessage[]; next_cursor?: string | null; has_more: boolean }> {
  const res = await fetch(`${httpBase()}/v1/team/threads/${encodeURIComponent(threadId)}`);
  return res.json();
}

export interface TeamOverview {
  workspace_id: string;
  actor_id: string;
  agents: TeamAgent[];
  conversations: TeamConversation[];
  counts?: { agents: number; conversations: number; tasks: number; unread: number };
}

export interface TeamPresence extends TeamAgent {
  sandbox_id?: string | null;
  sandbox_status?: string | null;
  active_runs: number;
  queued_deliveries: number;
  state: "ready" | "working" | "waiting" | "paused" | "offline" | "error" | "archived" | string;
  reason: string;
  ready: boolean;
  capacity: number;
}

export type TeamSearchKind = "messages" | "tasks" | "artifacts" | "agents" | "conversations";

export interface TeamSearchRecord {
  cursor: string;
  [key: string]: unknown;
}

export interface TeamSearchPage {
  results: TeamSearchRecord[];
  next_cursor?: string | null;
  has_more: boolean;
}

export async function searchTeam(
  query: string,
  kinds: TeamSearchKind[] = ["messages", "tasks", "artifacts", "agents", "conversations"],
): Promise<Record<string, TeamSearchPage> & { error?: string }> {
  const params = new URLSearchParams({ q: query });
  for (const kind of kinds) params.append("kind", kind);
  const res = await fetch(`${httpBase()}/v1/team/search?${params.toString()}`);
  return res.json();
}

export async function getTeamOverview(): Promise<TeamOverview> {
  const res = await fetch(`${httpBase()}/v1/team`);
  return res.json();
}

export async function getTeamPresence(): Promise<TeamPresence[]> {
  const res = await fetch(`${httpBase()}/v1/team/presence`);
  return (await res.json()).agents ?? [];
}

export interface TeamNotification {
  notification_id: string;
  workspace_id: string;
  recipient_actor_id: string;
  notification_kind: string;
  aggregate_type: string;
  aggregate_id: string;
  dedupe_key: string;
  read_at?: string | null;
  created_at: string;
  expires_at?: string | null;
  link?: {
    surface: "team" | "inbox" | "automations" | string;
    view?: "chat" | "tasks" | string;
    conversation_id?: string;
    thread_id?: string | null;
    message_id?: string;
    task_id?: string;
    approval_id?: string;
    automation_id?: string;
  };
}

export interface TeamNotificationPreferences {
  workspace_id: string;
  actor_id: string;
  muted: boolean;
  mention_only: boolean;
  quiet_hours: { start?: string; end?: string; timezone?: string };
  event_preferences: Record<string, boolean>;
  updated_at?: string | null;
}

export interface TeamApproval {
  approval_id: string;
  workspace_id: string;
  request_id?: string | null;
  run_id?: string | null;
  requested_by_actor_id: string;
  approver_actor_id?: string | null;
  action_kind: string;
  resource_kind: string;
  resource_id: string;
  status: "pending" | "approved" | "denied" | "expired" | "canceled" | string;
  reason: string;
  expires_at?: string | null;
  decided_at?: string | null;
  created_at: string;
}

export async function getTeamNotifications(options: {
  unreadOnly?: boolean;
  cursor?: string | null;
  limit?: number;
} = {}): Promise<{ notifications: TeamNotification[]; next_cursor?: string | null; has_more: boolean }> {
  const params = new URLSearchParams();
  if (options.unreadOnly) params.set("unread_only", "true");
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  const res = await fetch(`${httpBase()}/v1/team/notifications${query ? `?${query}` : ""}`);
  return res.json();
}

export async function markTeamNotificationRead(notificationId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${httpBase()}/v1/team/notifications/${encodeURIComponent(notificationId)}/read`, { method: "POST" });
  return res.json();
}

export async function getTeamNotificationPreferences(): Promise<TeamNotificationPreferences> {
  const res = await fetch(`${httpBase()}/v1/team/notification-preferences`);
  return res.json();
}

export async function updateTeamNotificationPreferences(
  changes: Partial<Omit<TeamNotificationPreferences, "workspace_id" | "actor_id" | "updated_at">>,
): Promise<TeamNotificationPreferences> {
  const res = await fetch(`${httpBase()}/v1/team/notification-preferences`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function getTeamApprovals(options: {
  statuses?: string[];
  requestId?: string;
  runId?: string;
  limit?: number;
} = {}): Promise<{ approvals: TeamApproval[]; error?: string }> {
  const query = new URLSearchParams();
  for (const status of options.statuses || []) query.append("status", status);
  if (options.requestId) query.set("request_id", options.requestId);
  if (options.runId) query.set("run_id", options.runId);
  if (options.limit) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${httpBase()}/v1/team/approvals${suffix}`);
  return res.json();
}

export async function getTeamApproval(approvalId: string): Promise<TeamApproval & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/approvals/${encodeURIComponent(approvalId)}`);
  return res.json();
}

export async function decideTeamApproval(
  approvalId: string,
  status: "approved" | "denied",
): Promise<TeamApproval & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/approvals/${encodeURIComponent(approvalId)}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return res.json();
}

export async function cancelTeamApproval(approvalId: string): Promise<TeamApproval & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/approvals/${encodeURIComponent(approvalId)}/cancel`, {
    method: "POST",
  });
  return res.json();
}

export async function createTeamAgent(payload: {
  display_name: string;
  handle: string;
  instructions: string;
  base_role?: string;
  avatar_id?: string;
  capabilities?: Array<{
    capability_kind: string;
    capability_ref: string;
    required?: boolean;
    config?: Record<string, unknown>;
  }>;
}): Promise<TeamAgent & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function getTeamAgentExport(agentId: string): Promise<TeamAgentPackage & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/agents/${encodeURIComponent(agentId)}/export`);
  return res.json();
}

export async function getTeamComputers(options: { limit?: number } = {}): Promise<{ computers: TeamComputer[]; error?: string }> {
  const query = options.limit ? `?limit=${encodeURIComponent(String(options.limit))}` : "";
  const res = await fetch(`${httpBase()}/v1/team/computers${query}`);
  return res.json();
}
716
export interface TeamComputerSession {
  session_id: string;
  computer_id: string;
  workspace_id: string;
  agent_id: string;
  owner_actor_id: string;
  run_id?: string | null;
  status: "active" | "closed" | "lost" | string;
  active_lease_id?: string | null;
  viewport: { width: number; height: number };
  created_at: string;
  closed_at?: string | null;
}
730
export interface TeamComputerObservation {
  session_id: string;
  url: string;
  title: string;
  viewport: { width: number; height: number };
  screenshot_b64?: string;
  mouse_position?: { x: number; y: number };
  error?: string;
}
740
export interface TeamScreenLease {
  lease_id: string;
  session_id: string;
  workspace_id: string;
  actor_id: string;
  acquired_at: string;
  expires_at: string;
  released_at?: string | null;
}
750
export interface TeamTakeoverRequest {
  takeover_id: string;
  session_id: string;
  workspace_id: string;
  actor_id: string;
  run_id?: string | null;
  reason: string;
  status: "pending" | "approved" | "rejected" | string;
  created_at: string;
  decided_at?: string | null;
}
762
export async function createTeamComputerSession(
  computerId: string,
  payload: { agent_id?: string; run_id?: string } = {},
): Promise<TeamComputerSession & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/${encodeURIComponent(computerId)}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}
774
export async function getTeamComputerSession(sessionId: string): Promise<TeamComputerSession & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}`);
  return res.json();
}
779
export async function reconnectTeamComputerSession(sessionId: string): Promise<TeamComputerSession & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/reconnect`, {
    method: "POST",
  });
  return res.json();
}
786
export async function getTeamComputerObservation(sessionId: string): Promise<TeamComputerObservation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/observe`);
  return res.json();
}
791
export async function getTeamComputerScreenshot(sessionId: string): Promise<{ session_id: string; data_b64: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/screenshot`);
  return res.json();
}
796
export async function navigateTeamComputerSession(
  sessionId: string,
  url: string,
): Promise<TeamComputerObservation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/navigate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return res.json();
}
808
export async function sendTeamComputerAction(
  sessionId: string,
  action: Record<string, unknown>,
  leaseId: string,
): Promise<TeamComputerObservation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...action, lease_id: leaseId }),
  });
  return res.json();
}
821
export async function acquireTeamScreenLease(
  sessionId: string,
  leaseSeconds = 60,
): Promise<TeamScreenLease & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/lease`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lease_seconds: leaseSeconds }),
  });
  return res.json();
}
833
export async function releaseTeamScreenLease(
  sessionId: string,
  leaseId: string,
): Promise<{ released: boolean; lease_id: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/lease/${encodeURIComponent(leaseId)}`, {
    method: "DELETE",
  });
  return res.json();
}
843
export async function requestTeamComputerTakeover(
  sessionId: string,
  payload: { reason?: string; run_id?: string } = {},
): Promise<TeamTakeoverRequest & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/computers/sessions/${encodeURIComponent(sessionId)}/takeover`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}
855
export async function decideTeamComputerTakeover(
  takeoverId: string,
  status: "approved" | "rejected",
): Promise<TeamTakeoverRequest & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/takeovers/${encodeURIComponent(takeoverId)}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return res.json();
}

export async function updateTeamAgent(
  agentId: string,
  changes: Record<string, unknown>,
): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/agents/${encodeURIComponent(agentId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function runTeamAgentAction(
  agentId: string,
  action: "pause" | "stop" | "resume" | "restart" | "archive" | "reset" | "clone" | "delete",
  body: Record<string, unknown> = {},
): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/agents/${encodeURIComponent(agentId)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export interface TeamPlaygroundResult {
  exit_code?: number | null;
  stdout?: string;
  stderr?: string;
  timed_out?: boolean;
  error?: string;
}

export async function runTeamAgentPlayground(agentId: string, command: string[]): Promise<TeamPlaygroundResult> {
  const res = await fetch(`${httpBase()}/v1/team/agents/${encodeURIComponent(agentId)}/playground`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command }),
  });
  return res.json();
}

export async function getTeamAgentSandboxHealth(agentId: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/agents/${encodeURIComponent(agentId)}/sandbox/health`);
  return res.json();
}

export async function getTeamConversations(includeArchived = false): Promise<TeamConversation[]> {
  const res = await fetch(`${httpBase()}/v1/team/conversations?include_archived=${includeArchived ? "true" : "false"}`);
  return (await res.json()).conversations ?? [];
}

export async function setTeamConversationPinned(
  conversationId: string,
  pinned: boolean,
): Promise<{ conversation_id: string; actor_id: string; pinned: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/pin`, {
    method: pinned ? "POST" : "DELETE",
  });
  return res.json();
}

export async function getTeamConversation(conversationId: string): Promise<TeamConversation> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}`);
  return res.json();
}

export async function updateTeamConversation(
  conversationId: string,
  changes: Partial<Pick<TeamConversation, "title" | "reply_mode" | "coordinator_agent_id" | "allow_at_all" | "max_fanout">>,
): Promise<TeamConversation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function transitionTeamConversation(
  conversationId: string,
  action: "archive" | "restore" | "delete",
): Promise<TeamConversation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/${action}`, {
    method: action === "delete" ? "DELETE" : "POST",
  });
  return res.json();
}

export async function addTeamConversationAgent(conversationId: string, agentId: string): Promise<TeamConversation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id: agentId }),
  });
  return res.json();
}

export async function removeTeamConversationAgent(conversationId: string, agentId: string): Promise<TeamConversation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/members/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function getTeamMessages(
  conversationId: string,
  options: { cursor?: string | null; limit?: number; threadId?: string | null } = {},
): Promise<{ messages: TeamMessage[]; next_cursor?: string | null; has_more: boolean }> {
  const query = new URLSearchParams();
  if (options.cursor) query.set("cursor", options.cursor);
  if (options.limit) query.set("limit", String(options.limit));
  if (options.threadId) query.set("thread_id", options.threadId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/messages${suffix}`);
  return res.json();
}

export async function createTeamConversation(payload: {
  kind: "direct" | "group";
  title?: string;
  agent_ids: string[];
  reply_mode?: string;
  coordinator_agent_id?: string | null;
  allow_at_all?: boolean;
}): Promise<TeamConversation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function postTeamMessage(
  conversationId: string,
  payload: {
    body: string;
    client_request_id: string;
    mention_actor_ids?: string[];
    at_all?: boolean;
    fanout_confirmed?: boolean;
    thread_id?: string | null;
    parent_message_id?: string | null;
    quote_message_id?: string | null;
  },
): Promise<{ message?: TeamMessage; request?: Record<string, unknown> | null; deliveries?: Record<string, unknown>[]; routing?: Record<string, unknown> | null; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function addTeamMessageAttachment(
  messageId: string,
  file: File,
): Promise<TeamMessageAttachment & { error?: string }> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  const res = await fetch(`${httpBase()}/v1/team/messages/${encodeURIComponent(messageId)}/attachments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_b64: btoa(binary), filename: file.name, media_type: file.type }),
  });
  return res.json();
}

export function teamMessageAttachmentUrl(messageId: string, attachmentId: string): string {
  return `${httpBase()}/v1/team/messages/${encodeURIComponent(messageId)}/attachments/${encodeURIComponent(attachmentId)}`;
}

export async function translateTeamMessage(
  messageId: string,
  language: string,
): Promise<TeamMessageTranslation & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/messages/${encodeURIComponent(messageId)}/translate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language }),
  });
  return res.json();
}

export async function editTeamMessage(
  messageId: string,
  body: string,
  expectedRevision?: number,
): Promise<TeamMessage & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/messages/${encodeURIComponent(messageId)}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body, expected_revision: expectedRevision }),
  });
  return res.json();
}

export async function deleteTeamMessage(messageId: string): Promise<TeamMessage & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/messages/${encodeURIComponent(messageId)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function setTeamMessageReaction(
  messageId: string,
  emoji: string,
  active: boolean,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${httpBase()}/v1/team/messages/${encodeURIComponent(messageId)}/reaction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ emoji, active }),
  });
  return res.json();
}

export async function getTeamPinnedMessages(
  conversationId: string,
): Promise<{ pins: TeamPinnedMessage[] }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/pins`);
  return res.json();
}

export async function pinTeamMessage(
  conversationId: string,
  messageId: string,
): Promise<TeamPinnedMessage & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/pin`, {
    method: "POST",
  });
  return res.json();
}

export async function unpinTeamMessage(
  conversationId: string,
  messageId: string,
): Promise<{ conversation_id: string; message_id: string; removed: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/pin`, {
    method: "DELETE",
  });
  return res.json();
}

export async function markTeamConversationRead(conversationId: string, messageId: string | null): Promise<Record<string, unknown>> {
  const res = await fetch(`${httpBase()}/v1/team/conversations/${encodeURIComponent(conversationId)}/read`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: messageId }),
  });
  return res.json();
}

export interface TeamTask {
  task_id: string;
  workspace_id: string;
  title: string;
  description: string;
  acceptance_criteria: string;
  status: "open" | "in_progress" | "blocked" | "review" | "done" | "canceled" | string;
  parent_task_id?: string | null;
  owner_agent_id?: string | null;
  reviewer_actor_id?: string | null;
  priority: number;
  due_at?: string | null;
  created_at?: string;
  updated_at: string;
  dependencies: { task_id: string; depends_on_task_id: string; status: string }[];
  watchers: { actor_id: string; display_name?: string; handle?: string | null }[];
  labels: string[];
  runs?: TeamRun[];
}

export interface TeamTaskEvent {
  event_id: string;
  task_id: string;
  workspace_id: string;
  actor_id?: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface TeamVerificationAttempt {
  attempt_id: string;
  task_id: string;
  run_id?: string | null;
  criteria_index: number;
  passed: boolean;
  score?: number | null;
  evidence: Record<string, unknown>;
  feedback: string;
  created_at: string;
}

export interface TeamHandoff {
  handoff_id: string;
  task_id: string;
  sender_agent_id: string;
  recipient_agent_id: string;
  purpose: string;
  expected_output: string;
  artifact_ids: string[];
  budget: Record<string, number | null>;
  depth: number;
  agent_path: string[];
  status: string;
  idempotency_key: string;
  correlation_id: string;
  created_at: string;
  accepted_at?: string | null;
  completed_at?: string | null;
}

export interface TeamRun {
  run_id: string;
  delivery_id: string;
  request_id: string;
  workspace_id: string;
  agent_id: string;
  agent_revision_id: string;
  sandbox_id: string;
  task_id?: string | null;
  status: string;
  budget: Record<string, number | null>;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  usage?: {
    request_id: string;
    tokens: number;
    cost_micros: number;
    model_calls: number;
    tool_calls: number;
    wall_seconds: number;
    wakes: number;
    fanout: number;
    handoffs: number;
  };
}

export interface TeamRunDetail {
  run: TeamRun;
  delivery: Record<string, unknown> | null;
  usage: NonNullable<TeamRun["usage"]>;
  events: {
    run_event_id: string;
    run_id: string;
    event_type: string;
    actor_id?: string | null;
    payload: Record<string, unknown>;
    occurred_at: string;
  }[];
  approvals?: TeamApproval[];
  error?: string;
}

export async function getTeamTasks(options: {
  statuses?: string[];
  cursor?: string | null;
  limit?: number;
} = {}): Promise<{ tasks: TeamTask[]; next_cursor?: string | null; has_more: boolean; error?: string }> {
  const query = new URLSearchParams();
  for (const status of options.statuses || []) query.append("status", status);
  if (options.cursor) query.set("cursor", options.cursor);
  if (options.limit) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${httpBase()}/v1/team/tasks${suffix}`);
  return res.json();
}

export async function getTeamTask(taskId: string): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}`);
  return res.json();
}

export interface TeamTaskThread {
  task: TeamTask;
  thread: TeamThread;
  messages: TeamMessage[];
  next_cursor?: string | null;
  has_more: boolean;
  error?: string;
}

export async function getTeamTaskThread(taskId: string): Promise<TeamTaskThread> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/thread`);
  return res.json();
}

export async function getTeamRuns(options: {
  agentId?: string;
  taskId?: string;
  conversationId?: string;
  statuses?: string[];
  limit?: number;
} = {}): Promise<{ runs: TeamRun[]; error?: string }> {
  const query = new URLSearchParams();
  if (options.agentId) query.set("agent_id", options.agentId);
  if (options.taskId) query.set("task_id", options.taskId);
  if (options.conversationId) query.set("conversation_id", options.conversationId);
  for (const status of options.statuses || []) query.append("status", status);
  if (options.limit) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${httpBase()}/v1/team/runs${suffix}`);
  return res.json();
}

export async function getTeamRun(runId: string): Promise<TeamRunDetail> {
  const res = await fetch(`${httpBase()}/v1/team/runs/${encodeURIComponent(runId)}`);
  return res.json();
}

export async function createTeamTask(payload: {
  title: string;
  description?: string;
  acceptance_criteria: string;
  parent_task_id?: string | null;
  owner_agent_id?: string | null;
  reviewer_actor_id?: string | null;
  priority?: number;
  due_at?: string | null;
  labels?: string[];
}): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function cancelTeamTask(taskId: string): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
  return res.json();
}

export async function reopenTeamTask(taskId: string): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/reopen`, {
    method: "POST",
  });
  return res.json();
}

export async function addTeamTaskWatcher(taskId: string, actorId?: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/watchers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor_id: actorId }),
  });
  return res.json();
}

export async function removeTeamTaskWatcher(taskId: string, actorId: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/watchers/${encodeURIComponent(actorId)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function addTeamTaskLabel(taskId: string, label: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/labels`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  return res.json();
}

export async function removeTeamTaskLabel(taskId: string, label: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/labels/${encodeURIComponent(label)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function getTeamTaskEvents(taskId: string): Promise<{ events: TeamTaskEvent[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/events`);
  return res.json();
}

export async function getTeamTaskVerifications(taskId: string): Promise<{ verifications: TeamVerificationAttempt[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/verifications`);
  return res.json();
}

export async function getTeamTaskHandoffs(taskId: string): Promise<{ handoffs: TeamHandoff[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/handoffs`);
  return res.json();
}

export async function createTeamTaskHandoff(taskId: string, payload: {
  sender_agent_id: string;
  recipient_agent_id: string;
  purpose: string;
  expected_output: string;
  artifact_ids?: string[];
  budget?: Record<string, number | null>;
}): Promise<{ request?: Record<string, unknown> | null; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/handoffs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function transitionTeamTask(taskId: string, status: string): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/transition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return res.json();
}

export async function reviewTeamTask(
  taskId: string,
  approved: boolean,
  comment = "",
): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, comment }),
  });
  return res.json();
}

export async function assignTeamTask(taskId: string, ownerAgentId: string): Promise<TeamTask & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_agent_id: ownerAgentId }),
  });
  return res.json();
}

export async function addTeamTaskDependency(taskId: string, dependsOnTaskId: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/dependencies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
  });
  return res.json();
}

export async function dispatchTeamTask(taskId: string, payload: {
  instruction?: string;
  expected_output?: string;
  budget?: Record<string, number | null>;
  priority?: number;
  client_request_id?: string;
} = {}): Promise<{
  request?: Record<string, unknown> | null;
  deliveries?: Record<string, unknown>[];
  error?: string;
}> {
  const res = await fetch(`${httpBase()}/v1/team/tasks/${encodeURIComponent(taskId)}/dispatch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export interface TeamArtifactRevision {
  artifact_revision_id: string;
  artifact_id: string;
  revision_number: number;
  source_run_id: string;
  source_task_id?: string | null;
  source_ref_kind: string;
  source_ref_id: string;
  publisher_actor_id: string;
  requester_actor_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface TeamArtifact {
  artifact_id: string;
  workspace_id: string;
  status: "published" | "archived" | "deleted" | string;
  visibility: string;
  scope_id: string;
  created_at: string;
  quarantined_at?: string | null;
  quarantine_reason?: string | null;
  revisions: TeamArtifactRevision[];
  links: { linked_aggregate_type: string; linked_aggregate_id: string; artifact_revision_id: string; created_at: string }[];
}

export interface TeamArtifactDownload {
  artifact_id: string;
  revision_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  storage_ref: string;
  content_available: boolean;
  error?: string;
}

export async function getTeamArtifacts(limit = 100): Promise<{ artifacts: TeamArtifact[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/artifacts?limit=${encodeURIComponent(String(limit))}`);
  return res.json();
}

export async function getTeamArtifactPreview(artifactId: string): Promise<Record<string, unknown> & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/artifacts/${encodeURIComponent(artifactId)}/preview`);
  return res.json();
}

export async function getTeamArtifactDownload(artifactId: string): Promise<TeamArtifactDownload> {
  const res = await fetch(`${httpBase()}/v1/team/artifacts/${encodeURIComponent(artifactId)}/download`);
  return res.json();
}

export function teamArtifactContentUrl(artifactId: string): string {
  return `${httpBase()}/v1/team/artifacts/${encodeURIComponent(artifactId)}/content`;
}

export async function transitionTeamArtifact(artifactId: string, status: "published" | "archived" | "deleted"): Promise<TeamArtifact & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/artifacts/${encodeURIComponent(artifactId)}/transition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return res.json();
}

export async function quarantineTeamArtifact(artifactId: string, reason: string): Promise<TeamArtifact & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/artifacts/${encodeURIComponent(artifactId)}/quarantine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  return res.json();
}

export async function releaseTeamArtifactQuarantine(artifactId: string): Promise<TeamArtifact & { error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/artifacts/${encodeURIComponent(artifactId)}/quarantine/release`, { method: "POST" });
  return res.json();
}

export async function exportTeamWorkspace(options: {
  include_attachments?: boolean;
  include_transcripts?: boolean;
} = {}): Promise<Blob> {
  const query = new URLSearchParams();
  if (options.include_attachments !== undefined) {
    query.set("include_attachments", options.include_attachments ? "true" : "false");
  }
  if (options.include_transcripts !== undefined) {
    query.set("include_transcripts", options.include_transcripts ? "true" : "false");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${httpBase()}/v1/team/workspace/export${suffix}`);
  if (!res.ok) {
    const errorJson = await res.json().catch(() => ({ error: "Export failed" }));
    throw new Error(errorJson.error || `Export failed with status ${res.status}`);
  }
  return res.blob();
}

export async function importTeamWorkspace(
  archive: Blob | File | ArrayBuffer,
  options: { clone?: boolean; targetWorkspaceId?: string } = {},
): Promise<{
  ok: boolean;
  workspace_id?: string;
  tables_imported?: Record<string, number>;
  total_records?: number;
  attachments_imported?: number;
  transcripts_imported?: number;
  error?: string;
}> {
  const query = new URLSearchParams();
  if (options.clone) query.set("clone", "true");
  if (options.targetWorkspaceId) query.set("target_workspace_id", options.targetWorkspaceId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${httpBase()}/v1/team/workspace/import${suffix}`, {
    method: "POST",
    headers: { "Content-Type": "application/gzip" },
    body: archive,
  });
  return res.json();
}

export async function createTeamDatabaseBackup(): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/team/database/backup`, {
    method: "POST",
  });
  return res.json();
}

export async function getJournalCases(): Promise<JournalCase[]> {
  const res = await fetch(`${httpBase()}/v1/teams/journal`);
  return (await res.json()).cases ?? [];
}

export interface ArtifactInfo {
  path: string; // workspace-relative (the display/API identifier)
  abs_path?: string; // absolute — what "Copy path" copies
  name: string;
  kind: "markdown" | "html" | "image" | "code" | "text" | string;
  size: number;
  modified_at: number;
  // Which rail surface opened it — drives the viewer's breadcrumb ("Artifacts" vs
  // "Files"). Absent = artifacts (UX-037).
  origin?: "artifacts" | "files";
}

export interface ArtifactContent {
  ok: boolean;
  error?: string;
  path: string;
  kind: string;
  content?: string;
  data_url?: string;
  truncated?: boolean;
  // kind === "folder": a directory listing (models sometimes link a whole package dir).
  entries?: { name: string; dir: boolean; size: number }[];
}

export async function getArtifacts(sessionId: string): Promise<ArtifactInfo[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts`);
  return (await res.json()).artifacts ?? [];
}

export async function readArtifact(sessionId: string, path: string): Promise<ArtifactContent> {
  const q = new URLSearchParams({ path });
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/read?${q.toString()}`);
  return res.json();
}

/** Native video playback URL. The server streams byte ranges instead of returning the file as JSON. */
export function artifactVideoUrl(sessionId: string, path: string): string {
  const q = new URLSearchParams({ path });
  const token = apiToken();
  if (token) q.set("token", token);
  return `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/video?${q.toString()}`;
}

/** Show the artifact in the OS file manager ("reveal") or open it with its default app ("open"). */
export async function revealArtifact(
  sessionId: string,
  path: string,
  mode: "reveal" | "open" = "reveal",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, mode }),
  });
  return res.json();
}

// -- session roots (orphan Cowork: scratch + added folders) -------------------
export interface RootInfo {
  path: string;
  writable: boolean;
  label: string;
  primary: boolean;
  exists: boolean;
}

export async function getRoots(sessionId: string): Promise<RootInfo[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/roots`);
  return (await res.json()).roots ?? [];
}

export async function addRoot(
  sessionId: string,
  path: string,
  writable: boolean,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/roots`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, writable }),
  });
  return res.json();
}

export async function removeRoot(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  const q = new URLSearchParams({ path });
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/roots?${q.toString()}`,
    { method: "DELETE" },
  );
  return res.json();
}

// -- MCP servers --------------------------------------------------------------
export interface McpServer {
  name: string;
  enabled: boolean;
  transport: string;
  requires_approval: boolean;
  // "connected" | "configured" | "disabled" | and for auth:"oauth" servers:
  // "needs_auth" (no tokens yet) | "authorizing" (browser sign-in in flight)
  status: string;
  auth?: "oauth" | null;
  // http server whose anonymous connect hit a 401/403 — offer OAuth sign-in.
  auth_hint?: boolean;
  // Epoch seconds of the last successful explicit Test (persisted server-side).
  last_test_at?: number | null;
  last_error?: string | null;
  tool_count: number | null;
  config: Record<string, any>;
}

export async function getMcpServers(): Promise<McpServer[]> {
  const res = await fetch(`${httpBase()}/v1/mcp`);
  return (await res.json()).servers ?? [];
}

export async function addMcpServer(name: string, config: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/mcp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, config }),
  });
  return res.json();
}

export async function patchMcpServer(name: string, changes: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function deleteMcpServer(name: string) {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
  return res.json();
}

export async function getMcpTools(
  name: string,
): Promise<{ ok: boolean; error?: string; tools: { name: string; description: string }[] }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/tools`);
  return res.json();
}

export async function reloadMcp() {
  const res = await fetch(`${httpBase()}/v1/mcp/reload`, { method: "POST" });
  return res.json();
}

/** Connect one MCP server now. For OAuth servers this opens the system browser;
 * poll getMcpServers() for the status flip (authorizing → connected / needs_auth). */
export async function connectMcp(name: string): Promise<{ ok: boolean; started?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/connect`, {
    method: "POST",
  });
  return res.json();
}

/** Drop the connection and forget the stored OAuth tokens. */
export async function signoutMcp(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/signout`, {
    method: "POST",
  });
  return res.json();
}

// -- connectors ---------------------------------------------------------------
export interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
}

// A message from a sender not (yet) on the allow-list — parked instead of dropped (§19).
export interface ParkedMessage {
  id: string;
  platform: string;
  chat_id: string;
  chat_name: string | null;
  user_id: string;
  user_name: string | null;
  chat_type: string;
  text: string;
  ts: number;
  team_id?: string | null; // workspace (managed Slack relay); null on manual Socket Mode
}

// One connected Slack workspace (managed relay is multi-workspace; ids are workspace-scoped,
// so each workspace carries its OWN allow-list).
export interface SlackWorkspace {
  team_id: string;
  account: string;
  domain?: string; // slack.com subdomain — unique even when display names collide
  allowed_users: string[];
  allow_all: boolean;
  allowed_user_names?: Record<string, string | null>;
  approval_owner_ids?: string[];
  approval_owner_names?: Record<string, string | null>;
  // Who installed this workspace (authed_user) — pre-added to the allow-list on
  // connect (UX-027); the GUI marks their chip "you" and keys the setup card copy.
  installer_user_id?: string;
  installer_name?: string;
}

// One connected GitHub App installation (managed relay is multi-installation;
// sender logins are global but each installation keeps its OWN allow-list).
export interface GithubInstallation {
  installation_id: string;
  account_login: string; // the org/user the App is installed on
  account_type: string; // "Organization" | "User"
  repo_selection: string; // "all" | "selected"
  github_login: string; // the connecting user's own login
  allowed_users: string[]; // sender logins allowed to trigger work
  allow_all: boolean;
}

// One connected HubSpot portal (multi-portal: `hubspot:portal:<hub_id>` profiles).
export interface HubSpotPortal {
  hub_id: string;
  name: string;
  sandbox: boolean;
  default: boolean;
  managed: boolean;
  access: "read" | "write" | ""; // consent tier granted ("" = manual token, unknown)
}

// One connected Google account (multi-account: `gmail:account:<email>` /
// `google_calendar:account:<email>` profiles — same shape for both).
export interface GmailAccount {
  email: string;
  default: boolean;
  managed: boolean;
  scopes: string;
  needs_reauth: boolean;
}

// "Never show agents" — enforced locally in the tool layer; agents see silent
// omissions, the user sees counts on tool cards + Activity rows.
export interface GmailFilters {
  senders: string[];
  labels: string[];
}

// One account of a generic multi-account connector (`<name>:account:<id>`
// profiles — Notion workspaces, PostHog projects, …). Gmail/Calendar predate
// the generic layer and keep their email-keyed shape above.
export interface AccountRow {
  account_id: string;
  name: string; // display identity captured at connect (workspace name, email, …)
  default: boolean;
  managed: boolean;
}

export interface Connector {
  name: string;
  title: string;
  icon: string;
  blurb: string;
  // Pre-connect detail page copy (UX-DECISIONS §38): optional About paragraph
  // (empty → group omitted) + honest Access bullets.
  about?: string;
  access?: string[];
  auth: string;
  two_way: boolean;
  // Chat-platform capability, narrower than two_way: sessions can subscribe to channels.
  channels: boolean;
  available: boolean;
  fields: ConnectorField[];
  instructions: string[];
  connected: boolean;
  account: string | null;
  enabled: boolean;
  brand_color: string; // hex brand color, e.g. "#611f69" (fallback gray "#6b7280")
  logo: string; // stable logo id keyed into the frontend registry (empty → fallback glyph)
  aliases?: string[]; // extra typeahead terms ("calendar" surfaces Outlook)
  mcp?: boolean; // MCP-backed one-click (vendor-hosted MCP + local OAuth — no cloud sign-in)
  allowed_users: string[]; // the allow-list (managed inline in the Connectors tab)
  allowed_user_names?: Record<string, string | null>; // id → display name (people directory)
  approval_owner_ids?: string[]; // Manual Slack: humans allowed to resolve approvals
  approval_owner_names?: Record<string, string | null>;
  recent?: RecentSender[]; // recently-seen senders on a connected two-way connector
  unauthorized?: ParkedMessage[]; // parked messages from unallowed senders (§19)
  tools: ConnectorTool[];
  managed: boolean; // one-click managed OAuth available (needs cloud sign-in)
  native_oauth?: boolean; // Astra-owned Google OAuth, independent of any connector broker
  managed_paused?: boolean; // one-click temporarily off (e.g. Google CASA pending) — badge "Coming soon"
  managed_profile: boolean; // current profile came from managed OAuth (vs manual paste)
  mode?: string; // "relay" for the managed cloud path; "" for manual/token connect
  workspaces?: SlackWorkspace[]; // Slack only: connected workspaces (managed relay)
  // Gmail/Calendar: email-keyed rows; generic account connectors (notion,
  // attio, posthog, …): AccountRow. The detail pages narrow by connector.
  accounts?: GmailAccount[] | AccountRow[];
  filters?: GmailFilters; // Gmail only: "Never show agents" senders/labels
  portals?: HubSpotPortal[]; // HubSpot only: connected portals (multi-portal)
  hidden_fields?: string[]; // HubSpot only: properties stripped from agent reads
  installations?: GithubInstallation[]; // GitHub only: App installations (managed relay)
}

// --- Astra Cloud (optional sign-in; manual token paste always works) ---

export interface CloudStatus {
  signed_in: boolean;
  account: string;
  user_id: string;
  telemetry_enabled?: boolean; // Phase 5 opt-out; signed-out users send nothing regardless
}

/** Flip the product-telemetry preference (local; only meaningful when signed in). */
export async function setCloudTelemetry(
  enabled: boolean,
): Promise<{ ok: boolean; telemetry_enabled?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/cloud/telemetry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

export async function getCloudStatus(): Promise<CloudStatus> {
  const res = await fetch(`${httpBase()}/v1/cloud/status`);
  return res.json();
}

export async function cloudLogin(): Promise<{ ok: boolean }> {
  // The sidecar opens the system browser; the GUI just polls status after.
  const res = await fetch(`${httpBase()}/v1/cloud/login`, { method: "POST" });
  return res.json();
}

/** Poll cloud status until the browser sign-in lands (or the bound runs out).
 *
 * Fast 500ms polls for the first 20s — the moment the user finishes in the
 * browser they're staring at the app waiting for it to flip, and a 2s interval
 * reads as "sign-in is slow" (owner complaint, 2026-07-16) — then relaxes to 2s
 * for the long tail (~2min total). Calls `onDone` with the signed-in status, or
 * null when it timed out. Returns a cancel function (call on unmount). */
export function waitForCloudSignIn(
  onDone: (s: CloudStatus | null) => void,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let polls = 0;
  const tick = async () => {
    polls += 1;
    const s = await getCloudStatus().catch(() => null);
    if (cancelled) return;
    if (s?.signed_in) return onDone(s);
    if (polls >= 90) return onDone(null); // 40×500ms + 50×2s ≈ 2min
    timer = setTimeout(tick, polls < 40 ? 500 : 2000);
  };
  timer = setTimeout(tick, 500);
  return () => {
    cancelled = true;
    if (timer) clearTimeout(timer);
  };
}

export async function cloudLogout(): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/cloud/logout`, { method: "POST" });
  return res.json();
}

export async function connectManaged(
  name: string,
  options?: { access?: "read" | "write" },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/connect-managed`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `access` names a broker-defined consent tier (hubspot read | write).
      // GitHub needs no flow choice: the broker is authorize-first — one connect
      // links an existing App installation or redirects on to the install page.
      body: JSON.stringify({
        ...(options?.access ? { access: options.access } : {}),
      }),
    },
  );
  return res.json();
}

export async function connectGoogleGmail(): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/gmail/connect-google`, {
    method: "POST",
  });
  return res.json();
}

export async function openGoogleOAuthSetup(): Promise<{
  ok: boolean;
  error?: string;
  redirect_uri?: string;
}> {
  const res = await fetch(`${httpBase()}/v1/connectors/gmail/open-google-oauth-setup`, {
    method: "POST",
  });
  return res.json();
}

/** One-click connect for an MCP-backed connector (monday, asana, jira): the sidecar
 * opens the vendor's sign-in in the browser (local OAuth, no cloud account needed);
 * poll getConnectors until the card flips to connected. */
export async function connectMcpBacked(name: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/mcp-connect`,
    { method: "POST" },
  );
  return res.json();
}

export interface ConnectorTool {
  name: string;
  label: string;
  kind: "read" | "write" | string;
  description: string;
  enabled: boolean;
  requires_approval: boolean;
}

export async function getConnectors(): Promise<Connector[]> {
  const res = await fetch(`${httpBase()}/v1/connectors`);
  return (await res.json()).connectors ?? [];
}

export async function connectConnector(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; account?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  return res.json();
}

export async function disconnectConnector(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/disconnect`, {
    method: "POST",
  });
  return res.json();
}

export async function updateConnectorTools(
  name: string,
  enabled: Record<string, boolean>,
): Promise<{ ok: boolean; error?: string; tools?: Record<string, boolean> }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/tools`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

export interface AuditEvent {
  id: number;
  timestamp: string;
  session_id: string;
  agent: string;
  workspace: string;
  connector: string;
  tool: string;
  stage: string;
  status: string;
  approval: string;
  args: Record<string, any>;
  result_preview: string;
  reason: string;
  resource: string;
}

export async function getAudit(params: {
  limit?: number;
  session_id?: string;
  connector?: string;
  tool?: string;
} = {}): Promise<AuditEvent[]> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.session_id) q.set("session_id", params.session_id);
  if (params.connector) q.set("connector", params.connector);
  if (params.tool) q.set("tool", params.tool);
  const res = await fetch(`${httpBase()}/v1/audit${q.toString() ? "?" + q.toString() : ""}`);
  return (await res.json()).events ?? [];
}

export interface BrowserState {
  open: boolean;
  url: string;
  title: string;
  status: string;
  last_action: string;
  last_result: string;
  last_error: string;
  screenshot_data_url: string;
  updated_at: string | null;
  controls: any[];
}

export async function getBrowserState(): Promise<BrowserState> {
  const res = await fetch(`${httpBase()}/v1/browser/state`);
  return res.json();
}

export async function takeBrowserScreenshot(): Promise<BrowserState & { ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/screenshot`, { method: "POST" });
  return res.json();
}

export async function closeBrowser(): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/close`, { method: "POST" });
  return res.json();
}

// -- settings (model API key, default model, onboarding) ----------------------
export async function searchWebInApp(query: string): Promise<{ ok: boolean; results: Array<{ title: string; url: string; snippet: string }> }> {
  const res = await fetch(`${httpBase()}/v1/web-search/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  return res.json();
}
export interface SurfaceVisibility {
  cowork: boolean; // always true
  chat: boolean;
  code: boolean;
}

export interface ModelSettings {
  provider: string;
  model: string;
  models: string[];
  has_key: boolean;
  model_ready: boolean; // can the default model's provider actually run (any provider)?
  source: "env" | "store" | null;
  onboarded: boolean;
  surfaces: SurfaceVisibility;
  scratch_base: string;
  secrets_path: string;  // OS-native on-disk location the server reports (not hardcoded)
  // Sidebar layout preference (§7): "flat" = the persona accordions / today's list; "grouped" =
  // bounded per-persona cards. Defaults to "flat" (absent → flat) so the GUI is robust to an older
  // backend that hasn't shipped the field yet.
  nav_layout?: "flat" | "grouped";
  // Sidebar: sessions shown per group before "Show more" (default 5, 1–50).
  sessions_peek?: number;
  // Composer: show the context-window fill bar (default FALSE; absent → the chip shows
  // the session total). The usage popover keeps both numbers regardless.
  context_bar?: boolean;
  // Auto-Approve mode (spec §1.5): the feature flag that offers the reviewer mode, and its
  // shadow-eval sibling. Both default FALSE and are absent on older backends — the composer
  // hides the Auto-Approve mode entry unless auto_approve is explicitly true.
  auto_approve?: boolean;
  auto_approve_shadow?: boolean;
  // Curated-matrix display names ({full id → "GLM-5.2 · via Together"}); custom models absent.
  model_labels?: Record<string, string>;
  // {full id → context window in tokens}, verified matrix entries only — drives the
  // composer's context-fill meter (absent id → the meter hides). Optional for older backends.
  model_context_windows?: Record<string, number>;
  // Token savings (PDF attachments): fallback for models without native PDF support,
  // and attach-time thresholds. Optional so the GUI is robust to an older backend.
  pdf_fallback?: "text" | "images";
  pdf_max_pages?: number; // default 20, 1–100
  pdf_max_mb?: number; // default 10, 1–10
  // Auto-compaction of long histories (OPE-27): trigger = min(threshold% × context
  // window, cap tokens); model pins the summarizer ("" → the session's own model).
  // Optional so the GUI is robust to an older backend.
  compaction_threshold_pct?: number; // default 0.8, 0.10–0.95
  compaction_cap_tokens?: number; // default 250000
  compaction_model?: string;
}

export interface PdfSettings {
  pdf_fallback: "text" | "images";
  pdf_max_pages: number;
  pdf_max_mb: number;
}

/** Persist the Token-savings PDF settings (fallback mode + attach thresholds). */
export async function setPdfSettings(
  patch: Partial<PdfSettings>,
): Promise<{ ok: boolean; error?: string } & Partial<PdfSettings>> {
  const res = await fetch(`${httpBase()}/v1/settings/pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

export interface CompactionSettings {
  compaction_threshold_pct: number;
  compaction_cap_tokens: number;
  compaction_model: string;
}

/** Persist the auto-compaction overrides (threshold %, token cap, summarizer model). */
export async function setCompactionSettings(
  patch: Partial<CompactionSettings>,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/compaction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

/** Local page/size probe for a PDF data URL — the composer's attach-time threshold check. */
export async function inspectPdf(
  dataUrl: string,
): Promise<{ ok: boolean; pages?: number; bytes?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/attachments/inspect-pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_url: dataUrl }),
  });
  return res.json();
}

/** Persist whether the composer shows the context-window fill bar. */
export async function setContextBar(
  shown: boolean,
): Promise<{ ok: boolean; context_bar?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/context-bar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context_bar: shown }),
  });
  return res.json();
}

type AutoApproveResult = {
  ok: boolean;
  auto_approve?: boolean;
  auto_approve_shadow?: boolean;
  error?: string;
};

/** Toggle the Auto-Approve feature flag (spec §1.5); applies to the next session build. */
export async function setAutoApprove(on: boolean): Promise<AutoApproveResult> {
  const res = await fetch(`${httpBase()}/v1/settings/auto-approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_approve: on }),
  });
  return res.json();
}

/** Toggle shadow evaluation (Part 6 step 3): the reviewer records but never decides. */
export async function setAutoApproveShadow(on: boolean): Promise<AutoApproveResult> {
  const res = await fetch(`${httpBase()}/v1/settings/auto-approve-shadow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_approve_shadow: on }),
  });
  return res.json();
}

/** Persist how many sessions a sidebar group shows before "Show more". */
export async function setSessionsPeek(
  n: number,
): Promise<{ ok: boolean; sessions_peek?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/sessions-peek`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessions_peek: n }),
  });
  return res.json();
}

export async function setScratchBase(
  path: string,
): Promise<{ ok: boolean; error?: string; scratch_base?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/scratch-base`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return res.json();
}

export async function setSurfaces(
  flags: { chat?: boolean; code?: boolean },
): Promise<{ ok: boolean; surfaces: SurfaceVisibility }> {
  const res = await fetch(`${httpBase()}/v1/settings/surfaces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(flags),
  });
  return res.json();
}

/** Persist the sidebar layout preference (flat ↔ grouped-by-persona); read back from getSettings. */
export async function setNavLayout(
  layout: "flat" | "grouped",
): Promise<{ ok: boolean; nav_layout?: "flat" | "grouped"; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/nav-layout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nav_layout: layout }),
  });
  return res.json();
}

// Fired after a cloud sign-in/out completes so the account row (§26) refreshes without
// waiting for the next window focus.
export const CLOUD_CHANGED = "coworker:cloud-changed";
export function announceCloudChanged() {
  window.dispatchEvent(new CustomEvent(CLOUD_CHANGED));
}

// Fired the first time Inbox machinery is engaged (an item parks, or a session goes
// Unattended) — the account row's inbox chip unlocks stickily on it (§26).
export const INBOX_UNLOCK = "coworker:inbox-unlock";
export function announceInboxUnlock() {
  window.dispatchEvent(new CustomEvent(INBOX_UNLOCK));
}

// -- Personas -----------------------------------------------------------------

// Fired after any persona mutation (enable/disable/install/delete) so always-mounted
// consumers (the sidebar's new-session picker) refetch instead of going stale.
export const PERSONAS_CHANGED = "coworker:personas-changed";
function announcePersonasChanged() {
  window.dispatchEvent(new CustomEvent(PERSONAS_CHANGED));
}

export interface Persona {
  id: string;
  name: string;
  icon: string;
  tagline: string;
  requires_folder: boolean; // folder gate — drives project-scoping
  builtin: boolean;
  tools: string[];
  enabled: boolean;
  surfaced: boolean;
  default: boolean;
  // Distribution flag (ships:false = internal builds only) + settings-page group.
  ships?: boolean;
  group?: string; // "general" | "security"
  version?: string;
  installed_at?: string;
}

export interface PersonaConsent {
  id: string;
  name: string;
  description: string;
  tools: string[];
  risk: string[];
  // "all" (general builtins) or the declared allowlist — [] means no connector access.
  connectors: "all" | string[];
  mcp: string[];
  messaging: boolean;
  recommended_mode: string;
  recommended_models: string[];
  recommends?: { kind: string; ref: string; reason: string; tier: string }[];
  version?: string;
  replaces?: { version: string; installed_at: string; capabilities_grew: boolean } | null;
  source: string | null;
  builtin: boolean;
}

export async function getPersonas(): Promise<Persona[]> {
  const res = await fetch(`${httpBase()}/v1/personas`);
  return (await res.json()).personas;
}

/** Personas plus the build flag: `internal` builds may show unshipped coworkers + the Gallery. */
export async function getPersonasIndex(): Promise<{ personas: Persona[]; internal: boolean }> {
  const res = await fetch(`${httpBase()}/v1/personas`);
  const body = await res.json();
  return { personas: body.personas ?? [], internal: !!body.internal };
}

export async function updatePersona(
  id: string,
  body: { enabled?: boolean; surfaced?: boolean; default?: boolean },
): Promise<{ ok: boolean; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await res.json();
  if (out.ok !== false) announcePersonasChanged();
  return out;
}

/** Uninstall a non-builtin persona (its snapshot + state). Local; works signed out. */
export async function deletePersona(
  id: string,
): Promise<{ ok: boolean; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  const out = await res.json();
  if (out.ok) announcePersonasChanged();
  return out;
}

// A curated persona card from the cloud gallery (metadata only — the manifest
// is fetched server-side at install and runs through the normal consent flow).
export interface GalleryPersona {
  slug: string;
  version: number;
  name: string;
  icon: string;
  tagline: string;
  description: string;
  family: string;
  workspace: string;
  publisher: string;
  recommended_connectors: string[];
  risk_summary: string;
  featured?: boolean; // publisher-flagged for the gallery's featured carousel
}

export async function getCloudGallery(): Promise<{
  ok: boolean;
  personas: GalleryPersona[];
  error?: string;
}> {
  const res = await fetch(`${httpBase()}/v1/cloud/gallery`);
  return res.json();
}

// Solo page for one gallery coworker. `capabilities` is the desktop's own
// consent summary derived from the manifest (same parser as install), so the
// page shows exactly what installing would ask the user to approve.
export interface GalleryDetail {
  ok: boolean;
  error?: string;
  card?: GalleryPersona & { pitch_markdown: string };
  capabilities?: {
    tools: string[];
    risk: string[];
    connectors: boolean;
    mcp: string[];
    messaging: boolean;
    recommended_mode: string;
    recommended_models: string[];
  };
  recommends?: { kind: string; ref: string; reason: string; tier: string }[];
}

export async function getCloudGalleryDetail(slug: string): Promise<GalleryDetail> {
  const res = await fetch(`${httpBase()}/v1/cloud/gallery/${encodeURIComponent(slug)}`);
  return res.json();
}

/** Sharing v1 (OPE-7): zip a coworker's bundle into `dir`; the zip is the import format. */
export async function exportPersona(
  id: string,
  dir: string,
): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir }),
  });
  return res.json();
}

export async function installPersona(
  body: { dir?: string; git_url?: string; gallery_slug?: string; zip_b64?: string; filename?: string },
): Promise<{ ok: boolean; consent?: PersonaConsent[]; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const out = await res.json();
  if (out.ok) announcePersonasChanged();
  return out;
}

// -- Persona detail + connection defaults (§5) --------------------------------
// A persona's declared recommendation (manifest `recommends`): a connector or MCP server it works
// best with, with a reason + tier (core/optional). `connected` is annotated server-side from the
// connector list so the detail page can show connect state without a second round-trip.
export interface PersonaRecommendation {
  kind: string; // "connector" | "mcp" | …
  ref: string; // connector id (e.g. "github") or mcp/server name
  reason: string;
  tier: string; // "core" | "optional"
  connected: boolean;
}

// A persona-default connection (the middle of the §4 hierarchy): for a connected connector, whether
// new sessions of this persona get it enabled by default.
export interface PersonaDefaultConnection {
  connector: string; // connector id
  enabled: boolean; // persona-default on/off
  connected: boolean; // is the account actually connected (else the toggle is disabled)
}

export interface PersonaDetail {
  id: string;
  name: string;
  icon: string;
  tagline: string;
  description: string;
  media: string[]; // bundle media/ screenshots, served via /v1/personas/{id}/media/{name}
  builtin: boolean;
  group: string;
  enabled: boolean; // persona on/off (shown in the picker)
  surfaced: boolean;
  default: boolean;
  tools: string[];
  recommended_models: string[];
  default_permission_mode: string;
  requires_folder: boolean; // folder gate (workspace-scratch-design.md)
  recommends: PersonaRecommendation[];
  default_connections: PersonaDefaultConnection[];
}

/** Fetch one bundle screenshot with launch auth and hand back an object URL. */
export async function getPersonaMediaUrl(id: string, name: string): Promise<string> {
  const res = await fetch(
    `${httpBase()}/v1/personas/${encodeURIComponent(id)}/media/${encodeURIComponent(name)}`,
  );
  if (!res.ok) throw new Error(`media ${name}: ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

export async function getPersonaDetail(id: string): Promise<PersonaDetail> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}`);
  return res.json();
}

/** Set a persona-default connection (new sessions of this persona get it on/off by default). */
export async function setPersonaConnection(
  id: string,
  connector: string,
  enabled: boolean,
): Promise<{ ok: boolean; default_connections?: PersonaDefaultConnection[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector, enabled }),
  });
  return res.json();
}

/** Enable/disable the persona (whether it surfaces in the new-session picker). */
export async function setPersonaEnabled(
  id: string,
  enabled: boolean,
): Promise<{ ok: boolean; personas?: Persona[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/personas/${encodeURIComponent(id)}/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const out = await res.json();
  if (out.ok) announcePersonasChanged();
  return out;
}

// -- Per-session connections (Sources bar + drawer, §6) -----------------------
// An effective-enabled connector for a session, with a short human detail (e.g. "#ocw-test · DMs").
// `enabled` reflects the session override/persona default so the drawer toggle shows correct state.
export interface SessionConnectedConnector {
  connector: string;
  enabled: boolean;
  detail: string;
}

// A persona-recommended connector not yet connected (drives the `⚠ N` attention count).
export interface SessionRecommendedConnector {
  connector: string;
  reason: string;
  tier: string;
  connected: boolean;
}

export interface SessionConnections {
  connected: SessionConnectedConnector[];
  recommended: SessionRecommendedConnector[];
  attention: number; // ⚠ count = recommended connectors not yet connected
}

/** `persona` = the active persona hint — required for brand-new sessions (no server-side
 * record yet), otherwise the view resolves to the default persona's defaults/recommends. */
export async function getSessionConnections(
  sessionId: string,
  persona?: string,
): Promise<SessionConnections> {
  const q = persona ? `?persona=${encodeURIComponent(persona)}` : "";
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/connections${q}`,
  );
  return res.json();
}

/**
 * Set a per-session connection override (mute/unmute a connector for THIS session). Pass
 * `clear: true` to drop the override and inherit the persona default again.
 */
export async function setSessionConnection(
  sessionId: string,
  connector: string,
  enabled: boolean,
  clear = false,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector, enabled, ...(clear ? { clear: true } : {}) }),
  });
  return res.json();
}

// -- Skills (SKILLS-SPEC §4) ----------------------------------------------------
// Scope = folder location: "global" (every session) or "project" (one workspace).
// The session endpoints resolve the effective menu (Settings disables + session mutes).

export interface SkillRow {
  name: string;
  description: string;
  instructions: string;
  scope: "global" | "project";
  source: string; // "local" | "uploaded" | "detected"
  enabled: boolean;
  path: string;
  files?: number; // bundled resources beyond SKILL.md (§6 — rich skills are visible)
}

export interface SessionSkillRow {
  name: string;
  description: string;
  scope: "global" | "project";
  enabled: boolean; // false = muted for this session only
}

export interface SkillUploadPreview {
  ok: boolean;
  error?: string;
  token?: string;
  name?: string;
  description?: string;
  instructions?: string;
  files?: string[];
}

const skillUrl = (path = "") => `${httpBase()}/v1/skills${path}`;
const jsonPost = (body: unknown, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function listSkills(workspace?: string): Promise<SkillRow[]> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(skillUrl(qs));
  return (await res.json()).skills ?? [];
}

export async function createSkill(body: {
  name: string;
  description: string;
  instructions: string;
  scope?: "global" | "project";
  workspace?: string;
}): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(), jsonPost(body));
  return res.json();
}

export async function updateSkill(
  name: string,
  patch: { description?: string; instructions?: string; enabled?: boolean; workspace?: string },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}`), jsonPost(patch, "PATCH"));
  return res.json();
}

export async function revealSkill(name: string): Promise<{ ok: boolean; error?: string }> {
  // §6 "Show folder": the backend opens the skill's folder in the OS file manager.
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}/reveal`), jsonPost({}));
  return res.json();
}

export async function deleteSkill(
  name: string,
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}${qs}`), { method: "DELETE" });
  return res.json();
}

export async function moveSkill(
  name: string,
  scope: "global" | "project",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}/move`), jsonPost({ scope, workspace }));
  return res.json();
}

export async function stageSkillUpload(
  dataB64: string,
  filename = "",
): Promise<SkillUploadPreview> {
  const res = await fetch(skillUrl("/upload"), jsonPost({ data_b64: dataB64, filename }));
  return res.json();
}

export async function confirmSkillUpload(
  token: string,
  scope: "global" | "project" = "global",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl("/upload/confirm"), jsonPost({ token, scope, workspace }));
  return res.json();
}


export async function sessionSkills(
  sessionId: string,
  workspace?: string,
): Promise<SessionSkillRow[]> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/skills${qs}`,
  );
  return (await res.json()).skills ?? [];
}

export async function setSessionSkill(
  sessionId: string,
  skill: string,
  enabled: boolean,
  opts: { clear?: boolean; workspace?: string } = {},
): Promise<{ skills?: SessionSkillRow[]; ok?: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/skills`,
    jsonPost({
      skill,
      enabled,
      ...(opts.clear ? { clear: true } : {}),
      ...(opts.workspace ? { workspace: opts.workspace } : {}),
    }),
  );
  return res.json();
}

// -- Inbox + Unattended -------------------------------------------------------
export interface InboxItem {
  id: string;
  session_id: string;
  kind: "approval" | "question" | "notification" | "directory" | "plan";
  title: string;
  body: string;
  state: "pending" | "resolved";
  resolution: string | null;
  inbox: string;
  created_at: string;
  resolved_at: string | null;
  visibility?: "inline" | "inbox";
  // Question metadata (ask_user): quick-reply choices + a free-text escape. Options may be rich
  // {label, description, recommended, preview} objects (OPE-51); `questions` is the grouped form
  // (stepper), whose resolution is a JSON object string keyed by header-or-question.
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
  header?: string;
  questions?: GroupedQuestion[];
  // Kind-specific payload (directory: {path, writable}; …).
  data?: Record<string, any>;
  // Originating-session context (server-joined) so the Inbox is self-contained.
  session_title?: string;
  session_agent?: string | null;
  session_workspace?: string | null;
  session_exists?: boolean;
}

export async function getInbox(sessionId?: string, state?: string): Promise<InboxItem[]> {
  const q = new URLSearchParams();
  if (sessionId) q.set("session_id", sessionId);
  if (state) q.set("state", state);
  const res = await fetch(`${httpBase()}/v1/inbox?${q.toString()}`);
  return (await res.json()).items ?? [];
}

export async function resolveInboxItem(
  id: string,
  resolution: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/inbox/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution }),
  });
  return res.json();
}

// -- channel subscriptions (view-only) ----------------------------------------
export interface Subscription {
  session_id: string;
  session_title: string;
  agent: string;
  channel: string;
  channel_name?: string | null; // resolved display name ("ocw-test"); address stays the id
  routing_target: string | null;
  collision: boolean; // inbound subscription == outbound Inbox routing on the same channel
}

export interface RecentChannel {
  channel: string;
  name?: string | null; // resolved display name, e.g. "ocw-test" (falls back to the address)
  last_from: string | null;
  last_text: string | null;
}

export async function getSubscriptions(): Promise<Subscription[]> {
  const res = await fetch(`${httpBase()}/v1/subscriptions`);
  return (await res.json()).subscriptions ?? [];
}

// -- inbox routing (where Unattended approvals/questions get mirrored) ---------
export interface InboxBinding {
  name: string;
  channel: string | null; // platform, e.g. "slack" (null = in-app Inbox only)
  target: string; // chat_id, e.g. "C0BEJNCQQ8Y"
}

export async function getInboxRouting(): Promise<InboxBinding[]> {
  const res = await fetch(`${httpBase()}/v1/inbox/routing`);
  return (await res.json()).bindings ?? [];
}

export async function setInboxBinding(
  name: string,
  channel: string | null,
  target: string,
): Promise<{ ok: boolean; bindings?: InboxBinding[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/inbox/routing/binding`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, channel, target }),
  });
  return res.json();
}

export interface UnroutedItem {
  source: string;
  sender: string;
  text: string;
  reason: string;
  ts: number;
}

export async function getUnrouted(): Promise<UnroutedItem[]> {
  const res = await fetch(`${httpBase()}/v1/unrouted`);
  return (await res.json()).items ?? [];
}

export async function getRecentChannels(): Promise<RecentChannel[]> {
  const res = await fetch(`${httpBase()}/v1/channels/recent`);
  return (await res.json()).channels ?? [];
}

export async function subscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; channel?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, channel }),
  });
  return res.json();
}

export async function unsubscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; removed?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/subscriptions/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, channel }),
  });
  return res.json();
}

export async function getUnattended(sessionId: string): Promise<boolean> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/unattended`,
  );
  return (await res.json()).unattended;
}

export async function setUnattended(
  sessionId: string,
  unattended: boolean,
): Promise<{ ok: boolean; unattended: boolean }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/unattended`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unattended }),
    },
  );
  return res.json();
}

// Auto-Approve metering (§1.7): per-session reviewer counts from the durable audit rows.
export interface ReviewerBucket {
  checks: number;
  allow: number;
  deny: number;
  unsure: number;
  tokens_in: number;
  tokens_out: number;
  // Cached-prefix share of the input, billed at ~10%. Without it the badge only ever
  // showed the FRESH tokens — a fraction of what a check really processes.
  cache_read: number;
  cache_write: number;
}
export interface ReviewerStats {
  live: ReviewerBucket;   // the mode actually deciding (Mode.AUTO_APPROVE)
  shadow: ReviewerBucket; // shadow evaluation: recorded next to the human's own decisions
}

export async function getReviewerStats(sessionId: string): Promise<ReviewerStats> {
  const res = await fetch(`${httpBase()}/v1/sessions/${sessionId}/reviewer-stats`);
  return res.json();
}

export async function getSettings(): Promise<ModelSettings> {
  const res = await fetch(`${httpBase()}/v1/settings`);
  return res.json();
}

export interface WebSearchSettings {
  provider: string;
  has_key: boolean;
  providers: string[];
}

export async function getWebSearch(): Promise<WebSearchSettings> {
  const res = await fetch(`${httpBase()}/v1/web-search`);
  return res.json();
}

export async function setWebSearch(
  provider: string,
  apiKey?: string,
): Promise<{ ok: boolean; provider?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/web-search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  return res.json();
}

export async function setModelKey(
  apiKey: string,
): Promise<{ ok: boolean; error?: string; has_key?: boolean; source?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/model-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  return res.json();
}

export async function setDefaultModel(
  model: string,
): Promise<{ ok: boolean; error?: string; model?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/default-model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return res.json();
}

export async function addModel(model: string): Promise<ModelSettings & { ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/models/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return res.json();
}

export async function removeModel(model: string): Promise<ModelSettings & { ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/models/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return res.json();
}

export async function setOnboarded(value: boolean): Promise<{ ok: boolean; onboarded: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/onboarded`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  return res.json();
}

// -- Memory (MEMORY-SPEC §5.3/§6: the memory screen, user rules, toast Undo) ----

export interface MemoryEntry {
  id: number;
  scope: string;
  content: string;
  summary: string;
  created_at: string;
}

export interface MemorySettings {
  enabled: boolean;
  user_rules: string;
}

// Fired whenever memory changes from OUTSIDE the memory screen — today the agent
// saving or editing one mid-conversation. The screen only loads its list on mount, so
// without this it sits there stale and the user reads "Nothing yet" seconds after a
// save actually landed (owner-hit 2026-07-28).
export const MEMORY_CHANGED = "coworker:memory-changed";
export function announceMemoryChanged() {
  window.dispatchEvent(new CustomEvent(MEMORY_CHANGED));
}

export async function getMemory(): Promise<MemoryEntry[]> {
  const res = await fetch(`${httpBase()}/v1/memory`);
  return (await res.json()).memory ?? [];
}

export async function updateMemory(
  id: number,
  content: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return res.json();
}

export async function deleteMemory(id: number): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/${id}`, { method: "DELETE" });
  return res.json();
}

export async function deleteAllMemory(): Promise<{ ok: boolean; deleted: number }> {
  const res = await fetch(`${httpBase()}/v1/memory`, { method: "DELETE" });
  return res.json();
}

export async function getMemorySettings(): Promise<MemorySettings> {
  const res = await fetch(`${httpBase()}/v1/memory/settings`);
  return res.json();
}

export async function setMemorySettings(
  patch: Partial<MemorySettings>,
): Promise<MemorySettings> {
  const res = await fetch(`${httpBase()}/v1/memory/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

// -- model providers (OpenAI, Ollama, …) --------------------------------------
export interface ProviderField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
  default?: string; // pre-filled editable value (e.g. an OpenAI-compatible vendor's endpoint)
  // Non-empty → segmented choice, not a text input. tag = tiny badge ("Easiest");
  // desc = one-liner atop the method panel; command = copyable terminal command.
  choices?: { value: string; label: string; tag?: string; desc?: string; command?: string }[];
  show_when?: Record<string, string> | null; // render only while these fields hold these values
}

export interface ProviderInfo {
  name: string;
  title: string;
  needs_key: boolean;
  fields: ProviderField[];
  configured: boolean;
  values: Record<string, string>; // non-secret stored values (e.g. base_url), for prefilling
  suggested_models: string[]; // bare model-name suggestions for the "add model" datalist
  recommended_model: string | null; // pre-filled default for this provider (e.g. qwen3-coder:30b)
  blurb?: string; // one-line note under the title ("Uses X's OpenAI-compatible API…")
  key_set_at?: string | null; // ISO date the key was last (re)saved — absent for env-only config
  last_used_at?: number | null; // epoch secs the provider last served a completion
  // OAuth providers (auth === "oauth"): browser sign-in instead of a key form.
  auth?: string | null;
  signed_in?: boolean;
  account?: string | null; // signed-in account label (email or id)
  authorizing?: boolean;
  last_error?: string | null;
}

// -- ChatGPT-subscription provider sign-in (OAuth; tokens never reach the GUI) ------
export interface CodexAuthStatus {
  signed_in: boolean;
  account?: string | null;
  authorizing: boolean;
  last_error?: string | null;
  authorize_url?: string | null;
}

export async function codexSignin(): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/providers/openai-codex/signin`, { method: "POST" });
  return res.json();
}

export async function codexAuthStatus(): Promise<CodexAuthStatus> {
  const res = await fetch(`${httpBase()}/v1/providers/openai-codex/status`);
  return res.json();
}

export async function codexSignout(): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/providers/openai-codex/signout`, { method: "POST" });
  return res.json();
}

export async function getProviders(): Promise<ProviderInfo[]> {
  const res = await fetch(`${httpBase()}/v1/providers`);
  return res.json();
}

export async function setProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string; provider?: string; recommended_model?: string | null }> {
  const res = await fetch(`${httpBase()}/v1/providers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, fields }),
  });
  return res.json();
}

/** Forget a provider's stored config (Settings ▸ Models "Remove key…"). */
export async function removeProvider(name: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  return res.json();
}

/** Live read-only credential check (does NOT save the key). Triggered by the user's "Test" click. */
export async function verifyProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, fields }),
  });
  return res.json();
}

/** Client-side provider guess from an API key's shape (mirrors the server's detect_provider). */
export function detectProvider(apiKey: string): string | null {
  const key = (apiKey || "").trim();
  if (!key) return null;
  if (key.startsWith("sk-ant-")) return "anthropic";
  if (key.startsWith("sk-or-")) return "openrouter";
  if (key.startsWith("AIza")) return "gemini";
  if (key.startsWith("sk-") || key.startsWith("sk_")) return "openai";
  return null;
}

// -- super-agent --------------------------------------------------------------
export interface RecentSender {
  user_id: string;
  user_name: string | null;
  chat_id: string;
  chat_type: string;
  target: string;
  authorized: boolean;
  team_id?: string | null; // workspace (managed relay); null on manual Socket Mode
}

// -- direct-message routing ---------------------------------------------------
export async function getDmRoute(): Promise<string | null> {
  const res = await fetch(`${httpBase()}/v1/messaging/dm-route`);
  return (await res.json()).dm_session ?? null;
}

export async function setDmRoute(sessionId: string): Promise<{ ok: boolean; dm_session: string | null }> {
  const res = await fetch(`${httpBase()}/v1/messaging/dm-route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return res.json();
}

// -- automations (scheduled tasks) --------------------------------------------
export interface Automation {
  id: string;
  title: string;
  instructions: string;
  schedule: string;
  schedule_raw?: { kind: string; cron?: string | null; fire_at?: string | null; timezone?: string };
  workspace: string;
  agent: string;
  enabled: boolean;
  next_run: number | null;
  last_run: number | null;
  last_status: string | null;
  run_count: number;
  notify_on_completion: boolean;
  // UX-023 sidebar badges: runs started since the user last opened this automation's
  // detail; `unseen_failed` = the newest unseen run errored (danger tint).
  unseen_runs?: number;
  unseen_failed?: boolean;
  seen_runs_at?: number;
  // Standing scoped approvals (§25): target-bound rules this automation may exercise
  // without asking. `entry` is the raw record entry — the revoke handle; `target` is
  // null for legacy name-only entries.
  always_allowed: { entry: string; tool: string; target: string | null }[];
}

export interface AutomationRun {
  run_id: string;
  task_id: string;
  session_id: string;
  started_at: number;
  finished_at: number | null;
  status: string;
  result_text: string | null;
  artifacts: string[];
  error: string | null;
  trigger: string;
}

export async function getAutomations(): Promise<Automation[]> {
  const res = await fetch(`${httpBase()}/v1/automations`);
  return (await res.json()).tasks ?? [];
}

// Fired after any automation mutation the sidebar should reflect immediately
// (mark-seen, create, delete) — its poll covers the rest.
export const AUTOMATIONS_CHANGED = "coworker:automations-changed";
export function announceAutomationsChanged() {
  window.dispatchEvent(new CustomEvent(AUTOMATIONS_CHANGED));
}

/** App-wide event stream (/ws/events): session-independent server pushes — today
 * automation_run_started (the UX-026 toast). Quietly reconnects while the app is
 * open; the returned cleanup stops it for good. */
export function connectEvents(
  onEvent: (msg: { type: string; data?: Record<string, unknown> }) => void
): () => void {
  let ws: WebSocket | null = null;
  let timer: number | null = null;
  let closed = false;
  const open = () => {
    if (closed) return;
    ws = openWebSocket(`${wsBase()}/ws/events`);
    ws.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    ws.onclose = () => {
      if (!closed) timer = window.setTimeout(open, 5000);
    };
  };
  open();
  return () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    ws?.close();
  };
}

export interface TeamDomainEvent {
  sequence: number;
  event_id: string;
  event_type: string;
  workspace_id: string;
  actor_id: string;
  aggregate_type: string;
  aggregate_id: string;
  correlation_id: string;
  payload?: Record<string, unknown>;
  occurred_at: string;
}

export interface TeamActivity extends TeamDomainEvent {
  activity_id: string;
  activity_kind: string;
}

export interface TeamCommandEnvelope {
  command_id: string;
  command_type: string;
  workspace_id: string;
  requester: { id: string; kind: "human" | "agent" | "system"; display_name?: string };
  idempotency_key: string;
  correlation_id: string;
  payload: Record<string, unknown>;
  expected_version?: number | null;
  causation_id?: string | null;
  requested_at?: string;
}

export interface TeamQueryEnvelope {
  query_id: string;
  query_type: string;
  workspace_id: string;
  requester: { id: string; kind: "human" | "agent" | "system"; display_name?: string };
  correlation_id: string;
  filters: Record<string, unknown>;
  cursor?: string | null;
  limit?: number;
  requested_at?: string;
}

export interface TeamProjectionState {
  cursor: number;
  aggregates: Record<string, Record<string, TeamDomainEvent>>;
}

/** Pure event reducer used as the GUI's duplicate-safe live projection index. */
export function reduceTeamEvent(
  state: TeamProjectionState,
  event: TeamDomainEvent,
): TeamProjectionState {
  if (!event || !Number.isFinite(event.sequence) || event.sequence <= state.cursor) return state;
  const kind = event.aggregate_type || "unknown";
  const id = event.aggregate_id || event.event_id;
  const current = state.aggregates[kind] || {};
  return {
    cursor: event.sequence,
    aggregates: {
      ...state.aggregates,
      [kind]: { ...current, [id]: event },
    },
  };
}

export async function sendTeamCommand<T = unknown>(
  envelope: TeamCommandEnvelope,
): Promise<{ command: TeamCommandEnvelope; result?: T; error?: string; api_error?: Record<string, unknown> }> {
  const res = await fetch(`${httpBase()}/v1/team/commands`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(envelope),
  });
  return res.json();
}

export async function queryTeam<T = unknown>(
  envelope: TeamQueryEnvelope,
): Promise<{ query: TeamQueryEnvelope; result?: T; error?: string; api_error?: Record<string, unknown> }> {
  const params = new URLSearchParams({
    query_type: envelope.query_type,
    query_id: envelope.query_id,
    correlation_id: envelope.correlation_id,
    filters: JSON.stringify(envelope.filters || {}),
    limit: String(envelope.limit || 50),
  });
  if (envelope.cursor) params.set("cursor", envelope.cursor);
  const res = await fetch(`${httpBase()}/v1/team/queries?${params.toString()}`);
  return res.json();
}

export async function getTeamObservability<T = unknown>(options: {
  runId?: string;
  limit?: number;
} = {}): Promise<T & { error?: string; api_error?: Record<string, unknown> }> {
  const params = new URLSearchParams();
  if (options.runId) params.set("run_id", options.runId);
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  const res = await fetch(`${httpBase()}/v1/team/observability${query ? `?${query}` : ""}`);
  return res.json();
}

export async function getTeamActivity(options: {
  afterSequence?: number;
  limit?: number;
} = {}): Promise<{ activities: TeamActivity[]; next_sequence: number; has_more: boolean }> {
  const params = new URLSearchParams();
  if (options.afterSequence) params.set("after_sequence", String(options.afterSequence));
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  const res = await fetch(`${httpBase()}/v1/team/activity${query ? `?${query}` : ""}`);
  return res.json();
}

/** Durable Team stream with sequence-based reconnect/catch-up semantics. */
export function connectTeamEvents(
  afterSequence: number,
  onEvent: (event: TeamDomainEvent, replayed?: boolean) => void,
): () => void {
  let ws: WebSocket | null = null;
  let timer: number | null = null;
  let cursor = Math.max(0, afterSequence);
  try {
    const persisted = Number(sessionStorage.getItem("astra.team.events.cursor") || 0);
    if (Number.isFinite(persisted)) cursor = Math.max(cursor, persisted);
  } catch {
    /* storage can be unavailable in private/webview contexts */
  }
  let closed = false;
  const persistCursor = () => {
    try {
      sessionStorage.setItem("astra.team.events.cursor", String(cursor));
    } catch {
      /* cursor remains correct for this connection */
    }
  };
  const open = () => {
    if (closed) return;
    ws = openWebSocket(`${wsBase()}/ws/team?after_sequence=${encodeURIComponent(String(cursor))}`);
    ws.onmessage = (e) => {
      try {
        const message = JSON.parse(e.data) as {
          type?: string;
          data?: { events?: TeamDomainEvent[]; next_sequence?: number } | TeamDomainEvent;
        };
        if (message.type === "team_catchup") {
          const page = message.data as { events?: TeamDomainEvent[]; next_sequence?: number };
          for (const event of page.events || []) {
            cursor = Math.max(cursor, Number(event.sequence) || 0);
            onEvent(event, true);
            persistCursor();
          }
          cursor = Math.max(cursor, Number(page.next_sequence) || 0);
          persistCursor();
        } else if (message.type === "team_event") {
          const event = message.data as TeamDomainEvent;
          if (!event || typeof event.sequence !== "number") return;
          if (event.sequence <= cursor) return;
          cursor = event.sequence;
          onEvent(event, false);
          persistCursor();
        }
      } catch {
        /* malformed frame — reconnect/catch-up remains safe */
      }
    };
    ws.onclose = () => {
      if (!closed) timer = window.setTimeout(open, 1000);
    };
  };
  open();
  return () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    ws?.close();
  };
}

/** Advance the automation's seen mark — clears its unseen-runs badge (UX-023). */
export async function markAutomationSeen(id: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/automations/${id}/seen`, { method: "POST" });
  return res.json();
}

export async function createAutomation(payload: {
  title: string;
  instructions: string;
  cron?: string;
  fire_at?: string;
  timezone?: string;
  // §25 standing grants (the creating surface rendered them; submit IS the consent).
  // Only target-bound write entries survive server-side validation.
  permissions?: { tool: string; target: string; access: "read" | "write" }[];
}): Promise<{ ok: boolean; error?: string; task?: Automation }> {
  const res = await fetch(`${httpBase()}/v1/automations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function getAutomation(id: string): Promise<{ task: Automation; runs: AutomationRun[] }> {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`);
  return res.json();
}

export async function updateAutomation(id: string, changes: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function deleteAutomation(id: string) {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`, { method: "DELETE" });
  return res.json();
}

export interface PreparedRun {
  ok: boolean;
  error?: string;
  run_id: string;
  session_id: string;
  workspace: string;
  agent: string;
  prompt: string;
}

/** Prepare a live manual run: returns the session to open + the opening prompt to send. */
export async function runAutomation(id: string): Promise<PreparedRun> {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}/run`, { method: "POST" });
  return res.json();
}

/** Mark a manual run complete after its first turn finished. */
export async function finalizeAutomationRun(id: string, runId: string) {
  const res = await fetch(
    `${httpBase()}/v1/automations/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}/finalize`,
    { method: "POST" },
  );
  return res.json();
}

export async function allowUser(
  name: string,
  userId: string,
  teamId?: string | null,
  displayName?: string,
) {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/allow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      ...(teamId ? { team_id: teamId } : {}),
      // Directory picks carry the display name so the chip is readable at once.
      ...(displayName ? { name: displayName } : {}),
    }),
  });
  return res.json();
}

// One workspace member from the roster (people picker; users:read, cached locally).
export interface SlackMember {
  id: string;
  name: string;
  handle: string;
  guest: boolean;
}

// One channel from the workspace roster. Private channels appear only where the
// bot is a member (Slack API constraint); is_member=false → "invite @Astra" hint.
export interface SlackChannelEntry {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

/** Workspace member roster for the people picker (teamId "default" = manual Socket Mode). */
export async function getSlackDirectory(
  teamId: string,
  q = "",
): Promise<{ ok: boolean; error?: string; members?: SlackMember[] }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/slack/workspaces/${encodeURIComponent(teamId)}/directory?q=${encodeURIComponent(q)}`,
  );
  return res.json();
}

/** Channel roster for the channel typeahead (name → id resolution). */
export async function getSlackChannels(
  teamId: string,
  q = "",
): Promise<{ ok: boolean; error?: string; channels?: SlackChannelEntry[] }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/slack/workspaces/${encodeURIComponent(teamId)}/channels?q=${encodeURIComponent(q)}`,
  );
  return res.json();
}

/** Resolve a parked unauthorized message (§19): dismiss / allow / allow_deliver. */
export async function resolveUnauthorized(
  name: string,
  itemId: string,
  action: "dismiss" | "allow" | "allow_deliver",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/unauthorized/${encodeURIComponent(itemId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    },
  );
  return res.json();
}

export async function disallowUser(name: string, userId: string, teamId?: string | null) {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/disallow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(teamId ? { user_id: userId, team_id: teamId } : { user_id: userId }),
  });
  return res.json();
}

export async function addSlackApprovalOwner(
  userId: string,
  displayName?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/approval-owners/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      ...(displayName ? { name: displayName } : {}),
    }),
  });
  return res.json();
}

export async function removeSlackApprovalOwner(
  userId: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/approval-owners/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  return res.json();
}

/** Stop relaying one managed Slack workspace (the app stays installed in Slack). */
export async function disconnectSlackWorkspace(teamId: string): Promise<{ ok: boolean; error?: string; remaining_workspaces?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/slack/workspaces/${encodeURIComponent(teamId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE Gmail mailbox; the default pointer moves to the next account. */
export async function disconnectGmailAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/gmail/accounts/${encodeURIComponent(email)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setGmailDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/gmail/accounts/${encodeURIComponent(email)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE Google Calendar account; the default pointer moves to the next one. */
export async function disconnectGcalAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/google_calendar/accounts/${encodeURIComponent(email)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setGcalDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/google_calendar/accounts/${encodeURIComponent(email)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE account of a generic multi-account connector (notion, attio,
 * posthog, …); the default pointer moves to the next account. */
export async function disconnectAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(connector)}/accounts/${encodeURIComponent(accountId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setDefaultAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(connector)}/accounts/${encodeURIComponent(accountId)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Replace the "Never show agents" lists (senders and/or labels; omit to keep). */
export async function setGmailFilters(filters: { senders?: string[]; labels?: string[] }): Promise<{ ok: boolean; filters?: GmailFilters; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/gmail/filters`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  return res.json();
}

// GitHub relay health, the Slack three-layer shape: shared relay socket /
// cloud sign-in / per-installation token health (+ missed-event counts).
export interface GithubStatus {
  ok: boolean;
  mode: string;
  relay: { state: string; reconnects: number; last_event_at: number | null; last_error: string };
  signed_in: boolean;
  installs: Record<string, { token_ok: boolean }>;
  missed: Record<string, number>;
}

export async function getGithubStatus(): Promise<GithubStatus> {
  const res = await fetch(`${httpBase()}/v1/connectors/github/status`);
  return res.json();
}

/** Stop relaying ONE GitHub App installation to this computer. */
export async function disconnectGithubInstallation(installationId: string): Promise<{ ok: boolean; error?: string; remaining_installs?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/github/installations/${encodeURIComponent(installationId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE HubSpot portal; the default pointer moves to the next portal. */
export async function disconnectHubSpotPortal(hubId: string): Promise<{ ok: boolean; error?: string; remaining_portals?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/hubspot/portals/${encodeURIComponent(hubId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setHubSpotDefaultPortal(hubId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/hubspot/portals/${encodeURIComponent(hubId)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Replace the hidden-fields denylist (properties stripped from agent reads). */
export async function setHubSpotHiddenFields(fields: string[]): Promise<{ ok: boolean; hidden_fields?: string[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/hubspot/hidden-fields`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hidden_fields: fields }),
  });
  return res.json();
}

/** Slack health, three honest layers: relay socket / cloud sign-in / per-team tokens. */
export interface SlackStatus {
  mode: string; // "relay" | "" (manual/off)
  relay: {
    state: "live" | "reconnecting" | "offline";
    reconnects: number;
    last_event_at: number | null;
    last_error: string;
  };
  signed_in: boolean;
  teams: Record<string, { token_ok: boolean }>;
}

export async function getSlackStatus(): Promise<SlackStatus> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/status`);
  return res.json();
}

export type Handlers = {
  onEvent: (event: WsEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
};

export class Session {
  private ws!: WebSocket;
  // Payloads sent before the socket finished opening, replayed on `onopen`. Belt-and-suspenders
  // against the first message being dropped if the user sends in the connect window.
  private outbox: object[] = [];
  private closed = false;
  private reconnectTimer: number | null = null;
  private sessionId: string;
  private workspace: string;
  private agent: string;
  private handlers: Handlers;

  constructor(sessionId: string, workspace: string, agent: string, handlers: Handlers) {
    this.sessionId = sessionId;
    this.workspace = workspace;
    this.agent = agent;
    this.handlers = handlers;
    this.connect();
  }

  private connect() {
    if (this.closed) return;
    const q = `?workspace=${encodeURIComponent(this.workspace)}&agent=${encodeURIComponent(this.agent)}`;
    this.ws = openWebSocket(`${wsBase()}/ws/session/${this.sessionId}${q}`);
    this.ws.onmessage = (e) => {
      try {
        this.handlers.onEvent(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    this.ws.onopen = () => {
      if (this.reconnectTimer !== null) {
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      this.flush();
      this.handlers.onOpen?.();
    };
    this.ws.onclose = () => {
      this.handlers.onClose?.();
      if (!this.closed && this.reconnectTimer === null) {
        this.reconnectTimer = window.setTimeout(() => {
          this.reconnectTimer = null;
          this.connect();
        }, 1500);
      }
    };
  }

  private flush() {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    const pending = this.outbox;
    this.outbox = [];
    for (const p of pending) this.ws.send(JSON.stringify(p));
  }

  private send(payload: object) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
    // Still connecting: queue and flush on open rather than silently dropping.
    else if (this.ws.readyState === WebSocket.CONNECTING) this.outbox.push(payload);
  }

  /** `model` = the composer's CURRENT selection, carried on every message so the turn uses
   * exactly what the user sees — immune to set_model races across reconnects (a new cowork
   * session always reconnects once to adopt its scratch dir, which could drop a queued
   * set_model and leave the engine on a stale/resumed model; found 2026-07-04). */
  userMessage(text: string, attachments?: unknown[], model?: string, skill?: string) {
    this.send({
      type: "user_message",
      text,
      ...(model ? { model } : {}),
      ...(attachments?.length ? { attachments } : {}),
      // Force-run (SKILLS-SPEC §4.1): the composer's /skill pick rides as its own field;
      // the server validates it against the session's effective menu and frames the turn.
      ...(skill ? { skill } : {}),
    });
  }

  approve(decision: string) {
    this.send({ type: "approval", decision });
  }

  /** §8.4 "Allow anyway": register a ONE-SHOT exact-action approval for a reviewer-denied
   *  tool call. The caller follows up with a normal user message so the agent retries. */
  allowAnyway(name: string, args: any) {
    this.send({ type: "allow_anyway", name, arguments: args ?? {} });
  }

  // Reply to a `request_directory` prompt: grant a folder (with access level) or decline.
  respondDirectory(granted: boolean, path?: string, writable?: boolean) {
    this.send({ type: "directory_response", granted, ...(path ? { path } : {}), writable: !!writable });
  }

  // Reply to a `request_tool` prompt: install the pinned build, or skip the check.
  respondTool(approved: boolean) {
    this.send({ type: "tool_response", approved });
  }

  // Reply to a `propose_plan` prompt: approve (choosing the execution mode) or reject with feedback.
  respondPlan(approved: boolean, mode?: string, feedback?: string) {
    this.send({
      type: "plan_response",
      approved,
      ...(mode ? { mode } : {}),
      ...(feedback ? { feedback } : {}),
    });
  }

  respondTeam(approved: boolean, feedback?: string, enableChat?: boolean) {
    this.send({
      type: "team_response",
      approved,
      ...(feedback ? { feedback } : {}),
      ...(enableChat !== undefined ? { enable_chat: enableChat } : {}),
    });
  }

  respondItems(approved: boolean, feedback?: string) {
    this.send({
      type: "items_response",
      approved,
      ...(feedback ? { feedback } : {}),
    });
  }

  // Answer a live `ask_user` prompt (attended sessions; unattended ones answer via the Inbox).
  respondQuestion(answer: string) {
    this.send({ type: "question_response", answer });
  }

  interrupt() {
    this.send({ type: "interrupt" });
  }

  // Re-run a turn that ended in a provider error — no new user message; the server
  // guards on the history tail so a stray frame is a no-op.
  retry() {
    this.send({ type: "retry" });
  }

  setMode(mode: string) {
    this.send({ type: "set_mode", mode });
  }

  setModel(model: string) {
    this.send({ type: "set_model", model });
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    // Detach before closing: this socket's async `close` event may land AFTER the
    // successor session's `open` (observed when switching into an automation-run
    // session), and a torn-down socket must not clobber the new one's connected state.
    this.ws.onopen = null;
    this.ws.onmessage = null;
    this.ws.onclose = null;
    this.ws.close();
  }
}

// -- project bindings (pass 20 / UX-044) ---------------------------------------

export interface ProjectMenu {
  kind: "memory" | "board";
  bound: string | null;
  derived: { kind: "git" | "folder"; label: string; full: string; key: string } | null;
  named: { name: string; key: string }[];
}

export async function getProjectMenu(sessionId: string, kind: "memory" | "board"): Promise<ProjectMenu> {
  const r = await fetch(`${httpBase()}/v1/sessions/${sessionId}/project-menu?kind=${kind}`);
  return r.json();
}

export async function setProjectBinding(
  sessionId: string,
  kind: "memory" | "board",
  name: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${httpBase()}/v1/sessions/${sessionId}/bindings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name }),
  });
  return r.json();
}

export async function nameCurrentProject(
  sessionId: string,
  kind: "memory" | "board",
  name: string,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${httpBase()}/v1/sessions/${sessionId}/project-name`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name }),
  });
  return r.json();
}

// -- Remote VPS & SSH Runner --------------------------------------------------

export interface SSHProfile {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  has_password?: boolean;
  has_key?: boolean;
  key_path?: string;
  remote_cwd?: string;
  connect_timeout?: number;
  keepalive_interval?: number;
}

export interface SSHProfileInput {
  id?: string;
  name: string;
  host: string;
  port?: number;
  username: string;
  password?: string;
  key_path?: string;
  private_key?: string;
  key_passphrase?: string;
  remote_cwd?: string;
  connect_timeout?: number;
  keepalive_interval?: number;
}

export interface RemoteStatus {
  active_ssh_profile: SSHProfile | null;
  ssh_profiles_count: number;
  is_remote_daemon: boolean;
  host: string;
  port: number;
  token_auth_enabled: boolean;
}

export async function listSSHProfiles(): Promise<SSHProfile[]> {
  const res = await fetch(`${httpBase()}/v1/remote/ssh/profiles`);
  return (await res.json()).profiles ?? [];
}

export async function saveSSHProfile(
  profile: SSHProfileInput,
): Promise<{ ok: boolean; profile?: SSHProfile; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/remote/ssh/profiles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  return res.json();
}

export async function deleteSSHProfile(
  profileId: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/remote/ssh/profiles/${encodeURIComponent(profileId)}`,
    { method: "DELETE" },
  );
  return res.json();
}

export async function setActiveSSHProfile(
  profileId: string,
): Promise<{ ok: boolean; active_ssh_profile?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/remote/ssh/active`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: profileId }),
  });
  return res.json();
}

export async function testSSHProfile(
  profile: SSHProfileInput,
): Promise<{
  ok: boolean;
  host?: string;
  latency_ms?: number;
  remote_cwd?: string;
  error?: string;
}> {
  const res = await fetch(`${httpBase()}/v1/remote/ssh/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  return res.json();
}

export async function getRemoteStatus(): Promise<RemoteStatus> {
  const res = await fetch(`${httpBase()}/v1/remote/status`);
  return res.json();
}
