import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createTeamConversation,
  createTeamAgent,
  connectTeamEvents,
  reduceTeamEvent,
  getTeamConversation,
  updateTeamConversation,
  transitionTeamConversation,
  addTeamConversationAgent,
  removeTeamConversationAgent,
  getTeamMessages,
  getTeamPinnedMessages,
  getTeamOverview,
  getTeamTasks,
  getTeamTask,
  getTeamTaskThread,
  getTeamRuns,
  getTeamRun,
  assignTeamTask,
  addTeamTaskDependency,
  addTeamTaskWatcher,
  removeTeamTaskWatcher,
  addTeamTaskLabel,
  removeTeamTaskLabel,
  cancelTeamTask,
  reopenTeamTask,
  getTeamTaskEvents,
  getTeamTaskVerifications,
  getTeamTaskHandoffs,
  getTeamPresence,
  getTeamNotifications,
  getTeamActivity,
  getTeamAgentExport,
  getTeamComputers,
  updateTeamAgent,
  runTeamAgentAction,
  runTeamAgentPlayground,
  getTeamAgentSandboxHealth,
  getTeamArtifacts,
  getTeamArtifactDownload,
  getTeamArtifactPreview,
  teamArtifactContentUrl,
  transitionTeamArtifact,
  quarantineTeamArtifact,
  releaseTeamArtifactQuarantine,
  addTeamMessageAttachment,
  teamMessageAttachmentUrl,
  markTeamNotificationRead,
  markTeamConversationRead,
  openTeamThread,
  postTeamMessage,
  editTeamMessage,
  deleteTeamMessage,
  setTeamMessageReaction,
  setTeamConversationPinned,
  pinTeamMessage,
  unpinTeamMessage,
  createTeamTask,
  dispatchTeamTask,
  transitionTeamTask,
  reviewTeamTask,
  translateTeamMessage,
  type TeamAgent,
  type TeamAgentPackage,
  type TeamComputer,
  type TeamPlaygroundResult,
  type TeamConversation,
  type TeamMessage,
  type TeamTask,
  type TeamTaskThread,
  type TeamTaskEvent,
  type TeamVerificationAttempt,
  type TeamHandoff,
  type TeamRun,
  type TeamRunDetail,
  type TeamOverview,
  type TeamPresence,
  type TeamDomainEvent,
  type TeamNotification,
  type TeamThread,
  type TeamActivity,
  type TeamArtifact,
  type TeamProjectionState,
} from "../api";
import { Icon } from "./Icon";
import { AstraBotAvatar } from "./AstraBotAvatar";
import { EmojiPicker } from "./EmojiPicker";
import { TeamComputerControl } from "./TeamComputerControl";
import { readDesktopNotificationPreference, readShowThinking } from "../preferences";
import { ThinkingBlock } from "./Transcript";
import { shouldNotifyDesktop, showDesktopNotification } from "../desktopNotifications";
type TeamTab = "all" | "unread" | "direct" | "groups" | "threads";
type TeamView = "chat" | "tasks" | "activity" | "agents" | "files" | "computer";
type AgentProfileTab = "overview" | "instructions" | "skills" | "connectors" | "sandbox" | "computer" | "memory" | "permissions" | "runs" | "activity" | "settings";
const TEAM_REACTION_OPTIONS = [
  { emoji: "👍", labelKey: "team_surface.reaction_like" },
  { emoji: "👏🏻", labelKey: "team_surface.reaction_cheer" },
  { emoji: "🎉", labelKey: "team_surface.reaction_celebrate" },
  { emoji: "✨", labelKey: "team_surface.reaction_appreciate" },
  { emoji: "🙂", labelKey: "team_surface.reaction_smile" },
] as const;

const BOT_AVATAR_IDS = Array.from({ length: 30 }, (_, index) => String(index + 1).padStart(2, "0"));

function botAvatarAssetUrl(avatarId?: string | null): string | null {
  const normalized = typeof avatarId === "string" ? avatarId.trim() : "";
  return normalized && BOT_AVATAR_IDS.includes(normalized)
    ? `/assets/astra-bot-avatars/${normalized}.png`
    : null;
}

const AGENT_PROFILE_TABS = [
  { value: "overview", labelKey: "team_surface.agent_overview" },
  { value: "instructions", labelKey: "team_surface.agent_instructions_tab" },
  { value: "skills", labelKey: "team_surface.agent_skills_tab" },
  { value: "connectors", labelKey: "team_surface.agent_connectors_tab" },
  { value: "sandbox", labelKey: "team_surface.agent_sandbox_tab" },
  { value: "computer", labelKey: "team_surface.agent_computer_tab" },
  { value: "memory", labelKey: "team_surface.agent_memory_tab" },
  { value: "permissions", labelKey: "team_surface.agent_permissions_tab" },
  { value: "runs", labelKey: "team_surface.agent_runs_tab" },
  { value: "activity", labelKey: "team_surface.agent_activity_tab" },
  { value: "settings", labelKey: "team_surface.agent_settings_tab" },
] as const satisfies ReadonlyArray<{ value: AgentProfileTab; labelKey: string }>;



const clientId = () =>
  (crypto as any).randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);

function conversationLabel(conversation: TeamConversation, agents: TeamAgent[]): string {
  if (conversation.title) return conversation.title;
  if (conversation.conversation_kind === "direct") {
    const agent = agents.find((item) => item.agent_id === conversation.agent_member_ids?.[0]);
    return agent?.display_name || agent?.handle || "Direct message";
  }
  return "Group conversation";
}

function directConversationAgentKey(conversation: TeamConversation, agents: TeamAgent[]): string | null {
  if (conversation.conversation_kind !== "direct") return null;
  const agentId = conversation.agent_member_ids?.[0] || conversation.coordinator_agent_id;
  if (agentId) {
    const agent = agents.find((item) => item.agent_id === agentId || item.actor_id === agentId);
    return `agent:${agent?.agent_id || agentId}`;
  }
  const actorId = conversation.agent_actor_ids?.[0];
  if (!actorId) return null;
  const agent = agents.find((item) => item.actor_id === actorId);
  return `agent:${agent?.agent_id || actorId}`;
}

function conversationUpdatedAt(conversation: TeamConversation): number {
  const timestamp = conversation.updated_at ? Date.parse(conversation.updated_at) : Number.NaN;
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function dedupeDirectConversations(conversations: TeamConversation[], agents: TeamAgent[]): TeamConversation[] {
  const result: TeamConversation[] = [];
  const indexByAgent = new Map<string, number>();

  for (const item of conversations) {
    const agentKey = directConversationAgentKey(item, agents);
    if (!agentKey) {
      result.push(item);
      continue;
    }
    const existingIndex = indexByAgent.get(agentKey);
    if (existingIndex === undefined) {
      indexByAgent.set(agentKey, result.length);
      result.push(item);
      continue;
    }
    const existing = result[existingIndex];
    if (conversationUpdatedAt(item) > conversationUpdatedAt(existing)) {
      result[existingIndex] = item;
    }
  }
  return result;
}

function timeLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function ageLabel(value?: string): string {
  if (!value) return "";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function costLabel(costMicros: number | undefined): string {
  if (!Number.isFinite(costMicros) || !costMicros) return "$0.00";
  return `$${(Number(costMicros) / 1_000_000).toFixed(4)}`;
}

function durationLabel(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt) return "—";
  const start = Date.parse(startedAt);
  const end = finishedAt ? Date.parse(finishedAt) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function artifactKind(filename: string): string {
  const extension = filename.split(".").pop()?.toLowerCase() || "file";
  if (["md", "mdx"].includes(extension)) return "md";
  if (["fig", "figma"].includes(extension)) return "fig";
  if (["yaml", "yml", "json", "js", "jsx", "ts", "tsx", "css", "html", "py", "sql"].includes(extension)) return "code";
  if (extension === "pdf") return "pdf";
  if (["csv", "xlsx", "xls"].includes(extension)) return "csv";
  if (["mp4", "mov", "webm"].includes(extension)) return "video";
  return "file";
}

function artifactSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function teamQueryValue(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

function priorityMeta(priority?: number): { labelKey: string; className: string } {
  switch (priority) {
    case 0:
      return { labelKey: "team_surface.priority_urgent", className: "bg-dangerSoft text-danger border-danger/30" };
    case 1:
      return { labelKey: "team_surface.priority_high", className: "bg-warningSoft text-warning border-warning/30" };
    case 2:
      return { labelKey: "team_surface.priority_medium", className: "bg-accentSoft text-accent border-accent/30" };
    case 3:
    default:
      return { labelKey: "team_surface.priority_low", className: "bg-panel text-muted border-line" };
  }
}

function deadlineMeta(dueAt?: string | null): { label: string; isOverdue: boolean; isSoon: boolean } | null {
  if (!dueAt) return null;
  const target = Date.parse(dueAt);
  if (!Number.isFinite(target)) return null;
  const now = Date.now();
  const diffMs = target - now;
  const isOverdue = diffMs < 0;
  const absHours = Math.abs(diffMs) / (1000 * 60 * 60);
  const absDays = Math.floor(absHours / 24);

  if (isOverdue) {
    if (absDays >= 1) return { label: `${absDays}d overdue`, isOverdue: true, isSoon: false };
    return { label: `${Math.max(1, Math.round(absHours))}h overdue`, isOverdue: true, isSoon: false };
  }
  const isSoon = absHours <= 24;
  if (absDays >= 1) return { label: `${absDays}d left`, isOverdue: false, isSoon };
  return { label: `${Math.max(1, Math.round(absHours))}h left`, isOverdue: false, isSoon: true };
}

function AgentAvatar({
  agent,
  avatarId,
  className = "h-10 w-10",
  iconSize: _iconSize = 18,
  online = false,
}: {
  agent?: TeamAgent | null;
  avatarId?: string | null;
  className?: string;
  iconSize?: number;
  online?: boolean;
}) {
  const title = agent?.display_name || agent?.handle || "AI agent";
  const assetUrl = botAvatarAssetUrl(avatarId ?? agent?.avatar_id);
  if (assetUrl) {
    return (
      <span
        className={`relative block shrink-0 overflow-visible ${className}`}
        title={title}
        data-testid="astra-bot-avatar"
        aria-label={title}
        role="img"
      >
        <img src={assetUrl} alt="" aria-hidden="true" className="block h-full w-full object-contain" />
        {online && <span className="absolute -bottom-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-panel bg-ok" aria-hidden="true" />}
      </span>
    );
  }
  return (
    <AstraBotAvatar
      identity={agent?.handle || agent?.agent_id || agent?.display_name || "astra"}
      role={agent?.base_role || ""}
      className={className}
      online={online}
      title={title}
      design="referenceGhost"
    />
  );
}

export function TeamSurface({
  onOpenInbox,
  onOpenAutomation,
  showWorkspaceRail = false,
}: {
  onOpenInbox?: (approvalId?: string) => void;
  onOpenAutomation?: (automationId?: string) => void;
  showWorkspaceRail?: boolean;
} = {}) {
  const { t } = useTranslation();
  const [overview, setOverview] = useState<TeamOverview | null>(null);
  const [presence, setPresence] = useState<TeamPresence[]>([]);
  const [notifications, setNotifications] = useState<TeamNotification[]>([]);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(() => teamQueryValue("team_message"));
  const [thread, setThread] = useState<TeamThread | null>(null);
  const [threadMessages, setThreadMessages] = useState<TeamMessage[]>([]);
  const [threadLoading, setThreadLoading] = useState(false);
  const [groupSettingsOpen, setGroupSettingsOpen] = useState(false);
  const [groupTitleDraft, setGroupTitleDraft] = useState("");
  const [groupReplyModeDraft, setGroupReplyModeDraft] = useState("mention_only");
  const [groupCoordinatorDraft, setGroupCoordinatorDraft] = useState("");
  const [groupAllowAtAllDraft, setGroupAllowAtAllDraft] = useState(false);
  const [groupMaxFanoutDraft, setGroupMaxFanoutDraft] = useState("8");
  const [groupMemberDraft, setGroupMemberDraft] = useState("");
  const [groupSettingsLoading, setGroupSettingsLoading] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [quoteMessage, setQuoteMessage] = useState<TeamMessage | null>(null);
  const [reactionMenuMessageId, setReactionMenuMessageId] = useState<string | null>(null);

  const [activeView, setActiveView] = useState<TeamView>(() => {
    const value = teamQueryValue("team_view");
    return value === "tasks" || value === "activity" || value === "agents" || value === "files" || value === "computer" ? value : "chat";
  });
  const [contextRailOpen, setContextRailOpen] = useState(true);
  const [activeTab, setActiveTab] = useState<TeamTab>("all");
  const [chatSearch, setChatSearch] = useState("");
  const chatSearchInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(() => teamQueryValue("team_conversation"));
  const [conversation, setConversation] = useState<TeamConversation | null>(null);
  const [messages, setMessages] = useState<TeamMessage[]>([]);
  const [messageTranslations, setMessageTranslations] = useState<Record<string, TeamMessage["translations"][number]>>({});
  const [translatingMessageId, setTranslatingMessageId] = useState<string | null>(null);
  const [pinnedMessageIds, setPinnedMessageIds] = useState<Set<string>>(new Set());
  const [showThinking] = useState<boolean>(() => readShowThinking());
  const [draft, setDraft] = useState("");
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState<File[]>([]);
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [composerOpen, setComposerOpen] = useState(false);
  const [agentComposerOpen, setAgentComposerOpen] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agentHandle, setAgentHandle] = useState("");
  const [agentInstructions, setAgentInstructions] = useState("");
  const [agentBaseRole, setAgentBaseRole] = useState("builder");
  const [agentTools, setAgentTools] = useState<string[]>(["web_search", "web_fetch"]);
  const [agentDraftReview, setAgentDraftReview] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newAgents, setNewAgents] = useState<string[]>([]);
  const [newAgentSearch, setNewAgentSearch] = useState("");
  const [tasks, setTasks] = useState<TeamTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(() => teamQueryValue("team_task"));
  const [taskDetail, setTaskDetail] = useState<TeamTask | null>(null);
  const [taskDetailLoading, setTaskDetailLoading] = useState(false);
  const [taskThread, setTaskThread] = useState<TeamTaskThread | null>(null);
  const [taskThreadLoading, setTaskThreadLoading] = useState(false);
  const [taskThreadReply, setTaskThreadReply] = useState("");
  const [taskThreadReplyLoading, setTaskThreadReplyLoading] = useState(false);
  const [taskAssignmentDraft, setTaskAssignmentDraft] = useState("");
  const [taskDependencyDraft, setTaskDependencyDraft] = useState("");
  const [activities, setActivities] = useState<TeamActivity[]>([]);
  const [artifacts, setArtifacts] = useState<TeamArtifact[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(() => teamQueryValue("team_artifact"));
  const [artifactPreview, setArtifactPreview] = useState<Record<string, unknown> | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactDownloadLoading, setArtifactDownloadLoading] = useState(false);
  const [artifactNotice, setArtifactNotice] = useState("");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(() => teamQueryValue("team_agent"));
  const [agentProfile, setAgentProfile] = useState<TeamAgentPackage | null>(null);
  const [agentProfileTab, setAgentProfileTab] = useState<AgentProfileTab>("overview");
  const [agentProfileLoading, setAgentProfileLoading] = useState(false);
  const [agentAvatarPickerOpen, setAgentAvatarPickerOpen] = useState(false);
  const [agentAvatarSaving, setAgentAvatarSaving] = useState(false);
  const [agentSearch, setAgentSearch] = useState("");

  const [agentInstructionsDraft, setAgentInstructionsDraft] = useState("");
  const [agentSaveLoading, setAgentSaveLoading] = useState(false);
  const [sandboxHealth, setSandboxHealth] = useState<Record<string, unknown> | null>(null);
  const [agentComputers, setAgentComputers] = useState<TeamComputer[]>([]);
  const [playgroundCommand, setPlaygroundCommand] = useState("echo hello-from-sandbox");
  const [playgroundResult, setPlaygroundResult] = useState<TeamPlaygroundResult | null>(null);
  const [playgroundLoading, setPlaygroundLoading] = useState(false);
  const [agentRuns, setAgentRuns] = useState<TeamRun[]>([]);
  const [selectedRunDetail, setSelectedRunDetail] = useState<TeamRunDetail | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [activityLoading, setActivityLoading] = useState(false);
  const [taskLoading, setTaskLoading] = useState(false);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDescription, setTaskDescription] = useState("");
  const [taskCriteria, setTaskCriteria] = useState("");
  const [taskOwner, setTaskOwner] = useState("");
  const [taskParentId, setTaskParentId] = useState("");
  const [taskPriority, setTaskPriority] = useState<number>(2);
  const [taskDueAt, setTaskDueAt] = useState("");
  const [taskReviewer, setTaskReviewer] = useState("");
  const [taskLabelsInput, setTaskLabelsInput] = useState("");
  const [taskEvents, setTaskEvents] = useState<TeamTaskEvent[]>([]);
  const [taskVerifications, setTaskVerifications] = useState<TeamVerificationAttempt[]>([]);
  const [taskHandoffs, setTaskHandoffs] = useState<TeamHandoff[]>([]);
  const [taskWatcherDraft, setTaskWatcherDraft] = useState("");
  const [taskLabelDraft, setTaskLabelDraft] = useState("");
  const [taskDetailTab, setTaskDetailTab] = useState<"overview" | "thread" | "verifications" | "events" | "handoffs" | "runs">("overview");
  const selectedIdRef = useRef<string | null>(null);
  const threadIdRef = useRef<string | null>(null);
  const actorIdRef = useRef<string | null>(null);
  const teamProjectionRef = useRef<TeamProjectionState>({ cursor: 0, aggregates: {} });
  const desktopPermissionRef = useRef<string>(
    typeof window !== "undefined" && "Notification" in window ? window.Notification.permission : "unsupported",
  );
  const [desktopPermission, setDesktopPermission] = useState(desktopPermissionRef.current);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    actorIdRef.current = overview?.actor_id || null;
  }, [overview?.actor_id]);

  useEffect(() => {
    desktopPermissionRef.current = desktopPermission;
  }, [desktopPermission]);

  useEffect(() => {
    threadIdRef.current = thread?.thread_id || null;
  }, [thread]);

  useEffect(() => {
    if (!reactionMenuMessageId) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest("[data-team-reaction-picker]")) return;
      setReactionMenuMessageId(null);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setReactionMenuMessageId(null);
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [reactionMenuMessageId]);


  const loadOverview = async (preserveSelection = true) => {
    setLoading(true);
    try {
      const next = await getTeamOverview();
      setOverview(next);
      void getTeamPresence().then(setPresence).catch(() => {});
      const nextConversations = dedupeDirectConversations(
        next.conversations.filter((item) => item.status !== "deleted"),
        next.agents,
      );
      if (!preserveSelection || !selectedId || !nextConversations.some((item) => item.conversation_id === selectedId)) {
        setSelectedId(nextConversations[0]?.conversation_id || null);
      }
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.load_error"));
    } finally {
      setLoading(false);
    }
  };

  const loadNotifications = async () => {
    try {
      const page = await getTeamNotifications({ limit: 40 });
      setNotifications(page.notifications || []);
    } catch {
      /* notifications are an enhancement; the Team surface remains usable */
    }
  };

  const loadTasks = async () => {
    setTaskLoading(true);
    try {
      const page = await getTeamTasks({ limit: 100 });
      if (page.error) throw new Error(page.error);
      setTasks(page.tasks || []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.load_error"));
    } finally {
      setTaskLoading(false);
    }
  };

  const loadTaskDetail = async (taskId: string) => {
    setSelectedTaskId(taskId);
    setTaskDetailLoading(true);
    setTaskThread(null);
    setTaskThreadReply("");
    setTaskEvents([]);
    setTaskVerifications([]);
    setTaskHandoffs([]);
    try {
      const detail = await getTeamTask(taskId);
      if (detail.error) throw new Error(detail.error);
      setTaskDetail(detail);
      setTaskAssignmentDraft(detail.owner_agent_id || "");
      setTaskThreadLoading(true);

      void getTeamTaskEvents(taskId).then((res) => {
        if (res.events) setTaskEvents(res.events);
      }).catch(() => {});

      void getTeamTaskVerifications(taskId).then((res) => {
        if (res.verifications) setTaskVerifications(res.verifications);
      }).catch(() => {});

      void getTeamTaskHandoffs(taskId).then((res) => {
        if (res.handoffs) setTaskHandoffs(res.handoffs);
      }).catch(() => {});

      try {
        const thread = await getTeamTaskThread(taskId);
        if (thread.error) throw new Error(thread.error);
        setTaskThread(thread);
      } catch {
        // Imported/legacy tasks can predate canonical threads. Keep their
        // detail view usable and explain the missing timeline in the UI.
        setTaskThread(null);
      } finally {
        setTaskThreadLoading(false);
      }
    } catch (caught) {
      setTaskDetail(null);
      setError(caught instanceof Error ? caught.message : t("team_surface.task_detail_error"));
    } finally {
      setTaskDetailLoading(false);
    }
  };

  const refreshTaskThread = async () => {
    if (!selectedTaskId) return;
    setTaskThreadLoading(true);
    try {
      const thread = await getTeamTaskThread(selectedTaskId);
      if (thread.error) throw new Error(thread.error);
      setTaskThread(thread);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_thread_error"));
    } finally {
      setTaskThreadLoading(false);
    }
  };

  const sendTaskThreadReply = async () => {
    const body = taskThreadReply.trim();
    if (!taskThread || !body || taskThreadReplyLoading) return;
    setTaskThreadReplyLoading(true);
    try {
      const result = await postTeamMessage(taskThread.thread.conversation_id, {
        body,
        client_request_id: clientId(),
        thread_id: taskThread.thread.thread_id,
        parent_message_id: taskThread.thread.root_message_id,
      });
      if (result.error) throw new Error(result.error);
      setTaskThreadReply("");
      await refreshTaskThread();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_thread_reply_error"));
    } finally {
      setTaskThreadReplyLoading(false);
    }
  };

  const loadActivity = async () => {
    setActivityLoading(true);
    try {
      const page = await getTeamActivity({ limit: 100 });
      setActivities(page.activities || []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.activity_error"));
    } finally {
      setActivityLoading(false);
    }
  };

  const loadArtifacts = async () => {
    setArtifactLoading(true);
    try {
      const page = await getTeamArtifacts(100);
      if (page.error) throw new Error(page.error);
      setArtifacts(page.artifacts || []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.artifact_error"));
    } finally {
      setArtifactLoading(false);
    }
  };

  const loadArtifactPreview = async (artifactId: string) => {
    setSelectedArtifactId(artifactId);
    setArtifactPreview(null);
    try {
      const preview = await getTeamArtifactPreview(artifactId);
      if (preview.error) throw new Error(String(preview.error));
      setArtifactPreview(preview);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.artifact_error"));
    }
  };

  const loadAgentProfile = async (agentId: string) => {
    setSelectedAgentId(agentId);
    setAgentProfileTab("overview");
    setAgentAvatarPickerOpen(false);
    setAgentProfileLoading(true);
    setSandboxHealth(null);
    setAgentComputers([]);
    setAgentRuns([]);
    setSelectedRunDetail(null);
    setPlaygroundResult(null);
    try {
      const profile = await getTeamAgentExport(agentId);
      if (profile.error) throw new Error(profile.error);
      setAgentProfile(profile);
      setAgentInstructionsDraft(profile.revision.instructions);
      void getTeamComputers({ limit: 100 }).then((page) => {
        if (!page.error) setAgentComputers((page.computers || []).filter((computer) => computer.agent_id === agentId));
      }).catch(() => {});
      void getTeamAgentSandboxHealth(agentId).then((health) => {
        if (!health.error) setSandboxHealth(health);
      }).catch(() => {});
      void getTeamRuns({ agentId, limit: 25 }).then((page) => {
        if (!page.error) setAgentRuns(page.runs || []);
      }).catch(() => {});
    } catch (caught) {
      setAgentProfile(null);
      setError(caught instanceof Error ? caught.message : t("team_surface.agent_error"));
    } finally {
      setAgentProfileLoading(false);
    }
  };

  useEffect(() => {
    void loadOverview(false);
    void loadNotifications();
    void loadArtifacts();
    void loadActivity();
  }, []);

  useEffect(() => {
    if (!focusMessageId) return;
    const frame = window.requestAnimationFrame(() => {
      const target = Array.from(document.querySelectorAll<HTMLElement>("[data-team-message-id]"))
        .find((element) => element.dataset.teamMessageId === focusMessageId);
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
      setFocusMessageId(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusMessageId, messages]);

  useEffect(() => {
    if (activeView === "tasks") {
      void loadTasks();
      if (selectedTaskId) void loadTaskDetail(selectedTaskId);
    }
    if (activeView === "activity") void loadActivity();
    if (activeView === "files") {
      void loadArtifacts();
      if (selectedArtifactId) void loadArtifactPreview(selectedArtifactId);
    }
    if (activeView === "agents" && selectedAgentId) void loadAgentProfile(selectedAgentId);
  }, [activeView]);

  useEffect(() => {
    const syncSearchTarget = () => {
      const view = teamQueryValue("team_view");
      const nextView: TeamView = view === "tasks" || view === "activity" || view === "agents" || view === "files" || view === "computer" ? view : "chat";
      const nextConversation = teamQueryValue("team_conversation");
      const nextMessage = teamQueryValue("team_message");
      const nextTask = teamQueryValue("team_task");
      const nextArtifact = teamQueryValue("team_artifact");
      const nextAgent = teamQueryValue("team_agent");
      setActiveView(nextView);
      setSelectedId(nextConversation);
      setFocusMessageId(nextMessage);
      setSelectedTaskId(nextTask);
      setSelectedArtifactId(nextArtifact);
      setSelectedAgentId(nextAgent);
      setTaskDetail(null);
      setTaskThread(null);
      setAgentProfile(null);
      setArtifactPreview(null);
      if (nextTask) void loadTaskDetail(nextTask);
      if (nextAgent) void loadAgentProfile(nextAgent);
      if (nextArtifact) void getTeamArtifactPreview(nextArtifact).then((preview) => {
        if (!preview.error) setArtifactPreview(preview);
      }).catch(() => {});
    };
    window.addEventListener("ocw-team-search", syncSearchTarget);
    return () => window.removeEventListener("ocw-team-search", syncSearchTarget);
  }, []);

  useEffect(() => {
    const onTeamEvent = (event: TeamDomainEvent, replayed = false) => {
      teamProjectionRef.current = reduceTeamEvent(teamProjectionRef.current, event);
      void getTeamOverview().then((next) => {
        setOverview(next);
        const nextConversations = dedupeDirectConversations(
          next.conversations.filter((item) => item.status !== "deleted"),
          next.agents,
        );
        if (!selectedIdRef.current && nextConversations[0]) {
          setSelectedId(nextConversations[0].conversation_id);
        } else if (selectedIdRef.current && !nextConversations.some((item) => item.conversation_id === selectedIdRef.current)) {
          setSelectedId(nextConversations[0]?.conversation_id || null);
        }
      }).catch(() => {});
      void getTeamPresence().then(setPresence).catch(() => {});
      void loadActivity();
      if (event.event_type === "notification.created") {
        void getTeamNotifications({ limit: 40 }).then((page) => {
          setNotifications(page.notifications || []);
          if (
            event.payload?.recipient_actor_id !== actorIdRef.current
            || replayed
            || desktopPermissionRef.current !== "granted"
            || !shouldNotifyDesktop(readDesktopNotificationPreference(), "important")
          ) return;
          const notification = page.notifications.find((item) => item.notification_id === event.aggregate_id);
          if (!notification) return;
          showDesktopNotification(
            t("nav.team"),
            notification.notification_kind,
            () => void openNotification(notification),
          );
        }).catch(() => {});
      }
      const selected = selectedIdRef.current;
      if (event.aggregate_type === "task") void loadTasks();
      if (event.aggregate_type === "artifact") void loadArtifacts();
      const conversationId = event.aggregate_type === "message"
        ? String(event.payload?.conversation_id || "")
        : event.aggregate_type === "conversation" ? event.aggregate_id : "";
      if (selected && conversationId === selected) {
        void getTeamMessages(selected, { limit: 100 }).then((page) => {
          setMessages(page.messages || []);
        }).catch(() => {});
        const openThreadId = threadIdRef.current;
        if (openThreadId) {
          void getTeamMessages(selected, { limit: 100, threadId: openThreadId }).then((page) => {
            setThreadMessages(page.messages || []);
          }).catch(() => {});
        }
        if (event.event_type === "message.pinned" || event.event_type === "message.unpinned") {
          void getTeamPinnedMessages(selected).then((result) => {
            setPinnedMessageIds(new Set((result.pins || []).map((pin) => pin.message_id)));
          }).catch(() => {});
        }
      }
    };
    return connectTeamEvents(0, onTeamEvent);
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setConversation(null);
      setMessages([]);
      setThread(null);
      setThreadMessages([]);
      return;
    }
    setThread(null);
    setThreadMessages([]);
    setGroupSettingsOpen(false);
    setPinnedMessageIds(new Set());
    setEditingMessageId(null);
    setEditDraft("");
    setQuoteMessage(null);
    let cancelled = false;
    setError("");
    Promise.all([
      getTeamConversation(selectedId),
      getTeamMessages(selectedId, { limit: 100 }),
      getTeamPinnedMessages(selectedId),
    ])
      .then(([nextConversation, page, pins]) => {
        if (cancelled) return;
        setConversation(nextConversation);
        setGroupTitleDraft(nextConversation.title || "");
        setGroupReplyModeDraft(nextConversation.reply_mode || "mention_only");
        setGroupCoordinatorDraft(nextConversation.coordinator_agent_id || "");
        setGroupAllowAtAllDraft(Boolean(nextConversation.allow_at_all));
        setGroupMaxFanoutDraft(String(nextConversation.max_fanout || 8));
        setMessages(page.messages || []);
        setPinnedMessageIds(new Set((pins.pins || []).map((pin) => pin.message_id)));
        const last = page.messages?.[page.messages.length - 1];
        if (last) void markTeamConversationRead(selectedId, last.message_id).catch(() => {});
        if (activeTab === "threads") {
          const unreadThreadEntries = Object.entries(nextConversation.thread_unread || {});
          const firstUnreadThread = unreadThreadEntries.find(([, count]) => Number(count || 0) > 0);
          if (firstUnreadThread) {
            void openTeamThread(selectedId, firstUnreadThread[0]).then((result) => {
              if (!cancelled && result.thread) {
                setThread(result.thread);
                setThreadMessages(result.messages || []);
              }
            }).catch(() => {});
          }
        }
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : t("team_surface.load_error"));
      });
    return () => {
      cancelled = true;
    };
  }, [activeTab, selectedId]);

  const conversations = overview?.conversations || [];
  const agents = overview?.agents || [];
  const newChatAgents = useMemo(() => {
    const query = newAgentSearch.trim().toLowerCase();
    if (!query) return agents;
    return agents.filter((agent) =>
      [agent.display_name, agent.handle, agent.description, agent.base_role]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query)),
    );
  }, [agents, newAgentSearch]);
  const newConversationIsDirect = newAgents.length === 1;

  const visibleAgents = useMemo(() => {
    const query = agentSearch.trim().toLowerCase();
    if (!query) return agents;
    return agents.filter((agent) =>
      [agent.display_name, agent.handle, agent.description, agent.base_role]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query)),
    );
  }, [agentSearch, agents]);

  const displayConversations = useMemo(
    () => dedupeDirectConversations(
      conversations.filter((item) => item.status !== "deleted"),
      agents,
    ),
    [agents, conversations],
  );
  const orderedConversations = displayConversations;
  const selectedConversationIndex = orderedConversations.findIndex((item) => item.conversation_id === selectedId);
  const presenceByAgent = useMemo(
    () => new Map(presence.map((item) => [item.agent_id, item])),
    [presence],
  );
  const visibleConversations = useMemo(() => {
    let items = displayConversations;
    if (activeTab === "unread") {
      items = items.filter(
        (item) =>
          Number(item.unread_count || 0) > 0 ||
          Object.values(item.thread_unread || {}).some((count) => Number(count || 0) > 0),
      );
    } else if (activeTab === "direct") {
      items = items.filter((item) => item.conversation_kind === "direct");
    } else if (activeTab === "groups") {
      items = items.filter((item) => item.conversation_kind === "group");
    } else if (activeTab === "threads") {
      items = items.filter(
        (item) =>
          Object.values(item.thread_unread || {}).some((count) => Number(count || 0) > 0) ||
          Boolean((item as { has_threads?: boolean }).has_threads),
      );
    }
    const query = chatSearch.trim().toLowerCase();
    if (query) {
      items = items.filter((item) => conversationLabel(item, agents).toLowerCase().includes(query));
    }
    return items;
  }, [activeTab, agents, chatSearch, displayConversations]);
  const pinnedConversations = useMemo(() => {
    const query = chatSearch.trim().toLowerCase();
    return displayConversations.filter((item) => {
      if (!item.pinned) return false;
      return !query || conversationLabel(item, agents).toLowerCase().includes(query);
    });
  }, [agents, chatSearch, displayConversations]);
  const chatConversations = visibleConversations.filter((item) => !item.pinned);
  const unreadConversationCount = useMemo(
    () =>
      displayConversations.filter(
        (item) =>
          Number(item.unread_count || 0) > 0 ||
          Object.values(item.thread_unread || {}).some((count) => Number(count || 0) > 0),
      ).length,
    [displayConversations],
  );
  const threadUnreadCount = displayConversations.reduce(
    (total, item) => total + Object.values(item.thread_unread || {}).reduce((sum, count) => sum + Number(count || 0), 0),
    0,
  );
  const navigateConversation = (direction: -1 | 1) => {
    const next = selectedConversationIndex >= 0 ? orderedConversations[selectedConversationIndex + direction] : null;
    if (next) setSelectedId(next.conversation_id);
  };

  const addAttachments = (fileList: FileList | null) => {
    const files = Array.from(fileList || []);
    if (files.length) setPendingAttachments((current) => [...current, ...files].slice(0, 8));
  };

  const insertEmoji = (emojiChar: string = "🙂") => {
    const input = composerInputRef.current;
    const start = input?.selectionStart ?? draft.length;
    const end = input?.selectionEnd ?? draft.length;
    const next = `${draft.slice(0, start)}${emojiChar}${draft.slice(end)}`;
    setDraft(next);
    requestAnimationFrame(() => {
      input?.focus();
      input?.setSelectionRange(start + emojiChar.length, start + emojiChar.length);
    });
  };

  const toggleDraftBold = () => {
    const input = composerInputRef.current;
    const start = input?.selectionStart ?? draft.length;
    const end = input?.selectionEnd ?? draft.length;
    const selected = draft.slice(start, end);
    const next = `${draft.slice(0, start)}**${selected || "text"}**${draft.slice(end)}`;
    setDraft(next);
    requestAnimationFrame(() => {
      input?.focus();
      const cursor = selected ? start + selected.length + 4 : start + 2;
      input?.setSelectionRange(cursor, cursor);
    });
  };

  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const selectedAgent = agents.find((item) => item.agent_id === selectedAgentId) || null;
  const selectedAgentPresence = selectedAgentId ? presenceByAgent.get(selectedAgentId) : null;
  const selectedAgentAvatarId = agentProfile?.definition.avatar_id ?? selectedAgent?.avatar_id ?? null;
  const conversationAgent = conversation?.conversation_kind === "direct"
    ? agents.find((agent) => agent.agent_id === conversation.agent_member_ids?.[0] || agent.agent_id === conversation.coordinator_agent_id || agent.actor_id === conversation.agent_actor_ids?.[0])
    : null;
  const selectedArtifact = artifacts.find((item) => item.artifact_id === selectedArtifactId) || null;
  const contextAgents = useMemo(() => {
    if (conversation?.conversation_kind === "group" && conversation.agent_member_ids?.length) {
      const memberIds = new Set(conversation.agent_member_ids);
      return agents.filter((agent) => memberIds.has(agent.agent_id)).slice(0, 6);
    }
    if (conversationAgent) return [conversationAgent];
    return agents.slice(0, 6);
  }, [agents, conversation, conversationAgent]);
  const selectedAgentConversations = useMemo(() => {
    if (!selectedAgentId) return [];
    return displayConversations.filter((item) =>
      item.conversation_kind === "direct"
      && directConversationAgentKey(item, agents) === `agent:${selectedAgentId}`,
    );
  }, [agents, displayConversations, selectedAgentId]);
  const openAgentConversation = () => {
    const target = selectedAgentConversations[0];
    if (!target) {
      setError(t("team_surface.agent_no_conversation"));
      return;
    }
    setActiveView("chat");
    setSelectedId(target.conversation_id);
  };

  const mentionMatch = draft.match(/(?:^|\s)@([\w-]*)$/u);
  const mentionCandidates = mentionMatch && conversation
    ? agents.filter((agent) =>
        conversation.agent_member_ids?.includes(agent.agent_id)
        && agent.handle
        && agent.handle.toLowerCase().startsWith(mentionMatch[1].toLowerCase()),
      ).slice(0, 6)
    : [];
  const mentionActorIds = (body: string) => {
    const tokens = new Set((body.match(/@[\w-]+/gu) || []).map((value) => value.slice(1).toLowerCase()));
    return agents
      .filter((agent) => agent.handle && tokens.has(agent.handle.toLowerCase()) && conversation?.agent_member_ids?.includes(agent.agent_id))
      .map((agent) => agent.actor_id);
  };

  const openMessageThread = async (messageId: string) => {
    if (!selectedId) return;
    setThreadLoading(true);
    try {
      const result = await openTeamThread(selectedId, messageId);
      if (!result.thread) throw new Error(t("team_surface.thread_error"));
      setThread(result.thread);
      setThreadMessages(result.messages || []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.thread_error"));
    } finally {
      setThreadLoading(false);
    }
  };

  const toggleReaction = async (message: TeamMessage, emoji: string) => {
    const active = !message.reactions.some(
      (reaction) => reaction.actor_id === overview?.actor_id && reaction.emoji === emoji,
    );
    setReactionMenuMessageId(null);
    try {
      const result = await setTeamMessageReaction(message.message_id, emoji, active);
      if (result.error) throw new Error(String(result.error));
      if (selectedId) {
        const page = await getTeamMessages(selectedId, { limit: 100 });
        setMessages(page.messages || []);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.reaction_error"));
    }
  };

  const togglePin = async (message: TeamMessage) => {
    if (!selectedId || busy) return;
    setBusy(true);
    try {
      const result = pinnedMessageIds.has(message.message_id)
        ? await unpinTeamMessage(selectedId, message.message_id)
        : await pinTeamMessage(selectedId, message.message_id);
      if (result.error) throw new Error(result.error);
      const pins = await getTeamPinnedMessages(selectedId);
      setPinnedMessageIds(new Set((pins.pins || []).map((pin) => pin.message_id)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.pin_error"));
    } finally {
      setBusy(false);
    }
  };

  const beginEdit = (message: TeamMessage) => {
    setEditingMessageId(message.message_id);
    setEditDraft(message.body);
  };

  const cancelEdit = () => {
    setEditingMessageId(null);
    setEditDraft("");
  };

  const saveEdit = async () => {
    const body = editDraft.trim();
    if (!editingMessageId || !body || busy) return;
    setBusy(true);
    try {
      const current = messages.find((item) => item.message_id === editingMessageId)
        || threadMessages.find((item) => item.message_id === editingMessageId);
      const updated = await editTeamMessage(editingMessageId, body, current?.revision_number);
      if (updated.error) throw new Error(updated.error);
      setMessages((current) => current.map((item) => item.message_id === updated.message_id ? updated : item));
      setThreadMessages((current) => current.map((item) => item.message_id === updated.message_id ? updated : item));
      cancelEdit();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.edit_error"));
    } finally {
      setBusy(false);
    }
  };

  const removeMessage = async (message: TeamMessage) => {
    if (busy || !window.confirm(t("team_surface.delete_confirm"))) return;
    setBusy(true);
    try {
      const deleted = await deleteTeamMessage(message.message_id);
      if (deleted.error) throw new Error(deleted.error);
      setMessages((current) => current.map((item) => item.message_id === deleted.message_id ? deleted : item));
      setThreadMessages((current) => current.map((item) => item.message_id === deleted.message_id ? deleted : item));
      if (editingMessageId === message.message_id) cancelEdit();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.delete_error"));
    } finally {
      setBusy(false);
    }
  };

  const copyMessageLink = async (messageId: string) => {
    if (!selectedId || !navigator.clipboard) return;
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("team_conversation", selectedId);
      url.searchParams.set("team_message", messageId);
      await navigator.clipboard.writeText(url.toString());
    } catch {
      setError(t("team_surface.copy_error"));
    }
  };

  const translateMessage = async (message: TeamMessage) => {
    if (busy || message.status !== "active" || translatingMessageId === message.message_id) return;
    const language = (typeof navigator !== "undefined" ? navigator.language : "en").split("-")[0] || "en";
    const cached = message.translations?.find((item) => item.language.toLowerCase() === language.toLowerCase());
    if (cached) {
      setMessageTranslations((current) => ({ ...current, [message.message_id]: cached }));
      return;
    }
    setTranslatingMessageId(message.message_id);
    try {
      const translated = await translateTeamMessage(message.message_id, language);
      if (translated.error) throw new Error(translated.error);
      setMessageTranslations((current) => ({ ...current, [message.message_id]: translated }));
      setMessages((current) => current.map((item) => item.message_id === message.message_id
        ? { ...item, translations: [...(item.translations || []).filter((entry) => entry.language.toLowerCase() !== language.toLowerCase()), translated] }
        : item));
      setThreadMessages((current) => current.map((item) => item.message_id === message.message_id
        ? { ...item, translations: [...(item.translations || []).filter((entry) => entry.language.toLowerCase() !== language.toLowerCase()), translated] }
        : item));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.translation_error"));
    } finally {
      setTranslatingMessageId(null);
    }
  };

  const requestDesktopNotifications = async () => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    const permission = await window.Notification.requestPermission();
    setDesktopPermission(permission);
  };

  const openNotification = async (notification: TeamNotification) => {
    if (!notification.read_at) {
      await markTeamNotificationRead(notification.notification_id).catch(() => {});
      setNotifications((current) => current.map((item) => item.notification_id === notification.notification_id ? { ...item, read_at: new Date().toISOString() } : item));
    }
    const link = notification.link;
    if (link?.surface === "team") {
      setActiveView(link.view === "tasks" ? "tasks" : "chat");
      if (link.conversation_id) setSelectedId(link.conversation_id);
      if (link.message_id) setFocusMessageId(link.message_id);
    } else if (link?.surface === "inbox") {
      onOpenInbox?.(link.approval_id);
    } else if (link?.surface === "automations") {
      onOpenAutomation?.(link.automation_id);
    }
    setNotificationsOpen(false);
  };

  const toggleAgent = (agentId: string) => {
    setNewAgents((current) =>
      current.includes(agentId) ? current.filter((value) => value !== agentId) : [...current, agentId],
    );
  };

  const openNewConversation = () => {
    setComposerOpen(true);
    setAgentComposerOpen(false);
    setAgentDraftReview(false);
    setNewTitle("");
    setNewAgents([]);
    setNewAgentSearch("");
    setError("");
  };

  const closeNewConversation = (force = false) => {
    if (busy && !force) return;
    setComposerOpen(false);
    setNewAgentSearch("");
  };

  const toggleConversationPin = async (item: TeamConversation) => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await setTeamConversationPinned(item.conversation_id, !item.pinned);
      if (result.error) throw new Error(result.error);
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.pin_error"));
    } finally {
      setBusy(false);
    }
  };

  const createConversation = async () => {
    const isDirect = newAgents.length === 1;
    if (newAgents.length === 0 || (!isDirect && !newTitle.trim()) || busy) return;
    setBusy(true);
    try {
      const created = await createTeamConversation({
        kind: isDirect ? "direct" : "group",
        title: isDirect ? "" : newTitle.trim(),
        agent_ids: newAgents,
        ...(isDirect ? {} : { reply_mode: "coordinator", coordinator_agent_id: newAgents[0] }),
      });
      if (created.error || !created.conversation_id) {
        throw new Error(created.error || t("team_surface.action_error"));
      }
      closeNewConversation(true);
      setNewTitle("");
      setNewAgents([]);
      await loadOverview(true);
      setSelectedId(created.conversation_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.action_error"));
    } finally {
      setBusy(false);
    }
  };

  const saveGroupSettings = async () => {
    if (!conversation || conversation.conversation_kind !== "group" || groupSettingsLoading || !groupTitleDraft.trim()) return;
    setGroupSettingsLoading(true);
    try {
      const updated = await updateTeamConversation(conversation.conversation_id, {
        title: groupTitleDraft.trim(),
        reply_mode: groupReplyModeDraft,
        coordinator_agent_id: groupCoordinatorDraft || null,
        allow_at_all: groupAllowAtAllDraft,
        max_fanout: Math.max(1, Number(groupMaxFanoutDraft) || 8),
      });
      if (updated.error) throw new Error(updated.error);
      setConversation(updated);
      setGroupSettingsOpen(false);
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.group_action_error"));
    } finally {
      setGroupSettingsLoading(false);
    }
  };

  const transitionConversation = async (action: "archive" | "restore" | "delete") => {
    if (!conversation || busy) return;
    if (action === "delete" && !window.confirm(t("team_surface.delete_group_confirm"))) return;
    setBusy(true);
    try {
      const updated = await transitionTeamConversation(conversation.conversation_id, action);
      if (updated.error) throw new Error(updated.error);
      setGroupSettingsOpen(false);
      await loadOverview(false);
      setSelectedId(action === "delete" ? null : updated.conversation_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.group_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const addGroupAgent = async () => {
    if (!conversation || conversation.conversation_kind !== "group" || !groupMemberDraft || busy) return;
    setBusy(true);
    try {
      const updated = await addTeamConversationAgent(conversation.conversation_id, groupMemberDraft);
      if (updated.error) throw new Error(updated.error);
      setConversation(updated);
      setGroupMemberDraft("");
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.group_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const removeGroupAgent = async (agentId: string) => {
    if (!conversation || conversation.conversation_kind !== "group" || busy) return;
    setBusy(true);
    try {
      const updated = await removeTeamConversationAgent(conversation.conversation_id, agentId);
      if (updated.error) throw new Error(updated.error);
      setConversation(updated);
      if (groupCoordinatorDraft === agentId) setGroupCoordinatorDraft("");
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.group_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const createAgent = async () => {
    if (!agentName.trim() || !agentHandle.trim() || !agentInstructions.trim() || busy) return;
    setBusy(true);
    try {
      const created = await createTeamAgent({
        display_name: agentName.trim(),
        handle: agentHandle.trim(),
        instructions: agentInstructions.trim(),
        base_role: agentBaseRole,
        capabilities: agentTools.map((t) => ({
          capability_kind: "tool",
          capability_ref: t,
          required: false,
        })),
      });
      if (created.error) throw new Error(created.error);
      setAgentName("");
      setAgentHandle("");
      setAgentInstructions("");
      setAgentBaseRole("builder");
      setAgentTools(["web_search", "web_fetch"]);
      setAgentDraftReview(false);
      setAgentComposerOpen(false);
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.action_error"));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    const body = draft.trim();
    if (!body || !selectedId || busy) return;
    const requestId = clientId();
    const attachments = [...pendingAttachments];
    const optimistic: TeamMessage = {
      message_id: `client-${requestId}`,
      conversation_id: selectedId,
      author_actor_id: overview?.actor_id || "local",
      author_kind: "human",
      body,
      status: "active",
      created_at: new Date().toISOString(),
      mentions: [],
      reactions: [],
      attachments: [],
      translations: [],
    };
    setMessages((current) => [...current, optimistic]);
    setDraft("");
    setPendingAttachments([]);
    setBusy(true);
    try {
      const result = await postTeamMessage(selectedId, {
        body,
        client_request_id: requestId,
        mention_actor_ids: mentionActorIds(body),
        thread_id: thread?.thread_id || null,
        parent_message_id: thread?.root_message_id || null,
        quote_message_id: quoteMessage?.message_id || null,
      });
      if (result.error) throw new Error(result.error);
      const messageId = result.message?.message_id;
      if (!messageId) throw new Error(t("team_surface.send_error"));
      for (const file of attachments) {
        const uploaded = await addTeamMessageAttachment(messageId, file);
        if (uploaded.error) throw new Error(uploaded.error);
      }
      const page = await getTeamMessages(selectedId, { limit: 100 });
      setMessages(page.messages || []);
      if (thread) {
        const threadPage = await getTeamMessages(selectedId, {
          limit: 100,
          threadId: thread.thread_id,
        });
        setThreadMessages(threadPage.messages || []);
      }
      setQuoteMessage(null);
      void loadOverview(true);
    } catch (caught) {
      setMessages((current) => current.filter((item) => item.message_id !== optimistic.message_id));
      setDraft(body);
      setPendingAttachments(attachments);
      setError(caught instanceof Error ? caught.message : t("team_surface.send_error"));
    } finally {
      setBusy(false);
    }
  };

  const createTaskFromMessage = async (message: TeamMessage, dispatch = false) => {
    if (busy || message.status !== "active") return;
    setBusy(true);
    try {
      const conversationAgent = conversation?.coordinator_agent_id || conversation?.agent_member_ids?.[0] || null;
      if (dispatch && !conversationAgent) throw new Error(t("team_surface.assign_before_dispatch"));
      const created = await createTeamTask({
        title: message.body.replace(/\s+/gu, " ").trim().slice(0, 120) || t("team_surface.task_from_message"),
        description: message.body,
        acceptance_criteria: t("team_surface.task_from_message_criteria"),
        owner_agent_id: conversationAgent,
        reviewer_actor_id: overview?.actor_id || null,
      });
      if (created.error) throw new Error(created.error);
      if (dispatch && created.task_id) {
        const submitted = await dispatchTeamTask(created.task_id, {
          instruction: message.body,
          expected_output: created.acceptance_criteria,
          client_request_id: clientId(),
        });
        if (submitted.error) throw new Error(submitted.error);
      }
      await loadTasks();
      setActiveView("tasks");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const createTask = async () => {
    if (!taskTitle.trim() || !taskCriteria.trim() || busy) return;
    setBusy(true);
    try {
      const labels = taskLabelsInput
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const created = await createTeamTask({
        title: taskTitle.trim(),
        description: taskDescription.trim(),
        acceptance_criteria: taskCriteria.trim(),
        owner_agent_id: taskOwner || null,
        reviewer_actor_id: taskReviewer || overview?.actor_id || null,
        parent_task_id: taskParentId || null,
        priority: taskPriority,
        due_at: taskDueAt || null,
        labels: labels.length > 0 ? labels : undefined,
      });
      if (created.error) throw new Error(created.error);
      setTaskTitle("");
      setTaskDescription("");
      setTaskCriteria("");
      setTaskOwner("");
      setTaskParentId("");
      setTaskPriority(2);
      setTaskDueAt("");
      setTaskReviewer("");
      setTaskLabelsInput("");
      await loadTasks();
      void loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.action_error"));
    } finally {
      setBusy(false);
    }
  };

  const cancelSelectedTask = async (taskId: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await cancelTeamTask(taskId);
      if (res.error) throw new Error(res.error);
      await loadTasks();
      if (selectedTaskId === taskId) await loadTaskDetail(taskId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const reopenSelectedTask = async (taskId: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await reopenTeamTask(taskId);
      if (res.error) throw new Error(res.error);
      await loadTasks();
      if (selectedTaskId === taskId) await loadTaskDetail(taskId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const addSelectedTaskWatcher = async () => {
    if (!selectedTaskId || !taskWatcherDraft || busy) return;
    setBusy(true);
    try {
      const res = await addTeamTaskWatcher(selectedTaskId, taskWatcherDraft);
      if (res.error) throw new Error(res.error);
      setTaskWatcherDraft("");
      await loadTaskDetail(selectedTaskId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const removeSelectedTaskWatcher = async (actorId: string) => {
    if (!selectedTaskId || busy) return;
    setBusy(true);
    try {
      const res = await removeTeamTaskWatcher(selectedTaskId, actorId);
      if (res.error) throw new Error(res.error);
      await loadTaskDetail(selectedTaskId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const addSelectedTaskLabel = async () => {
    if (!selectedTaskId || !taskLabelDraft.trim() || busy) return;
    setBusy(true);
    try {
      const res = await addTeamTaskLabel(selectedTaskId, taskLabelDraft.trim());
      if (res.error) throw new Error(res.error);
      setTaskLabelDraft("");
      await loadTaskDetail(selectedTaskId);
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const removeSelectedTaskLabel = async (label: string) => {
    if (!selectedTaskId || busy) return;
    setBusy(true);
    try {
      const res = await removeTeamTaskLabel(selectedTaskId, label);
      if (res.error) throw new Error(res.error);
      await loadTaskDetail(selectedTaskId);
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const advanceTask = async (task: TeamTask) => {
    const next = task.status === "open" ? "in_progress" : task.status === "in_progress" ? "review" : task.status === "review" ? "done" : "";
    if (!next || busy || (task.status === "open" && !task.owner_agent_id)) return;
    setBusy(true);
    try {
      if (task.status === "review") {
        const reviewed = await reviewTeamTask(task.task_id, true);
        if (reviewed.error) throw new Error(reviewed.error);
        setTasks((current) => current.map((item) => item.task_id === task.task_id ? reviewed : item));
        if (selectedTaskId === task.task_id) setTaskDetail(reviewed);
        return;
      }
      if (task.status === "open") {
        const dispatched = await dispatchTeamTask(task.task_id, { client_request_id: clientId() });
        if (dispatched.error) throw new Error(dispatched.error);
        await loadTasks();
      } else {
        const updated = await transitionTeamTask(task.task_id, next);
        if (updated.error) throw new Error(updated.error);
        setTasks((current) => current.map((item) => item.task_id === task.task_id ? updated : item));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.action_error"));
    } finally {
      setBusy(false);
    }
  };

  const requestTaskChanges = async (task: TeamTask) => {
    if (task.status !== "review" || busy) return;
    setBusy(true);
    try {
      const reviewed = await reviewTeamTask(task.task_id, false);
      if (reviewed.error) throw new Error(reviewed.error);
      setTasks((current) => current.map((item) => item.task_id === task.task_id ? reviewed : item));
      if (selectedTaskId === task.task_id) setTaskDetail(reviewed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const assignSelectedTask = async () => {
    if (!selectedTaskId || !taskAssignmentDraft || busy) return;
    setBusy(true);
    try {
      const updated = await assignTeamTask(selectedTaskId, taskAssignmentDraft);
      if (updated.error) throw new Error(updated.error);
      setTaskDetail(updated);
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const addSelectedTaskDependency = async () => {
    if (!selectedTaskId || !taskDependencyDraft || taskDependencyDraft === selectedTaskId || busy) return;
    setBusy(true);
    try {
      const result = await addTeamTaskDependency(selectedTaskId, taskDependencyDraft);
      if (result.error) throw new Error(String(result.error));
      setTaskDependencyDraft("");
      await loadTaskDetail(selectedTaskId);
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.task_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const saveAgentInstructions = async () => {
    if (!selectedAgentId || !agentInstructionsDraft.trim() || agentSaveLoading) return;
    setAgentSaveLoading(true);
    try {
      const result = await updateTeamAgent(selectedAgentId, { instructions: agentInstructionsDraft.trim() });
      if (result.error) throw new Error(String(result.error));
      await loadAgentProfile(selectedAgentId);
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.agent_action_error"));
    } finally {
      setAgentSaveLoading(false);
    }
  };

  const saveAgentAvatar = async (avatarId: string) => {
    if (!selectedAgentId || !botAvatarAssetUrl(avatarId) || agentAvatarSaving) return;
    setAgentAvatarSaving(true);
    try {
      const result = await updateTeamAgent(selectedAgentId, { avatar_id: avatarId });
      if (result.error) throw new Error(String(result.error));
      await loadAgentProfile(selectedAgentId);
      await loadOverview(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.agent_action_error"));
    } finally {
      setAgentAvatarSaving(false);
    }
  };

  const runAgentPlayground = async () => {
    if (!selectedAgentId || !playgroundCommand.trim() || playgroundLoading) return;
    setPlaygroundLoading(true);
    try {
      const result = await runTeamAgentPlayground(selectedAgentId, playgroundCommand.trim().split(/\s+/u));
      if (result.error) throw new Error(result.error);
      setPlaygroundResult(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.playground_error"));
    } finally {
      setPlaygroundLoading(false);
    }
  };

  const openRun = async (runId: string) => {
    setRunDetailLoading(true);
    try {
      const detail = await getTeamRun(runId);
      if (detail.error) throw new Error(detail.error);
      setSelectedRunDetail(detail);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.run_error"));
    } finally {
      setRunDetailLoading(false);
    }
  };

  const manageAgent = async (action: "pause" | "stop" | "resume" | "restart" | "archive" | "reset" | "clone" | "delete") => {
    if (!selectedAgentId || busy) return;
    if (action === "archive" && !window.confirm(t("team_surface.archive_agent_confirm"))) return;
    if (action === "reset" && !window.confirm(t("team_surface.reset_sandbox_confirm"))) return;
    if (action === "stop" && !window.confirm(t("team_surface.stop_agent_confirm"))) return;
    if (action === "delete" && !window.confirm(t("team_surface.delete_agent_confirm"))) return;
    setBusy(true);
    try {
      const result = await runTeamAgentAction(selectedAgentId, action);
      if (result.error) throw new Error(String(result.error));
      if (action === "clone" || action === "delete") {
        setSelectedAgentId(null);
        setAgentProfile(null);
        await loadOverview(true);
      } else {
        await loadOverview(true);
        await loadAgentProfile(selectedAgentId);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.agent_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const downloadAgentPackage = () => {
    if (!agentProfile || !selectedAgentId) return;
    const blob = new Blob([JSON.stringify(agentProfile, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${agentProfile.definition.handle || selectedAgentId}.astra-agent.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const openArtifact = async (artifact: TeamArtifact) => {
    await loadArtifactPreview(artifact.artifact_id);
  };

  const copyArtifactLink = async (artifactId: string) => {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("team_view", "files");
      url.searchParams.set("team_artifact", artifactId);
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(url.toString());
      setArtifactNotice(t("team_surface.artifact_link_copied"));
    } catch {
      setError(t("team_surface.copy_error"));
    }
  };

  const downloadArtifact = async (artifact: TeamArtifact, reveal = false) => {
    if (artifactDownloadLoading) return;
    setArtifactDownloadLoading(true);
    setArtifactNotice("");
    try {
      const descriptor = await getTeamArtifactDownload(artifact.artifact_id);
      if (descriptor.error) throw new Error(descriptor.error);
      if (!descriptor.content_available) {
        if (!navigator.clipboard) throw new Error("clipboard unavailable");
        await navigator.clipboard.writeText(descriptor.storage_ref);
        setArtifactNotice(t("team_surface.artifact_external_storage"));
        return;
      }
      const anchor = document.createElement("a");
      anchor.href = teamArtifactContentUrl(artifact.artifact_id);
      anchor.download = descriptor.filename;
      if (reveal) {
        anchor.target = "_blank";
        anchor.rel = "noreferrer";
      }
      anchor.click();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.artifact_action_error"));
    } finally {
      setArtifactDownloadLoading(false);
    }
  };

  const manageArtifact = async (artifact: TeamArtifact, action: "archive" | "delete" | "quarantine" | "release") => {
    if (busy) return;
    if (action === "delete" && !window.confirm(t("team_surface.delete_artifact_confirm"))) return;
    setBusy(true);
    try {
      const result = action === "quarantine"
        ? await quarantineTeamArtifact(artifact.artifact_id, t("team_surface.quarantine_reason_default"))
        : action === "release"
          ? await releaseTeamArtifactQuarantine(artifact.artifact_id)
          : await transitionTeamArtifact(artifact.artifact_id, action === "delete" ? "deleted" : "archived");
      if (result.error) throw new Error(String(result.error));
      await loadArtifacts();
      if (selectedArtifactId === artifact.artifact_id) {
        if (action === "delete") {
          setSelectedArtifactId(null);
          setArtifactPreview(null);
        } else {
          await openArtifact(result);
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("team_surface.artifact_action_error"));
    } finally {
      setBusy(false);
    }
  };

  const renderConversationRow = (item: TeamConversation) => {
    const isDirect = item.conversation_kind === "direct";
    const isSelected = selectedId === item.conversation_id;
    const directAgentId = item.agent_member_ids?.[0] || item.coordinator_agent_id || item.agent_actor_ids?.[0];
    const directAgent = isDirect && directAgentId
      ? agents.find((agent) => agent.agent_id === directAgentId || agent.actor_id === directAgentId)
      : null;
    const isOnline = Boolean(directAgent && (
      presenceByAgent.get(directAgent.agent_id)?.state === "ready"
      || directAgent.status === "ready"
    ));
    const label = conversationLabel(item, agents);
    const memberCount = item.member_count ?? item.agent_member_ids?.length ?? Math.max(0, (item.members?.length || 1) - 1);

    return (
      <div
        key={item.conversation_id}
        role="listitem"
        className={
          "group relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 transition-all duration-150 " +
          (isSelected
            ? "bg-panel border border-line/70 shadow-xs text-ink"
            : "text-muted hover:bg-panel/50 hover:text-ink border border-transparent")
        }
      >
        <button
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
          onClick={() => setSelectedId(item.conversation_id)}
        >
          {isDirect ? (
            <AgentAvatar agent={directAgent} online={isOnline} className="h-8 w-8 rounded-lg shadow-2xs" iconSize={15} />
          ) : (
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/20 bg-accentSoft/60 text-accent shadow-2xs">
              <Icon name="users" size={14} />
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className="flex items-center justify-between gap-1.5">
              <span className={"min-w-0 truncate text-[13px] " + (isSelected ? "font-semibold text-ink" : "font-medium text-ink/90")}>{label}</span>
              <span className="shrink-0 text-[10px] text-faint">{ageLabel(item.updated_at)}</span>
            </span>
            <span className="mt-0.5 flex items-center justify-between gap-1 text-[11px] text-muted/80">
              <span className="truncate">{isDirect ? `@${directAgent?.handle || "agent"}` : `${memberCount} ${t("team_surface.members").toLowerCase()}`}</span>
              {Number(item.unread_count || 0) > 0 && (
                <span className="min-w-4 rounded-full bg-accent px-1.5 py-0.2 text-center text-[9px] font-bold text-white shadow-2xs">
                  {Number(item.unread_count) > 99 ? "99+" : item.unread_count}
                </span>
              )}
            </span>
          </span>
        </button>
        <button
          className={
            "shrink-0 rounded-md p-1 text-[11px] transition-opacity hover:bg-panel " +
            (item.pinned ? "text-accent" : "text-faint opacity-0 group-hover:opacity-100 hover:text-ink")
          }
          aria-label={item.pinned ? t("team_surface.unpin_conversation") : t("team_surface.pin_conversation")}
          title={item.pinned ? t("team_surface.unpin_conversation") : t("team_surface.pin_conversation")}
          onClick={() => void toggleConversationPin(item)}
        >
          <Icon name="pin" size={12} className={item.pinned ? "rotate-45" : ""} />
          <span className="sr-only">{item.pinned ? t("team_surface.unpin_conversation") : t("team_surface.pin_conversation")}</span>
        </button>
      </div>
    );
  };

  return (
    <div className={(showWorkspaceRail ? "team-surface team-surface-docked" : "team-surface") + (contextRailOpen ? "" : " team-context-collapsed") + ` team-view-${activeView} flex min-h-0 flex-1 bg-canvas`} data-testid="team-surface">
      {showWorkspaceRail && (
        <nav className="team-workspace-rail flex w-[72px] shrink-0 flex-col items-center border-r border-line bg-chrome py-4" aria-label={t("team_surface.views_label")}>
          <div className="team-rail-brand" aria-label="Astra">
            <Icon name="logo" size={26} />
          </div>
          <div className="flex flex-col items-center gap-2">
            {([
              ["chat", "messageCircle"],
              ["tasks", "listChecks"],
              ["activity", "activity"],
              ["agents", "bot"],
              ["files", "files"],
            ] as const).map(([view, icon]) => (
              <button
                key={view}
                className={"grid h-10 w-10 place-items-center rounded-xl transition-colors " + (activeView === view ? "bg-accentSoft text-accent" : "text-muted hover:bg-panel hover:text-ink")}
                aria-label={t(`team_surface.views.${view}`)}
                title={t(`team_surface.views.${view}`)}
                onClick={() => setActiveView(view)}
              >
                <Icon name={icon} size={18} />
              </button>
            ))}
          </div>
        </nav>
      )}
      {composerOpen && (
        <div
          className="team-new-chat-overlay"
          data-testid="team-new-conversation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeNewConversation();
          }}
        >
          <form
            className="team-new-chat-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="team-new-chat-title"
            onSubmit={(event) => {
              event.preventDefault();
              void createConversation();
            }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="team-new-chat-header">
              <h2 id="team-new-chat-title">{t("team_surface.new_chat")}</h2>
              <button
                type="button"
                className="team-new-chat-close"
                aria-label={t("modal.close")}
                title={t("modal.close")}
                onClick={() => closeNewConversation()}
              >
                <Icon name="x" size={18} />
              </button>
            </header>

            <div className="team-new-chat-body">
              <section className="team-new-chat-agents" aria-labelledby="team-new-chat-agents-title">
                <label className="team-new-chat-search">
                  <Icon name="search" size={16} />
                  <span className="sr-only">{t("team_surface.search_agents_contacts")}</span>
                  <input
                    autoFocus
                    type="search"
                    value={newAgentSearch}
                    onChange={(event) => setNewAgentSearch(event.target.value)}
                    placeholder={t("team_surface.search_agents_contacts")}
                  />
                </label>
                <h3 id="team-new-chat-agents-title" className="team-new-chat-section-label">
                  {t("team_surface.views.agents")}
                </h3>
                <div className="team-new-chat-agent-list" role="list">
                  {agents.length === 0 ? (
                    <div className="team-new-chat-empty">{t("team_surface.no_agents")}</div>
                  ) : newChatAgents.length === 0 ? (
                    <div className="team-new-chat-empty">{t("team_surface.no_matching_agents")}</div>
                  ) : newChatAgents.map((agent) => {
                    const name = agent.display_name || agent.handle || agent.agent_id;
                    const selected = newAgents.includes(agent.agent_id);
                    const online = presenceByAgent.get(agent.agent_id)?.state === "ready" || agent.status === "ready";
                    return (
                      <label key={agent.agent_id} className={"team-new-chat-agent" + (selected ? " is-selected" : "")}>
                        <AgentAvatar agent={agent} online={online} className="h-8 w-8 rounded-lg" iconSize={14} />
                        <span className="team-new-chat-agent-copy">
                          <span className="team-new-chat-agent-name">
                            <strong>{name}</strong>
                            <span className="team-new-chat-ai-badge">AI</span>
                          </span>
                          <span className="team-new-chat-agent-description">
                            {agent.description || agent.base_role || t("team_surface.agent")}
                          </span>
                        </span>
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleAgent(agent.agent_id)}
                          aria-label={name}
                        />
                        <span className="team-new-chat-checkbox" aria-hidden="true" />
                      </label>
                    );
                  })}
                </div>
              </section>

              <section className="team-new-chat-selected" aria-labelledby="team-new-chat-selected-title">
                <div className="team-new-chat-selected-heading">
                  <h3 id="team-new-chat-selected-title" className="team-new-chat-section-label">
                    {t("team_surface.selected")}
                  </h3>
                  <span className="team-new-chat-member-count">
                    {t("team_surface.selected_count", { count: newAgents.length + 1 })}
                  </span>
                </div>
                <div className="team-new-chat-member-list">
                  <div className="team-new-chat-member">
                    <span className="team-new-chat-user-avatar">Y</span>
                    <span className="team-new-chat-member-copy">
                      <strong>{t("team_surface.you")}</strong>
                      <small>({t("team_surface.you").toLowerCase()})</small>
                    </span>
                  </div>
                  {newAgents.map((agentId) => {
                    const agent = agents.find((item) => item.agent_id === agentId);
                    if (!agent) return null;
                    const name = agent.display_name || agent.handle || agent.agent_id;
                    return (
                      <div className="team-new-chat-member" key={agent.agent_id}>
                        <AgentAvatar agent={agent} online={presenceByAgent.get(agent.agent_id)?.state === "ready" || agent.status === "ready"} className="h-8 w-8 rounded-lg" iconSize={14} />
                        <span className="team-new-chat-member-copy">
                          <strong>{name}</strong>
                          <small>{agent.base_role || t("team_surface.agent")}</small>
                        </span>
                        <button
                          type="button"
                          className="team-new-chat-remove"
                          aria-label={`${t("common.remove")} ${name}`}
                          title={`${t("common.remove")} ${name}`}
                          onClick={() => toggleAgent(agent.agent_id)}
                        >
                          <Icon name="x" size={14} />
                        </button>
                      </div>
                    );
                  })}
                </div>

                {newConversationIsDirect ? (
                  <p className="rounded-lg bg-accentSoft p-3 text-[11px] text-muted">
                    {t("team_surface.direct_chat_hint")}
                  </p>
                ) : (
                  <div className="team-new-chat-group-field">
                    <label htmlFor="team-new-chat-group-name" className="team-new-chat-field-label">
                      {t("team_surface.group_name")} <span aria-hidden="true">*</span>
                    </label>
                    <div className="team-new-chat-group-input">
                      <span aria-hidden="true">GT</span>
                      <input
                        id="team-new-chat-group-name"
                        type="text"
                        required
                        value={newTitle}
                        onChange={(event) => setNewTitle(event.target.value)}
                        placeholder="e.g. AI Research"
                      />
                    </div>
                  </div>
                )}
              </section>
            </div>

            {error && <div className="team-new-chat-error" role="alert">{error}</div>}
            <footer className="team-new-chat-footer">
              <button type="button" className="team-new-chat-cancel" onClick={() => closeNewConversation()}>
                {t("team_surface.cancel")}
              </button>
              <button type="submit" className="team-new-chat-submit" disabled={busy || newAgents.length === 0 || (!newConversationIsDirect && !newTitle.trim())}>
                {busy ? t("team_surface.creating") : newConversationIsDirect ? t("team_surface.start_direct_chat") : t("team_surface.create_group_chat")}
              </button>
            </footer>
          </form>
        </div>
      )}
      <aside className="team-chat-sidebar team-sidebar flex shrink-0 flex-col border-r border-line/50 bg-chrome/60 px-3.5 pt-2 pb-4 backdrop-blur-md">
        <div className="mb-3 flex items-center justify-between px-1">
          <h1 className="text-[16px] font-bold tracking-tight text-ink">{t("team_surface.views.chat")}</h1>
          <div className="flex items-center gap-1">
            <div className="flex items-center gap-0.5 rounded-lg border border-line/50 bg-panel/80 p-0.5 shadow-2xs">
              <button
                className="grid h-6 w-6 place-items-center rounded-md text-muted transition-colors hover:bg-paper hover:text-ink disabled:pointer-events-none disabled:opacity-30"
                aria-label={t("team_surface.previous_conversation")}
                title={t("team_surface.previous_conversation")}
                disabled={selectedConversationIndex <= 0}
                onClick={() => navigateConversation(-1)}
              >
                <Icon name="arrowLeft" size={13} />
              </button>
              <button
                className="grid h-6 w-6 place-items-center rounded-md text-muted transition-colors hover:bg-paper hover:text-ink disabled:pointer-events-none disabled:opacity-30"
                aria-label={t("team_surface.next_conversation")}
                title={t("team_surface.next_conversation")}
                disabled={selectedConversationIndex < 0 || selectedConversationIndex >= orderedConversations.length - 1}
                onClick={() => navigateConversation(1)}
              >
                <Icon name="arrowLeft" size={13} className="rotate-180" />
              </button>
            </div>
            <button
              className="grid h-7 w-7 place-items-center rounded-lg text-muted transition-all hover:bg-panel hover:text-ink active:scale-95 shadow-2xs"
              onClick={() => { setAgentComposerOpen((value) => !value); setAgentDraftReview(false); }}
              title={t("team_surface.new_agent")}
              aria-label={t("team_surface.new_agent")}
            >
              <Icon name="sparkle" size={13} className="text-accent" />
            </button>
            <button
              className="grid h-7 w-7 place-items-center rounded-lg text-muted transition-all hover:bg-panel hover:text-ink active:scale-95 shadow-2xs"
              aria-label={t("team_surface.new_conversation")}
              title={t("team_surface.new_conversation")}
              onClick={openNewConversation}
            >
              <Icon name="plus" size={14} />
            </button>
            <button
              className="relative grid h-7 w-7 place-items-center rounded-lg text-muted transition-all hover:bg-panel hover:text-ink active:scale-95 shadow-2xs"
              aria-label={t("team_surface.notifications")}
              aria-expanded={notificationsOpen}
              title={t("team_surface.notifications")}
              onClick={() => setNotificationsOpen((value) => !value)}
            >
              <Icon name="moreHorizontal" size={14} />
              {unreadNotifications > 0 && (
                <span className="absolute -right-0.5 -top-0.5 min-w-3.5 rounded-full bg-accent px-1 text-center text-[9px] font-bold leading-3.5 text-white shadow-2xs">
                  {unreadNotifications > 9 ? "9+" : unreadNotifications}
                </span>
              )}
            </button>
            {notificationsOpen && (
              <div className="absolute right-0 top-10 z-30 w-72 rounded-2xl border border-line bg-panel p-2 shadow-xl backdrop-blur-md" role="menu" aria-label={t("team_surface.notifications")}>
                <div className="mb-1.5 flex items-center justify-between px-2.5 py-1">
                  <div className="text-[12px] font-semibold text-ink">{t("team_surface.notifications")}</div>
                  {desktopPermission === "default" && (
                    <button className="text-[10px] font-medium text-accent hover:underline" onClick={() => void requestDesktopNotifications()}>
                      {t("team_surface.enable_desktop_notifications")}
                    </button>
                  )}
                </div>
                {notifications.length === 0 ? (
                  <div className="px-2 py-4 text-center text-[11px] text-muted">{t("team_surface.no_notifications")}</div>
                ) : (
                  <div className="max-h-72 space-y-1 overflow-y-auto">
                    {notifications.slice(0, 8).map((notification) => (
                      <button
                        key={notification.notification_id}
                        role="menuitem"
                        className={"flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-paper " + (!notification.read_at ? "bg-accentSoft/30" : "")}
                        onClick={() => void openNotification(notification)}
                      >
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[11px] font-medium text-ink">{notification.notification_kind}</span>
                          <span className="block text-[10px] text-muted">{timeLabel(notification.created_at)}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Primary View Navigation Tabs */}
        {!showWorkspaceRail && <div className="mb-4 grid grid-cols-5 gap-0.5 rounded-xl bg-panel p-1" role="tablist" aria-label={t("team_surface.views_label")}>
          {(["chat", "tasks", "activity", "agents", "files"] as TeamView[]).map((view) => (
            <button
              key={view}
              role="tab"
              aria-selected={activeView === view}
              className={
                    "rounded-lg py-1.5 text-center text-[11px] font-medium transition-all duration-150 " +
                    (activeView === view
                  ? "bg-paper font-semibold text-ink shadow-2xs"
                  : "text-muted hover:bg-paper/70 hover:text-ink")
              }
              onClick={() => setActiveView(view)}
            >
              {t(`team_surface.views.${view}`)}
            </button>
          ))}
        </div>}

        {activeView === "agents" ? (
          <div className="rounded-xl border border-line/60 bg-paper/70 px-3.5 py-3 text-[11px] text-muted shadow-2xs">
            {t("team_surface.agent_count", { count: agents.length })}
          </div>
        ) : activeView === "files" ? (
          <div className="rounded-xl border border-line/60 bg-paper/70 px-3.5 py-3 text-[11px] text-muted shadow-2xs">
            {t("team_surface.files_count", { count: artifacts.length })}
          </div>
        ) : activeView === "tasks" ? (
          <div className="rounded-xl border border-line/60 bg-paper/70 px-3.5 py-3 text-[11px] text-muted shadow-2xs">
            {t("team_surface.task_count", { count: tasks.length })}
          </div>
        ) : activeView === "activity" ? (
          <div className="rounded-xl border border-line/60 bg-paper/70 px-3.5 py-3 text-[11px] text-muted shadow-2xs">
            {t("team_surface.activity_count", { count: activities.length })}
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <label className="relative mb-3 block">
              <Icon name="search" size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
              <input
                ref={chatSearchInputRef}
                className="h-11 w-full rounded-lg border border-line bg-panel pl-9 pr-3 text-[13px] text-ink shadow-2xs outline-none transition-colors placeholder:text-muted/70 focus:border-accent/60 focus:ring-2 focus:ring-accent/10"
                placeholder={t("sidebar.search")}
                value={chatSearch}
                onChange={(event) => setChatSearch(event.target.value)}
                aria-label={t("sidebar.search")}
              />
              <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-line bg-paper px-1.5 py-0.5 text-[10px] text-faint">Ctrl+K</kbd>
            </label>

            {/* Filter Tabs for Chat */}
            <div className="mb-3 flex items-center gap-1 rounded-xl border border-line/40 bg-panel/80 p-1 shadow-2xs backdrop-blur-sm" role="tablist">
              {(["all", "unread", "direct", "groups", "threads"] as TeamTab[]).map((tab) => (
                <button
                  key={tab}
                  role="tab"
                  aria-selected={activeTab === tab}
                  className={
                    "flex-1 rounded-lg py-1 text-center text-[11px] font-medium transition-all duration-150 " +
                    (activeTab === tab
                      ? "bg-paper font-semibold text-ink shadow-xs"
                      : "text-muted hover:text-ink")
                  }
                  onClick={() => setActiveTab(tab)}
                >
                  {t(`team_surface.tabs.${tab}`)}
                  {tab === "unread" && unreadConversationCount > 0 && (
                    <span className="ml-1 rounded-full bg-accent px-1.5 py-0.2 text-[9px] font-bold text-white">{unreadConversationCount}</span>
                  )}
                  {tab === "threads" && threadUnreadCount > 0 && (
                    <span className="ml-1 rounded-full bg-accent px-1.5 py-0.2 text-[9px] font-bold text-white">{threadUnreadCount}</span>
                  )}
                </button>
              ))}
            </div>


            {/* Agent Composer */}
            {agentComposerOpen && (
              <div className="mb-3 rounded-2xl border border-line bg-paper p-3 shadow-sm" data-testid="team-new-agent">
                {!agentDraftReview ? (
                  <>
                    <input
                      className="mb-2 w-full rounded-xl border border-line bg-panel px-3 py-1.5 text-[12px] text-ink outline-none transition-colors focus:border-accent"
                      placeholder={t("team_surface.agent_name")}
                      value={agentName}
                      onChange={(event) => setAgentName(event.target.value)}
                    />
                    <input
                      className="mb-2 w-full rounded-xl border border-line bg-panel px-3 py-1.5 text-[12px] text-ink outline-none transition-colors focus:border-accent"
                      placeholder={t("team_surface.agent_handle")}
                      value={agentHandle}
                      onChange={(event) => setAgentHandle(event.target.value)}
                    />
                    <select
                      className="mb-2 w-full rounded-xl border border-line bg-panel px-3 py-1.5 text-[12px] text-ink outline-none transition-colors focus:border-accent"
                      value={agentBaseRole}
                      onChange={(event) => setAgentBaseRole(event.target.value)}
                      aria-label={t("team_surface.agent_role")}
                    >
                      <option value="builder">{t("team_surface.role_builder")}</option>
                      <option value="reviewer">{t("team_surface.role_reviewer")}</option>
                      <option value="researcher">{t("team_surface.role_researcher")}</option>
                    </select>
                    <div className="mb-2">
                      <div className="mb-1 text-[11px] font-medium text-ink">Capabilities & Tools</div>
                      <div className="grid grid-cols-2 gap-1.5 text-[11px] text-muted">
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={agentTools.includes("web_search")}
                            onChange={(e) => {
                              setAgentTools((prev) => e.target.checked ? [...prev, "web_search"] : prev.filter((t) => t !== "web_search"));
                            }}
                          />
                          <span>Web Search</span>
                        </label>
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={agentTools.includes("web_fetch")}
                            onChange={(e) => {
                              setAgentTools((prev) => e.target.checked ? [...prev, "web_fetch"] : prev.filter((t) => t !== "web_fetch"));
                            }}
                          />
                          <span>Web Fetch</span>
                        </label>
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={agentTools.includes("read_file")}
                            onChange={(e) => {
                              setAgentTools((prev) => e.target.checked ? [...prev, "read_file"] : prev.filter((t) => t !== "read_file"));
                            }}
                          />
                          <span>Read Files</span>
                        </label>
                        <label className="flex items-center gap-1.5 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={agentTools.includes("run_shell")}
                            onChange={(e) => {
                              setAgentTools((prev) => e.target.checked ? [...prev, "run_shell"] : prev.filter((t) => t !== "run_shell"));
                            }}
                          />
                          <span>Run Shell</span>
                        </label>
                      </div>
                    </div>
                    <textarea
                      placeholder={t("team_surface.agent_instructions")}
                      value={agentInstructions}
                      onChange={(event) => setAgentInstructions(event.target.value)}
                    />
                    <button
                      className="btn primary w-full rounded-xl text-[12px]"
                      disabled={!agentName.trim() || !agentHandle.trim() || !agentInstructions.trim()}
                      onClick={() => setAgentDraftReview(true)}
                    >
                      {t("team_surface.review_agent")}
                    </button>
                  </>
                ) : (
                  <div className="space-y-2.5">
                    <div className="text-[11px] font-semibold text-ink">{t("team_surface.review_agent")}</div>
                    <div className="rounded-xl bg-panel p-2.5 text-[11px] text-muted">
                      <div className="font-semibold text-ink">{agentName}</div>
                      <div className="text-[10px]">@{agentHandle} · {agentBaseRole}</div>
                      <p className="mt-2 whitespace-pre-wrap break-words text-ink/90">{agentInstructions}</p>
                    </div>
                    <div className="flex gap-2">
                      <button className="btn secondary flex-1 rounded-xl text-[12px]" onClick={() => setAgentDraftReview(false)}>
                        {t("team_surface.edit_agent")}
                      </button>
                      <button className="btn primary flex-1 rounded-xl text-[12px]" disabled={busy} onClick={() => void createAgent()}>
                        {t("team_surface.create_agent")}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Conversation List */}
            <div className="min-h-0 flex-1 overflow-y-auto pr-0.5">
              {loading && <div className="px-2 py-3 text-[12px] text-faint">{t("team_surface.loading")}</div>}
              {!loading && pinnedConversations.length > 0 && (
                <section className="mb-5" aria-label={t("sidebar.pinned")}>
                  <div className="mb-2 flex items-center gap-2 px-1 text-[12px] font-semibold text-ink">
                    <Icon name="chevronDown" size={13} className="text-muted" />
                    <span>{t("sidebar.pinned")}</span>
                    <span className="text-[11px] font-medium text-faint">{pinnedConversations.length}</span>
                  </div>
                  <div role="list" className="space-y-1">
                    {pinnedConversations.map(renderConversationRow)}
                  </div>
                </section>
              )}
              {!loading && (
                <section aria-label={t("team_surface.chats")}>
                  <div className="mb-2 flex items-center gap-2 px-1 text-[12px] font-semibold text-ink">
                    <Icon name="chevronDown" size={13} className="text-muted" />
                    <span>{t("team_surface.chats")}</span>
                    <span className="text-[11px] font-medium text-faint">{chatConversations.length}</span>
                    <button
                      className="ml-auto grid h-5 w-5 place-items-center rounded-md text-muted transition-colors hover:bg-panel hover:text-ink"
                      aria-label={t("team_surface.new_conversation")}
                      title={t("team_surface.new_conversation")}
                      onClick={openNewConversation}
                    >
                      <Icon name="plus" size={13} />
                    </button>
                  </div>
                  {chatConversations.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-line/70 bg-panel/50 px-3 py-5 text-center text-[12px] leading-relaxed text-muted">
                      {t(`team_surface.empty_${activeTab}`, { defaultValue: t("team_surface.empty") })}
                    </div>
                  ) : (
                    <div role="list" className="space-y-1">
                      {chatConversations.map(renderConversationRow)}
                    </div>
                  )}
                </section>
              )}
            </div>
          </div>
        )}
      </aside>
      <main className="team-main flex min-w-0 flex-1 flex-col bg-panel">
        {error && <div className="flex items-start gap-3 border-b border-line bg-dangerSoft px-5 py-2 text-[12px] text-danger" role="alert" aria-live="assertive"><span className="min-w-0 flex-1">{error}</span><button className="shrink-0 text-danger hover:underline" aria-label={t("common.close")} onClick={() => setError("")}>×</button></div>}
        {activeView === "agents" ? (
          <div className="team-agent-workspace min-h-0 flex-1 overflow-y-auto">
            <div className="team-agent-shell">
              <header className="team-agent-page-header">
                <div>
                  <div className="team-agent-eyebrow"><Icon name="users" size={14} />{t("team_surface.agent_workspace_label")}</div>
                  <h2>{t("team_surface.agent_detail")}</h2>
                  <p>{t("team_surface.agent_workspace_body")}</p>
                </div>
                <button
                  type="button"
                  className="team-agent-refresh"
                  onClick={() => {
                    void loadOverview(true);
                    if (selectedAgentId) void loadAgentProfile(selectedAgentId);
                  }}
                >
                  <Icon name="refresh" size={14} />
                  {t("team_surface.refresh_agent")}
                </button>
              </header>
              <div className="team-agent-layout">
                <aside className="team-agent-roster" aria-label={t("team_surface.agent_directory")}>
                  <div className="team-agent-roster-head">
                    <div>
                      <div className="team-agent-eyebrow">{t("team_surface.agent_directory")}</div>
                      <h3>{t("team_surface.agent_count", { count: agents.length })}</h3>
                    </div>
                    <button
                      type="button"
                      className="team-agent-icon-action"
                      aria-label={t("team_surface.new_agent")}
                      title={t("team_surface.new_agent")}
                      onClick={() => {
                        setActiveView("chat");
                        setAgentComposerOpen(true);
                        setAgentDraftReview(false);
                      }}
                    >
                      <Icon name="plus" size={15} />
                    </button>
                  </div>
                  <label className="team-agent-search">
                    <Icon name="search" size={14} />
                    <input
                      type="search"
                      value={agentSearch}
                      onChange={(event) => setAgentSearch(event.target.value)}
                      placeholder={t("team_surface.search_agents")}
                      aria-label={t("team_surface.search_agents")}
                    />
                  </label>
                  <div className="team-agent-roster-list">
                    {visibleAgents.map((agent) => {
                      const state = presenceByAgent.get(agent.agent_id)?.state || agent.status;
                      const isOnline = state === "ready" || state === "working" || state === "waiting";
                      return (
                        <button
                          key={agent.agent_id}
                          type="button"
                          className={"team-agent-row" + (selectedAgentId === agent.agent_id ? " is-selected" : "")}
                          aria-label={agent.display_name || agent.handle || agent.agent_id}
                          aria-current={selectedAgentId === agent.agent_id ? "true" : undefined}
                          aria-describedby={`team-agent-state-${agent.agent_id}`}
                          onClick={() => void loadAgentProfile(agent.agent_id)}
                        >
                          <AgentAvatar agent={agent} online={isOnline} className="h-9 w-9 rounded-xl" iconSize={16} />
                          <span className="team-agent-row-copy">
                            <span className="team-agent-row-name">{agent.display_name || agent.handle || agent.agent_id}</span>
                            <span className="team-agent-row-role">{agent.base_role || t("team_surface.unknown")}</span>
                          </span>
                          <span id={`team-agent-state-${agent.agent_id}`} className={"team-agent-row-state " + (isOnline ? "is-online" : "")}>{state}</span>
                        </button>
                      );
                    })}
                    {visibleAgents.length === 0 && (
                      <div className="team-agent-roster-empty">
                        {agents.length === 0 ? t("team_surface.agent_empty") : t("team_surface.agent_no_matches")}
                      </div>
                    )}
                  </div>
                </aside>
                <section className="team-agent-detail" aria-label={t("team_surface.agent_detail")}>
                  {agentProfileLoading ? <div className="team-agent-state-panel" aria-busy="true">{t("team_surface.agent_loading")}</div> : !selectedAgent || !agentProfile ? (
                    <div className="team-agent-state-panel">{t("team_surface.agent_empty")}</div>
                  ) : (
                    <>
                      <header className="team-agent-identity">
                        <div className="team-agent-avatar-control">
                          <button
                            type="button"
                            className="team-agent-avatar-trigger"
                            aria-label={t("team_surface.change_avatar")}
                            aria-haspopup="dialog"
                            aria-expanded={agentAvatarPickerOpen}
                            disabled={agentAvatarSaving}
                            onClick={() => setAgentAvatarPickerOpen((open) => !open)}
                          >
                            <AgentAvatar
                              agent={selectedAgent}
                              avatarId={selectedAgentAvatarId}
                              online={Boolean(selectedAgentPresence?.state === "ready" || selectedAgentPresence?.state === "working" || selectedAgent.status === "ready")}
                              className="h-16 w-16 rounded-2xl"
                              iconSize={26}
                            />
                            <span className="team-agent-avatar-edit" aria-hidden="true"><Icon name="pencil" size={13} /></span>
                          </button>
                          {agentAvatarPickerOpen && (
                            <div className="team-agent-avatar-picker" role="dialog" aria-label={t("team_surface.choose_bot_avatar")}>
                              <div className="team-agent-avatar-picker-header">
                                <strong>{t("team_surface.choose_bot_avatar")}</strong>
                                <button
                                  type="button"
                                  className="team-agent-avatar-picker-close"
                                  aria-label={t("common.close")}
                                  onClick={() => setAgentAvatarPickerOpen(false)}
                                >
                                  <Icon name="x" size={13} />
                                </button>
                              </div>
                              <div className="team-agent-avatar-grid" role="listbox" aria-label={t("team_surface.choose_bot_avatar")}>
                                {BOT_AVATAR_IDS.map((avatarId) => {
                                  const assetUrl = botAvatarAssetUrl(avatarId);
                                  if (!assetUrl) return null;
                                  const isSelected = selectedAgentAvatarId === avatarId;
                                  return (
                                    <button
                                      key={avatarId}
                                      type="button"
                                      role="option"
                                      aria-label={t("team_surface.bot_avatar_option", { number: avatarId })}
                                      aria-selected={isSelected}
                                      className={"team-agent-avatar-option" + (isSelected ? " is-selected" : "")}
                                      disabled={agentAvatarSaving}
                                      onClick={() => void saveAgentAvatar(avatarId)}
                                    >
                                      <img src={assetUrl} alt="" aria-hidden="true" />
                                      {isSelected && <span aria-hidden="true">✓</span>}
                                    </button>
                                  );
                                })}
                              </div>
                              <p className="team-agent-avatar-picker-help">{t("team_surface.bot_avatar_help")}</p>
                            </div>
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="team-agent-eyebrow">{selectedAgent.base_role || agentProfile.definition.base_role || t("team_surface.unknown")}</div>
                          <div className="team-agent-title-row">
                            <h3 id="team-agent-name">{selectedAgent.display_name || agentProfile.definition.display_name}</h3>
                            <span className="team-agent-status" data-status={selectedAgent.status}>
                              <span aria-hidden="true" />
                              {selectedAgent.status}
                            </span>
                          </div>
                          <div className="team-agent-handle">@{selectedAgent.handle || agentProfile.definition.handle}</div>
                          <p className="team-agent-description">{selectedAgent.description || agentProfile.definition.description || t("team_surface.no_description")}</p>
                        </div>
                        <div className="team-agent-primary-actions">
                          <button type="button" className="team-agent-primary-action" disabled={busy} onClick={openAgentConversation}>
                            <Icon name="send" size={14} />
                            {t("team_surface.agent_send_message")}
                          </button>
                        </div>
                      </header>
                      <div className="team-agent-action-bar" aria-label={t("team_surface.agent_actions")}>
                        <div className="team-agent-action-group">
                          {selectedAgent.status === "paused" ? <button type="button" className="team-agent-action team-agent-action-secondary" disabled={busy} onClick={() => void manageAgent("resume")}>{t("team_surface.resume_agent")}</button> : selectedAgent.status !== "archived" && <button type="button" className="team-agent-action team-agent-action-secondary" disabled={busy} onClick={() => void manageAgent("pause")}>{t("team_surface.pause_agent")}</button>}
                          {selectedAgent.status === "paused" || selectedAgent.status === "offline" || selectedAgent.status === "error" || selectedAgent.status === "archived" ? <button type="button" className="team-agent-action team-agent-action-secondary" disabled={busy} onClick={() => void manageAgent("restart")}>{t("team_surface.restart_agent")}</button> : <button type="button" className="team-agent-action team-agent-action-danger" disabled={busy} onClick={() => void manageAgent("stop")}>{t("team_surface.stop_agent")}</button>}
                        </div>
                        <div className="team-agent-action-group">
                          {selectedAgent.status !== "archived" && <button type="button" className="team-agent-action team-agent-action-secondary" disabled={busy} onClick={() => void manageAgent("reset")}>{t("team_surface.reset_sandbox")}</button>}
                          {selectedAgent.status !== "archived" && <button type="button" className="team-agent-action team-agent-action-secondary" disabled={busy} onClick={() => void manageAgent("archive")}>{t("team_surface.archive_agent")}</button>}
                          {selectedAgent.status === "archived" && <button type="button" className="team-agent-action team-agent-action-danger" disabled={busy} onClick={() => void manageAgent("delete")}>{t("team_surface.delete_agent")}</button>}
                        </div>
                        <div className="team-agent-action-group">
                          <button type="button" className="team-agent-action team-agent-action-secondary" disabled={busy} onClick={() => void manageAgent("clone")}>{t("team_surface.clone_agent")}</button>
                          <button type="button" className="team-agent-action team-agent-action-secondary" onClick={downloadAgentPackage}>{t("team_surface.export_agent")}</button>
                        </div>
                      </div>
                      <div className="team-agent-tabs" role="tablist" aria-label={t("team_surface.agent_detail")}>
                        {AGENT_PROFILE_TABS.map(({ value, labelKey }) => (
                          <button
                            key={value}
                            type="button"
                            role="tab"
                            aria-selected={agentProfileTab === value}
                            className={"team-agent-tab" + (agentProfileTab === value ? " is-selected" : "")}
                            onClick={() => setAgentProfileTab(value)}
                          >
                            {t(labelKey)}
                          </button>
                        ))}
                      </div>
                      {agentProfileTab === "overview" && (() => {
                        const capabilities = agentProfile.revision.capabilities;
                        const skillCount = capabilities.filter((capability) => String(capability.capability_kind || "").toLowerCase() === "skill").length;
                        const connectorCount = capabilities.filter((capability) => String(capability.capability_kind || "").toLowerCase() === "connector").length;
                        const sandboxState = sandboxHealth?.home_exists === true ? t("team_surface.healthy") : sandboxHealth ? t("team_surface.degraded") : t("team_surface.unknown");
                        const approvalRequired = agentProfile.revision.approval_policy.required === true;
                        return (
                          <div className="team-agent-overview">
                            <div className="team-agent-section-heading">
                              <div>
                                <h4>{t("team_surface.operational_snapshot")}</h4>
                                <p>{t("team_surface.agent_workspace_body")}</p>
                              </div>
                            </div>
                            <div className="team-agent-stat-grid">
                              <article className="team-agent-stat-card">
                                <span className="team-agent-stat-label">{t("team_surface.agent_status")}</span>
                                <strong>{selectedAgent.status}</strong>
                                <span>{selectedAgentPresence?.reason || t("team_surface.unknown")}</span>
                              </article>
                              <article className="team-agent-stat-card">
                                <span className="team-agent-stat-label">{t("team_surface.agent_sandbox_summary")}</span>
                                <strong>{agentProfile.sandbox.isolation_level}</strong>
                                <span>{sandboxState}</span>
                              </article>
                              <article className="team-agent-stat-card">
                                <span className="team-agent-stat-label">{t("team_surface.direct_messages")}</span>
                                <strong>{selectedAgentConversations.length}</strong>
                                <button type="button" className="team-agent-inline-link" disabled={selectedAgentConversations.length === 0} onClick={openAgentConversation}>{t("team_surface.open_chat")}</button>
                              </article>
                              <article className="team-agent-stat-card">
                                <span className="team-agent-stat-label">{t("team_surface.recent_runs")}</span>
                                <strong>{agentRuns.length}</strong>
                                <button type="button" className="team-agent-inline-link" onClick={() => setAgentProfileTab("runs")}>{t("team_surface.view_runs")}</button>
                              </article>
                            </div>
                            <div className="team-agent-card-grid">
                              <article className="team-agent-card">
                                <div className="team-agent-card-head">
                                  <span className="team-agent-card-icon"><Icon name="sparkle" size={14} /></span>
                                  <div>
                                    <h4>{t("team_surface.agent_capabilities_summary")}</h4>
                                    <p>{t("team_surface.configured_count", { count: capabilities.length })}</p>
                                  </div>
                                </div>
                                <div className="team-agent-metric-list">
                                  <div><span>{t("team_surface.agent_skills_tab")}</span><strong>{skillCount}</strong></div>
                                  <div><span>{t("team_surface.agent_connectors_tab")}</span><strong>{connectorCount}</strong></div>
                                  <div><span>{t("team_surface.max_concurrency")}</span><strong>{agentProfile.installation.max_concurrency}</strong></div>
                                </div>
                              </article>
                              <article className="team-agent-card">
                                <div className="team-agent-card-head">
                                  <span className="team-agent-card-icon"><Icon name="shield" size={14} /></span>
                                  <div>
                                    <h4>{t("team_surface.agent_approval_summary")}</h4>
                                    <p>{approvalRequired ? t("team_surface.approval_required") : t("team_surface.approval_not_required")}</p>
                                  </div>
                                </div>
                                <div className="team-agent-policy-value">{approvalRequired ? t("team_surface.approval_required") : t("team_surface.approval_not_required")}</div>
                                <button type="button" className="team-agent-inline-link" onClick={() => setAgentProfileTab("permissions")}>{t("team_surface.agent_permissions_tab")}</button>
                              </article>
                              <article className="team-agent-card team-agent-card-wide">
                                <div className="team-agent-card-head">
                                  <span className="team-agent-card-icon"><Icon name="book" size={14} /></span>
                                  <div className="min-w-0">
                                    <h4>{t("team_surface.agent_instructions_summary")}</h4>
                                    <p>{t("team_surface.agent_instructions_label")}</p>
                                  </div>
                                  <button type="button" className="team-agent-inline-link ml-auto" onClick={() => setAgentProfileTab("instructions")}>{t("team_surface.edit_instructions")}</button>
                                </div>
                                <p className="team-agent-instructions-preview">{agentProfile.revision.instructions || t("team_surface.no_configured")}</p>
                              </article>
                            </div>
                          </div>
                        );
                      })()}
                      {agentProfileTab === "instructions" && <div>
                        <label className="mb-2 block text-[11px] font-medium text-ink" htmlFor="team-agent-instructions">{t("team_surface.agent_instructions_label")}</label>
                        <textarea id="team-agent-instructions" className="min-h-48 w-full resize-y rounded-lg border border-line bg-panel px-3 py-2 text-[12px] leading-relaxed text-ink outline-none focus:border-accent" value={agentInstructionsDraft} onChange={(event) => setAgentInstructionsDraft(event.target.value)} />
                        <button className="btn primary mt-3 text-[11px]" disabled={agentSaveLoading || !agentInstructionsDraft.trim()} onClick={() => void saveAgentInstructions()}>{agentSaveLoading ? t("team_surface.saving") : t("team_surface.save_instructions")}</button>
                      </div>}
                      {agentProfileTab === "skills" && <div className="space-y-3">
                        <div className="text-[11px] text-muted">{t("team_surface.agent_skills_body")}</div>
                        {agentProfile.revision.capabilities.filter((capability) => String(capability.capability_kind || "").toLowerCase() === "skill").length === 0 ? <div className="rounded-lg bg-panel p-3 text-[11px] text-muted">{t("team_surface.no_configured")}</div> : <div className="space-y-1">{agentProfile.revision.capabilities.filter((capability) => String(capability.capability_kind || "").toLowerCase() === "skill").map((capability, index) => <div key={`skill-${index}`} className="rounded-lg bg-panel px-3 py-2 text-[11px] text-ink">{String(capability.capability_ref || t("team_surface.unknown"))}</div>)}</div>}
                      </div>}
                      {agentProfileTab === "connectors" && <div className="space-y-3">
                        <div className="text-[11px] text-muted">{t("team_surface.agent_connectors_body")}</div>
                        {agentProfile.revision.capabilities.filter((capability) => String(capability.capability_kind || "").toLowerCase() === "connector").length === 0 ? <div className="rounded-lg bg-panel p-3 text-[11px] text-muted">{t("team_surface.no_configured")}</div> : <div className="space-y-1">{agentProfile.revision.capabilities.filter((capability) => String(capability.capability_kind || "").toLowerCase() === "connector").map((capability, index) => <div key={`connector-${index}`} className="rounded-lg bg-panel px-3 py-2 text-[11px] text-ink">{String(capability.capability_ref || t("team_surface.unknown"))}</div>)}</div>}
                      </div>}
                      {agentProfileTab === "sandbox" && <div className="space-y-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                          <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.isolation")}</div><div className="mt-1 text-[12px] text-ink">{agentProfile.sandbox.isolation_level}</div></div>
                          <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.sandbox_health")}</div><div className="mt-1 text-[12px] text-ink">{String(sandboxHealth?.home_exists === true ? t("team_surface.healthy") : sandboxHealth ? t("team_surface.degraded") : t("team_surface.unknown"))}</div><div className="mt-1 text-[10px] text-muted">{String(sandboxHealth?.provider_kind || t("team_surface.unknown"))}</div></div>
                        </div>
                        <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.mounts")}</div>{agentProfile.sandbox.mounts.length === 0 ? <div className="mt-1 text-[11px] text-muted">{t("team_surface.no_mounts")}</div> : <div className="mt-2 space-y-1">{agentProfile.sandbox.mounts.map((mount) => <div key={`${mount.target_path}-${mount.access_mode}`} className="flex gap-2 text-[11px] text-ink"><code className="min-w-0 flex-1 truncate">{mount.target_path}</code><span className="text-muted">{mount.access_mode}</span></div>)}</div>}</div>
                        <div className="rounded-lg bg-panel p-3"><label className="block text-[10px] uppercase tracking-wide text-faint" htmlFor="team-playground-command">{t("team_surface.playground")}</label><div className="mt-2 flex gap-2"><input id="team-playground-command" className="min-w-0 flex-1 rounded-md border border-line bg-paper px-2 py-1.5 text-[11px] text-ink outline-none" value={playgroundCommand} onChange={(event) => setPlaygroundCommand(event.target.value)} /><button className="btn secondary shrink-0 text-[10px]" disabled={playgroundLoading || !playgroundCommand.trim()} onClick={() => void runAgentPlayground()}>{playgroundLoading ? t("team_surface.running") : t("team_surface.run_playground")}</button></div>{playgroundResult && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-paper p-2 text-[10px] text-muted">{playgroundResult.stdout || playgroundResult.stderr || `exit_code=${String(playgroundResult.exit_code ?? "unknown")}`}</pre>}</div>
                        <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.agent_sandbox")}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted">{JSON.stringify(agentProfile.revision.sandbox_policy, null, 2)}</pre></div>
                      </div>}
                      {agentProfileTab === "computer" && <div className="space-y-3">
                        <div className="text-[11px] text-muted">{t("team_surface.agent_computer_body")}</div>
                        {agentComputers.length === 0 ? <div className="rounded-lg bg-panel p-3 text-[11px] text-muted">{t("team_surface.no_computer")}</div> : <div className="space-y-2">
                          {agentComputers.map((computer) => <article key={computer.computer_id} className="rounded-lg bg-panel p-3">
                            <div className="flex flex-wrap items-start gap-3">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2"><code className="text-[10px] text-ink">{computer.computer_id}</code><span className="rounded-full bg-accentSoft px-2 py-0.5 text-[10px] text-accent">{computer.status}</span></div>
                                <div className="mt-2 grid gap-2 text-[10px] text-muted sm:grid-cols-3"><span>{t("team_surface.computer_provider")}: {computer.provider_kind}</span><span>{t("team_surface.computer_capabilities")}: {Object.keys(computer.capabilities || {}).length}</span><span>{t("team_surface.computer_updated")}: {timeLabel(computer.updated_at)}</span></div>
                              </div>
                            </div>
                          </article>)}
                        </div>}
                      </div>}
                      {agentProfileTab === "permissions" && <div className="space-y-3">
                        <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.model_policy")}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted">{JSON.stringify(agentProfile.revision.model_policy, null, 2)}</pre></div>
                        <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.capabilities")}</div>{agentProfile.revision.capabilities.length === 0 ? <div className="mt-1 text-[11px] text-muted">{t("team_surface.no_mounts")}</div> : <div className="mt-2 space-y-1">{agentProfile.revision.capabilities.map((capability, index) => <div key={`${String(capability.capability_kind || "capability")}-${index}`} className="rounded-md bg-paper px-2 py-1.5 text-[11px] text-ink">{String(capability.capability_kind || t("team_surface.unknown"))}: {String(capability.capability_ref || "")}</div>)}</div>}</div>
                        <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.agent_permissions")}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted">{JSON.stringify(agentProfile.revision.approval_policy, null, 2)}</pre></div>
                      </div>}
                      {agentProfileTab === "memory" && <div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.memory_policy")}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted">{JSON.stringify(agentProfile.revision.memory_policy, null, 2)}</pre></div>}
                      {agentProfileTab === "settings" && <div className="space-y-3"><div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.reply_policy")}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted">{JSON.stringify(agentProfile.revision.reply_policy, null, 2)}</pre></div><div className="rounded-lg bg-panel p-3"><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.budget_policy")}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted">{JSON.stringify(agentProfile.revision.budget_policy, null, 2)}</pre></div></div>}
                      {(agentProfileTab === "runs" || agentProfileTab === "activity") && <div className="space-y-3">
                        <div className="text-[11px] text-muted">{agentProfileTab === "runs" ? t("team_surface.recent_runs") : t("team_surface.agent_activity_body")}</div>
                        {agentRuns.length === 0 ? <div className="rounded-lg bg-panel p-3 text-[11px] text-muted">{t("team_surface.no_runs")}</div> : <div className="space-y-2">{agentRuns.map((run) => <article key={run.run_id} className="rounded-lg bg-panel p-3"><div className="flex items-start gap-3"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><code className="text-[10px] text-ink">{run.run_id}</code><span className="rounded-full bg-accentSoft px-2 py-0.5 text-[10px] text-accent">{run.status}</span></div><div className="mt-2 grid grid-cols-2 gap-2 text-[10px] text-muted sm:grid-cols-4"><span>{t("team_surface.tokens")}: {run.usage?.tokens ?? 0}</span><span>{t("team_surface.cost")}: {costLabel(run.usage?.cost_micros)}</span><span>{t("team_surface.tools")}: {run.usage?.tool_calls ?? 0}</span><span>{t("team_surface.duration")}: {durationLabel(run.started_at, run.finished_at)}</span></div></div><button className="text-[10px] text-accent hover:underline" onClick={() => void openRun(run.run_id)}>{t("team_surface.run_detail")}</button></div></article>)}</div>}
                        {runDetailLoading && <div className="text-[11px] text-faint">{t("team_surface.loading")}</div>}
                        {selectedRunDetail && <div className="rounded-lg border border-accent/30 bg-paper p-3"><div className="flex items-center gap-2"><code className="text-[10px] text-ink">{selectedRunDetail.run.run_id}</code><span className="text-[10px] text-muted">{selectedRunDetail.run.status}</span></div><div className="mt-2 grid grid-cols-2 gap-2 text-[10px] text-muted sm:grid-cols-4"><span>{t("team_surface.tokens")}: {selectedRunDetail.usage.tokens}</span><span>{t("team_surface.cost")}: {costLabel(selectedRunDetail.usage.cost_micros)}</span><span>{t("team_surface.tools")}: {selectedRunDetail.usage.tool_calls}</span><span>{t("team_surface.queue")}: {selectedRunDetail.run.status === "queued" ? t("team_surface.queued") : t("team_surface.not_queued")}</span></div><div className="mt-3 text-[10px] uppercase tracking-wide text-faint">{t("team_surface.run_timeline")}</div>{selectedRunDetail.events.length === 0 ? <div className="mt-1 text-[11px] text-muted">{t("team_surface.no_run_events")}</div> : <div className="mt-1 space-y-1">{selectedRunDetail.events.map((event) => <div key={event.run_event_id} className="flex gap-2 text-[10px] text-muted"><span className="shrink-0">{timeLabel(event.occurred_at)}</span><span>{event.event_type}</span></div>)}</div>}</div>}
                      </div>}
                    </>
                  )}
                </section>
              </div>
            </div>
          </div>
        ) : activeView === "files" ? (
          <div className="team-content-view min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-5xl">
              <header className="mb-5">
                <h2 className="text-[18px] font-semibold text-ink">{t("team_surface.files_title")}</h2>
                <p className="mt-1 text-[12px] text-muted">{t("team_surface.files_body")}</p>
                {artifactNotice && <div className="mt-2 text-[11px] text-accent" role="status">{artifactNotice}</div>}
              </header>
              {artifactLoading ? <div className="text-[12px] text-faint">{t("team_surface.loading")}</div> : artifacts.length === 0 ? <div className="rounded-xl border border-dashed border-line p-10 text-center text-[12px] text-muted">{t("team_surface.no_artifacts")}</div> : <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
                <div className="space-y-2">
                  {artifacts.map((artifact) => {
                    const revision = artifact.revisions[artifact.revisions.length - 1];
                    return <article key={artifact.artifact_id} className={"rounded-xl border bg-paper p-4 " + (selectedArtifactId === artifact.artifact_id ? "border-accent" : "border-line")}>
                      <button className="w-full text-left" onClick={() => void openArtifact(artifact)}>
                        <div className="flex items-start gap-3"><div className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-accentSoft text-accent"><Icon name="file" size={14} /></div><div className="min-w-0 flex-1"><h3 className="truncate text-[12px] font-semibold text-ink">{revision?.filename || artifact.artifact_id}</h3><div className="mt-1 text-[10px] text-muted">{revision?.media_type || t("team_surface.unknown")} · {revision?.size_bytes || 0} bytes · {artifact.status}</div></div>{artifact.quarantined_at && <span className="rounded-full bg-dangerSoft px-2 py-1 text-[10px] text-danger">{t("team_surface.quarantine_artifact")}</span>}</div>
                      </button>
                      <div className="mt-3 flex flex-wrap gap-2"><button className="text-[10px] text-accent hover:underline" onClick={() => void openArtifact(artifact)}>{t("team_surface.artifact_preview")}</button><button className="text-[10px] text-accent hover:underline" disabled={artifactDownloadLoading || Boolean(artifact.quarantined_at)} onClick={() => void downloadArtifact(artifact)}>{t("team_surface.download_artifact")}</button><button className="text-[10px] text-muted hover:text-ink" onClick={() => void copyArtifactLink(artifact.artifact_id)}>{t("team_surface.copy_artifact_link")}</button>{artifact.quarantined_at ? <button className="text-[10px] text-muted hover:text-ink" disabled={busy} onClick={() => void manageArtifact(artifact, "release")}>{t("team_surface.release_quarantine")}</button> : <button className="text-[10px] text-muted hover:text-ink" disabled={busy} onClick={() => void manageArtifact(artifact, "quarantine")}>{t("team_surface.quarantine_artifact")}</button>}<button className="text-[10px] text-muted hover:text-ink" disabled={busy} onClick={() => void manageArtifact(artifact, "archive")}>{t("team_surface.archive_artifact")}</button><button className="text-[10px] text-danger hover:underline" disabled={busy} onClick={() => void manageArtifact(artifact, "delete")}>{t("team_surface.delete_artifact")}</button></div>
                    </article>;
                  })}
                </div>
                <aside className="h-fit rounded-xl border border-line bg-paper p-4">
                  {!selectedArtifact ? <div className="text-[12px] text-muted">{t("team_surface.artifact_preview")}</div> : <><h3 className="text-[13px] font-semibold text-ink">{t("team_surface.artifact_preview")}</h3><div className="mt-3 space-y-3 text-[11px]"><div><div className="text-[10px] uppercase tracking-wide text-faint">{selectedArtifact.revisions[selectedArtifact.revisions.length - 1]?.filename || selectedArtifact.artifact_id}</div><div className="mt-1 text-muted">{selectedArtifact.revisions.length} {t("team_surface.artifact_revisions", { count: selectedArtifact.revisions.length })}</div></div><div className="flex flex-wrap gap-2"><button className="btn secondary text-[10px]" disabled={artifactDownloadLoading || Boolean(selectedArtifact.quarantined_at)} onClick={() => void downloadArtifact(selectedArtifact)}>{t("team_surface.download_artifact")}</button><button className="btn secondary text-[10px]" disabled={artifactDownloadLoading || Boolean(selectedArtifact.quarantined_at)} onClick={() => void downloadArtifact(selectedArtifact, true)}>{t("team_surface.reveal_artifact")}</button><button className="btn secondary text-[10px]" onClick={() => void copyArtifactLink(selectedArtifact.artifact_id)}>{t("team_surface.copy_artifact_link")}</button></div>{typeof artifactPreview?.preview === "string" ? <p className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-panel p-3 text-ink">{artifactPreview.preview}</p> : <div className="rounded-lg bg-panel p-3 text-muted">{t("team_surface.artifact_no_preview")}</div>}<div><div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.artifact_provenance")}</div><div className="mt-1 break-words text-muted">{selectedArtifact.links.length} linked records · {selectedArtifact.revisions[selectedArtifact.revisions.length - 1]?.source_ref_kind || t("team_surface.unknown")}</div>{selectedArtifact.revisions[selectedArtifact.revisions.length - 1]?.source_run_id && <div className="mt-2 flex items-center gap-2 text-[10px] text-muted"><span className="min-w-0 flex-1 truncate">run: {selectedArtifact.revisions[selectedArtifact.revisions.length - 1]?.source_run_id}</span><button className="text-accent hover:underline" onClick={() => { setActiveView("tasks"); void openRun(selectedArtifact.revisions[selectedArtifact.revisions.length - 1]!.source_run_id); }}>{t("team_surface.open")}</button></div>}{selectedArtifact.links.length > 0 && <div className="mt-2 space-y-1">{selectedArtifact.links.map((link) => <div key={`${link.linked_aggregate_type}-${link.linked_aggregate_id}-${link.artifact_revision_id}`} className="flex items-center gap-2 text-[10px] text-muted"><span className="min-w-0 flex-1 truncate">{link.linked_aggregate_type}: {link.linked_aggregate_id}</span>{link.linked_aggregate_type === "task" && <button className="text-accent hover:underline" onClick={() => { setActiveView("tasks"); setSelectedTaskId(link.linked_aggregate_id); void loadTaskDetail(link.linked_aggregate_id); }}>{t("team_surface.open")}</button>}{link.linked_aggregate_type === "run" && <button className="text-accent hover:underline" onClick={() => { setActiveView("tasks"); void openRun(link.linked_aggregate_id); }}>{t("team_surface.open")}</button>}</div>)}</div>}</div></div></>}
                </aside>
              </div>}
            </div>
          </div>
        ) : activeView === "computer" ? (
          <div className="team-content-view min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-5xl">
              <header className="mb-5">
                <h2 className="text-[18px] font-semibold text-ink">{t("team_surface.computer_title")}</h2>
                <p className="mt-1 text-[12px] text-muted">{t("team_surface.computer_body")}</p>
              </header>
              <TeamComputerControl />
            </div>
          </div>
        ) : activeView === "tasks" ? (
          <div className="team-content-view min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-4xl">
              <header className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-[18px] font-semibold text-ink">{t("team_surface.tasks_title")}</h2>
                  <p className="mt-1 text-[12px] text-muted">{t("team_surface.tasks_body")}</p>
                </div>
              </header>
              <div className="mb-5 rounded-xl border border-line bg-paper p-4">
                <div className="mb-3 text-[12px] font-semibold text-ink">{t("team_surface.new_task")}</div>
                <div className="grid gap-2 md:grid-cols-2">
                  <input className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none" placeholder={t("team_surface.task_title")} value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} />
                  <select className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none" value={taskOwner} onChange={(event) => setTaskOwner(event.target.value)}>
                    <option value="">{t("team_surface.unassigned")}</option>
                    {agents.map((agent) => <option key={agent.agent_id} value={agent.agent_id}>{agent.display_name || agent.handle || agent.agent_id}</option>)}
                  </select>
                  <select className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none" value={taskParentId} onChange={(event) => setTaskParentId(event.target.value)}>
                    <option value="">{t("team_surface.no_parent_task")}</option>
                    {tasks.map((item) => <option key={item.task_id} value={item.task_id}>{item.title}</option>)}
                  </select>
                  <select className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none" value={taskPriority} onChange={(event) => setTaskPriority(Number(event.target.value))}>
                    <option value={0}>{t("team_surface.priority_urgent")}</option>
                    <option value={1}>{t("team_surface.priority_high")}</option>
                    <option value={2}>{t("team_surface.priority_medium")}</option>
                    <option value={3}>{t("team_surface.priority_low")}</option>
                  </select>
                  <input type="datetime-local" className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none" placeholder={t("team_surface.due_date")} value={taskDueAt} onChange={(event) => setTaskDueAt(event.target.value)} />
                  <select className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none" value={taskReviewer} onChange={(event) => setTaskReviewer(event.target.value)}>
                    <option value="">{t("team_surface.reviewer_default")}</option>
                    {agents.map((agent) => <option key={agent.agent_id} value={agent.actor_id || agent.agent_id}>{agent.display_name || agent.handle || agent.agent_id}</option>)}
                  </select>
                  <input className="rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none md:col-span-2" placeholder={t("team_surface.labels_placeholder")} value={taskLabelsInput} onChange={(event) => setTaskLabelsInput(event.target.value)} />
                  <textarea className="min-h-16 rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none md:col-span-2" placeholder={t("team_surface.task_description")} value={taskDescription} onChange={(event) => setTaskDescription(event.target.value)} />
                  <textarea className="min-h-16 rounded-md border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none md:col-span-2" placeholder={t("team_surface.task_criteria")} value={taskCriteria} onChange={(event) => setTaskCriteria(event.target.value)} />
                </div>
                <button className="btn primary mt-3" disabled={busy || !taskTitle.trim() || !taskCriteria.trim()} onClick={() => void createTask()}>{t("team_surface.create_task")}</button>
              </div>
              {taskLoading ? <div className="text-[12px] text-faint">{t("team_surface.loading")}</div> : tasks.length === 0 ? <div className="rounded-xl border border-dashed border-line p-8 text-center text-[12px] text-muted">{t("team_surface.no_tasks")}</div> : (
                <div className="space-y-2">
                  {tasks.map((task) => {
                    const canAdvance = task.status === "open" ? Boolean(task.owner_agent_id) : ["in_progress", "review"].includes(task.status);
                    const nextLabel = task.status === "open" ? t("team_surface.dispatch_task") : task.status === "in_progress" ? t("team_surface.send_to_review") : t("team_surface.approve_task");
                    const pMeta = priorityMeta(task.priority);
                    const dMeta = deadlineMeta(task.due_at);
                    const isSubtask = Boolean(task.parent_task_id);
                    const subtaskCount = tasks.filter((sub) => sub.parent_task_id === task.task_id).length;
                    return <article key={task.task_id} className={"rounded-xl border bg-paper p-4 " + (isSubtask ? "ml-6 border-line/60 bg-paper/60" : "border-line")}>
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-[13px] font-semibold text-ink">{task.title}</h3>
                            <span className="rounded-full bg-accentSoft px-2 py-0.5 text-[10px] text-accent">{task.status}</span>
                            <span className={"rounded-full border px-2 py-0.5 text-[10px] font-medium " + pMeta.className}>{t(pMeta.labelKey)}</span>
                          {dMeta && <span className={"rounded-full border px-2 py-0.5 text-[10px] font-medium " + (dMeta.isOverdue ? "border-danger/30 bg-dangerSoft text-danger" : "border-line bg-panel text-muted")}>{dMeta.label}</span>}
                            {task.parent_task_id && <span className="rounded-full bg-panel px-2 py-0.5 text-[10px] text-faint">subtask of #{task.parent_task_id.slice(-6)}</span>}
                            {subtaskCount > 0 && <span className="rounded-full bg-accentSoft px-2 py-0.5 text-[10px] text-accent">{subtaskCount} subtasks</span>}
                            {task.labels?.map((label) => <span key={label} className="rounded-full bg-panel px-2 py-0.5 text-[10px] text-muted">{label}</span>)}
                          </div>
                          {task.description && <p className="mt-2 text-[12px] leading-relaxed text-muted">{task.description}</p>}
                          <div className="mt-2 text-[11px] text-faint">{t("team_surface.criteria")}: {task.acceptance_criteria}</div>
                          {task.runs?.[0] && <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted"><span>{t("team_surface.latest_run")}: {task.runs[0].status}</span><span>{t("team_surface.tokens")}: {task.runs[0].usage?.tokens ?? 0}</span><span>{t("team_surface.cost")}: {costLabel(task.runs[0].usage?.cost_micros)}</span><span>{t("team_surface.queue")}: {task.runs[0].status === "queued" ? t("team_surface.queued") : t("team_surface.not_queued")}</span></div>}
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          <button className="text-[10px] text-accent hover:underline" onClick={() => void loadTaskDetail(task.task_id)}>{t("team_surface.task_detail")}</button>
                          {canAdvance && <button className="btn secondary text-[11px]" disabled={busy} onClick={() => void advanceTask(task)}>{nextLabel}</button>}
                          {task.status === "review" && <button className="text-[10px] text-danger hover:underline" disabled={busy} onClick={() => void requestTaskChanges(task)}>{t("team_surface.request_changes")}</button>}
                          {task.status !== "canceled" && task.status !== "done" && (
                            <button className="text-[10px] text-faint hover:text-danger hover:underline" disabled={busy} onClick={() => void cancelSelectedTask(task.task_id)}>{t("team_surface.cancel_task")}</button>
                          )}
                          {task.status === "canceled" && (
                            <button className="text-[10px] text-accent hover:underline" disabled={busy} onClick={() => void reopenSelectedTask(task.task_id)}>{t("team_surface.reopen_task")}</button>
                          )}
                          {task.status === "open" && !task.owner_agent_id && <span className="text-[10px] text-faint">{t("team_surface.assign_before_dispatch")}</span>}
                        </div>
                      </div>
                    </article>;
                  })}
                </div>
              )}
              {taskDetail && <aside className="mt-4 rounded-xl border border-accent/40 bg-paper p-4" aria-label={t("team_surface.task_detail")}>
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-[14px] font-semibold text-ink">{taskDetail.title}</h3>
                      <span className="rounded-full bg-accentSoft px-2 py-0.5 text-[10px] text-accent">{taskDetail.status}</span>
                      <span className={"rounded-full border px-2 py-0.5 text-[10px] font-medium " + priorityMeta(taskDetail.priority).className}>{t(priorityMeta(taskDetail.priority).labelKey)}</span>
                      {deadlineMeta(taskDetail.due_at) && <span className={"rounded-full border px-2 py-0.5 text-[10px] font-medium " + (deadlineMeta(taskDetail.due_at)!.isOverdue ? "border-danger/30 bg-dangerSoft text-danger" : "border-line bg-panel text-muted")}>{deadlineMeta(taskDetail.due_at)!.label}</span>}
                    </div>
                  </div>
                  <button className="text-[11px] text-muted hover:text-ink" onClick={() => { setSelectedTaskId(null); setTaskDetail(null); }} aria-label={t("common.close")}>×</button>
                </div>
                {taskDetailLoading ? <div className="mt-3 text-[11px] text-faint">{t("team_surface.loading")}</div> : (
                  <div className="mt-3 space-y-3">
                    <nav className="flex gap-1 border-b border-line pb-2" aria-label="Task Detail Tabs">
                      {(["overview", "thread", "verifications", "events", "handoffs", "runs"] as const).map((tab) => (
                        <button
                          key={tab}
                          className={"rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors " + (taskDetailTab === tab ? "bg-accentSoft text-accent" : "text-muted hover:bg-panel hover:text-ink")}
                          onClick={() => setTaskDetailTab(tab)}
                        >
                          {t(`team_surface.task_tabs.${tab}`)}
                        </button>
                      ))}
                    </nav>

                    {taskDetailTab === "overview" && (
                      <div className="space-y-3">
                        <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-ink">{taskDetail.description || t("team_surface.no_description")}</p>
                        <div className="rounded-lg bg-panel p-3">
                          <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.criteria")}</div>
                          <p className="mt-1 whitespace-pre-wrap text-[11px] text-ink">{taskDetail.acceptance_criteria}</p>
                        </div>
                        <div className="grid gap-2 md:grid-cols-2">
                          <label className="text-[11px] text-muted">
                            {t("team_surface.assigned_to")}
                            <select className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-[11px] text-ink outline-none" value={taskAssignmentDraft} onChange={(event) => setTaskAssignmentDraft(event.target.value)}>
                              <option value="">{t("team_surface.unassigned")}</option>
                              {agents.map((agent) => <option key={agent.agent_id} value={agent.agent_id}>{agent.display_name || agent.handle || agent.agent_id}</option>)}
                            </select>
                            <button className="mt-1 text-[10px] text-accent hover:underline" disabled={busy || !taskAssignmentDraft} onClick={() => void assignSelectedTask()}>{t("team_surface.assign_task")}</button>
                          </label>
                          <label className="text-[11px] text-muted">
                            {t("team_surface.add_dependency")}
                            <select className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-[11px] text-ink outline-none" value={taskDependencyDraft} onChange={(event) => setTaskDependencyDraft(event.target.value)}>
                              <option value="">{t("team_surface.select_task")}</option>
                              {tasks.filter((item) => item.task_id !== taskDetail.task_id).map((item) => <option key={item.task_id} value={item.task_id}>{item.title}</option>)}
                            </select>
                            <button className="mt-1 text-[10px] text-accent hover:underline" disabled={busy || !taskDependencyDraft} onClick={() => void addSelectedTaskDependency()}>{t("team_surface.add_dependency")}</button>
                          </label>
                        </div>

                        {/* Labels Section */}
                        <div className="rounded-lg bg-panel p-3">
                          <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.labels")}</div>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {taskDetail.labels?.map((label) => (
                              <span key={label} className="inline-flex items-center gap-1 rounded-full border border-line bg-paper px-2 py-0.5 text-[10px] text-ink">
                                {label}
                                <button className="text-muted hover:text-danger" onClick={() => void removeSelectedTaskLabel(label)}>×</button>
                              </span>
                            ))}
                          </div>
                          <div className="mt-2 flex gap-2">
                            <input className="min-w-0 flex-1 rounded-md border border-line bg-paper px-2 py-1 text-[11px] text-ink outline-none" placeholder={t("team_surface.add_label_placeholder")} value={taskLabelDraft} onChange={(e) => setTaskLabelDraft(e.target.value)} />
                            <button className="btn secondary text-[10px]" disabled={busy || !taskLabelDraft.trim()} onClick={() => void addSelectedTaskLabel()}>{t("team_surface.add")}</button>
                          </div>
                        </div>

                        {/* Watchers Section */}
                        <div className="rounded-lg bg-panel p-3">
                          <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.watchers")}</div>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {taskDetail.watchers?.map((watcher) => (
                              <span key={watcher.actor_id} className="inline-flex items-center gap-1 rounded-full border border-line bg-paper px-2 py-0.5 text-[10px] text-ink">
                                {watcher.display_name || watcher.handle || watcher.actor_id}
                                <button className="text-muted hover:text-danger" onClick={() => void removeSelectedTaskWatcher(watcher.actor_id)}>×</button>
                              </span>
                            ))}
                          </div>
                          <div className="mt-2 flex gap-2">
                            <select className="min-w-0 flex-1 rounded-md border border-line bg-paper px-2 py-1 text-[11px] text-ink outline-none" value={taskWatcherDraft} onChange={(e) => setTaskWatcherDraft(e.target.value)}>
                              <option value="">{t("team_surface.select_watcher")}</option>
                              {agents.map((agent) => <option key={agent.agent_id} value={agent.actor_id || agent.agent_id}>{agent.display_name || agent.handle || agent.agent_id}</option>)}
                            </select>
                            <button className="btn secondary text-[10px]" disabled={busy || !taskWatcherDraft} onClick={() => void addSelectedTaskWatcher()}>{t("team_surface.add_watcher")}</button>
                          </div>
                        </div>

                        <div>
                          <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.dependencies")}</div>
                          {taskDetail.dependencies?.length ? (
                            <div className="mt-1 space-y-1">
                              {taskDetail.dependencies.map((dependency) => (
                                <div key={`${dependency.task_id}-${dependency.depends_on_task_id}`} className="text-[11px] text-muted">
                                  {dependency.depends_on_task_id} · {dependency.status}
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="mt-1 text-[11px] text-muted">{t("team_surface.no_dependencies")}</div>
                          )}
                        </div>
                      </div>
                    )}

                    {taskDetailTab === "thread" && (
                      <div className="rounded-lg bg-panel p-3" aria-label={t("team_surface.task_thread")}>
                        <div className="flex items-center gap-2">
                          <div className="min-w-0 flex-1 text-[10px] uppercase tracking-wide text-faint">{t("team_surface.task_thread")}</div>
                          <button className="text-[10px] text-accent hover:underline" disabled={taskThreadLoading} onClick={() => void refreshTaskThread()}>{t("team_surface.refresh")}</button>
                        </div>
                        {taskThreadLoading ? <div className="mt-2 text-[11px] text-faint">{t("team_surface.loading")}</div> : !taskThread ? <div className="mt-2 text-[11px] text-muted">{t("team_surface.no_task_thread")}</div> : <>
                          <div className="mt-2 max-h-56 space-y-2 overflow-y-auto">
                            {taskThread.messages.length === 0 ? <div className="text-[11px] text-muted">{t("team_surface.no_task_messages")}</div> : taskThread.messages.map((message) => <article key={message.message_id} className="rounded-md border border-line bg-paper p-2">
                              <div className="flex items-center gap-2 text-[10px] text-muted"><span className="font-medium text-ink">{message.author_actor_id}</span><time>{timeLabel(message.created_at)}</time></div>
                              <p className="mt-1 whitespace-pre-wrap text-[11px] text-ink">{message.body}</p>
                            </article>)}
                          </div>
                          <div className="mt-2 flex gap-2">
                            <textarea className="min-h-10 min-w-0 flex-1 rounded-md border border-line bg-paper px-2 py-1.5 text-[11px] text-ink outline-none" aria-label={t("team_surface.task_thread_reply")} placeholder={t("team_surface.task_thread_reply_placeholder")} value={taskThreadReply} onChange={(event) => setTaskThreadReply(event.target.value)} />
                            <button className="btn secondary self-end text-[10px]" disabled={taskThreadReplyLoading || !taskThreadReply.trim()} onClick={() => void sendTaskThreadReply()}>{taskThreadReplyLoading ? t("team_surface.sending") : t("team_surface.reply_in_thread")}</button>
                          </div>
                        </>}
                      </div>
                    )}

                    {taskDetailTab === "verifications" && (
                      <div className="space-y-2">
                        <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.verification_attempts")}</div>
                        {taskVerifications.length === 0 ? (
                          <div className="rounded-lg bg-panel p-4 text-[11px] text-muted">{t("team_surface.no_verifications")}</div>
                        ) : (
                          taskVerifications.map((v) => (
                            <article key={v.attempt_id} className="rounded-lg border border-line bg-panel p-3">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                  <span className={"rounded-full px-2 py-0.5 text-[10px] font-semibold " + (v.passed ? "bg-successSoft text-success" : "bg-dangerSoft text-danger")}>
                                    {v.passed ? "PASSED" : "FAILED"}
                                  </span>
                                  <code className="text-[10px] text-muted">{v.attempt_id.slice(0, 12)}</code>
                                </div>
                                <time className="text-[10px] text-faint">{timeLabel(v.created_at)}</time>
                              </div>
                              <div className="mt-2 text-[11px] text-ink">{v.feedback || t("team_surface.no_verification_feedback")}</div>
                              {Object.keys(v.evidence || {}).length > 0 && (
                                <pre className="mt-2 max-h-32 overflow-auto rounded bg-paper p-2 text-[10px] text-muted">{JSON.stringify(v.evidence, null, 2)}</pre>
                              )}
                            </article>
                          ))
                        )}
                      </div>
                    )}

                    {taskDetailTab === "events" && (
                      <div className="space-y-2">
                        <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.task_events")}</div>
                        {taskEvents.length === 0 ? (
                          <div className="rounded-lg bg-panel p-4 text-[11px] text-muted">{t("team_surface.no_events")}</div>
                        ) : (
                          taskEvents.map((evt) => (
                            <div key={evt.event_id} className="flex items-start gap-2.5 rounded-lg bg-panel p-2.5 text-[11px]">
                              <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center justify-between">
                                  <span className="font-semibold text-ink">{evt.event_type}</span>
                                  <time className="text-[10px] text-faint">{timeLabel(evt.occurred_at)}</time>
                                </div>
                                <div className="mt-0.5 text-[10px] text-muted">by {evt.actor_id}</div>
                                {evt.payload && Object.keys(evt.payload).length > 0 && (
                                  <pre className="mt-1 max-h-24 overflow-auto rounded bg-paper p-1.5 text-[9px] text-muted">{JSON.stringify(evt.payload, null, 2)}</pre>
                                )}
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    )}

                    {taskDetailTab === "handoffs" && (
                      <div className="space-y-2">
                        <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.task_handoffs")}</div>
                        {taskHandoffs.length === 0 ? (
                          <div className="rounded-lg bg-panel p-4 text-[11px] text-muted">{t("team_surface.no_handoffs")}</div>
                        ) : (
                          taskHandoffs.map((h) => (
                            <article key={h.handoff_id} className="rounded-lg border border-line bg-panel p-3">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                  <span className="rounded-full bg-accentSoft px-2 py-0.5 text-[10px] text-accent">{h.status}</span>
                                  <span className="text-[11px] font-semibold text-ink">{h.sender_agent_id} → {h.recipient_agent_id}</span>
                                </div>
                                <time className="text-[10px] text-faint">{timeLabel(h.created_at)}</time>
                              </div>
                              <div className="mt-2 text-[11px] text-muted">{h.purpose}</div>
                            </article>
                          ))
                        )}
                      </div>
                    )}

                    {taskDetailTab === "runs" && (
                      <div className="space-y-2">
                        <div className="text-[10px] uppercase tracking-wide text-faint">{t("team_surface.run_timeline")}</div>
                        {taskDetail.runs?.length ? (
                          <div className="space-y-1">
                            {taskDetail.runs.map((run) => (
                              <div key={run.run_id} className="flex items-center gap-2 rounded-lg bg-panel p-2.5 text-[11px] text-muted">
                                <code className="min-w-0 flex-1 truncate">{run.run_id}</code>
                                <span>{run.status}</span>
                                <span>{costLabel(run.usage?.cost_micros)}</span>
                                <button className="text-accent hover:underline" onClick={() => void openRun(run.run_id)}>{t("team_surface.run_detail")}</button>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="rounded-lg bg-panel p-4 text-[11px] text-muted">{t("team_surface.no_runs")}</div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </aside>}
              {selectedRunDetail && <aside className="mt-4 rounded-xl border border-line bg-paper p-4" aria-label={t("team_surface.run_detail")}>
                <div className="flex items-start gap-3"><div className="min-w-0 flex-1"><h3 className="text-[13px] font-semibold text-ink">{t("team_surface.run_detail")}</h3><div className="mt-1 text-[10px] text-muted"><code>{selectedRunDetail.run.run_id}</code> · {selectedRunDetail.run.status}</div></div><button className="text-[11px] text-muted hover:text-ink" onClick={() => setSelectedRunDetail(null)} aria-label={t("common.close")}>×</button></div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] text-muted sm:grid-cols-5"><span>{t("team_surface.tokens")}: {selectedRunDetail.usage.tokens}</span><span>{t("team_surface.cost")}: {costLabel(selectedRunDetail.usage.cost_micros)}</span><span>{t("team_surface.tools")}: {selectedRunDetail.usage.tool_calls}</span><span>{t("team_surface.duration")}: {durationLabel(selectedRunDetail.run.started_at, selectedRunDetail.run.finished_at)}</span><span>{t("team_surface.events")}: {selectedRunDetail.events.length}</span></div>
                <div className="mt-3 space-y-1">{selectedRunDetail.events.length === 0 ? <div className="text-[11px] text-muted">{t("team_surface.no_run_events")}</div> : selectedRunDetail.events.map((event) => <div key={event.run_event_id} className="flex gap-2 text-[10px] text-muted"><span className="shrink-0">{timeLabel(event.occurred_at)}</span><span>{event.event_type}</span></div>)}</div>
              </aside>}
            </div>
          </div>
        ) : activeView === "activity" ? (
          <div className="team-content-view min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto max-w-4xl">
              <header className="mb-5">
                <h2 className="text-[18px] font-semibold text-ink">{t("team_surface.activity_title")}</h2>
                <p className="mt-1 text-[12px] text-muted">{t("team_surface.activity_body")}</p>
              </header>
              {activityLoading ? <div className="text-[12px] text-faint">{t("team_surface.loading")}</div> : activities.length === 0 ? (
                <div className="rounded-xl border border-dashed border-line p-8 text-center text-[12px] text-muted">{t("team_surface.no_activity")}</div>
              ) : (
                <div className="space-y-2">
                  {activities.map((item) => {
                    const conversationId = String(item.payload?.conversation_id || "");
                    return <article key={item.activity_id} className="rounded-xl border border-line bg-paper p-4">
                      <div className="flex items-start gap-3">
                        <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-[12px] font-semibold text-ink">{item.activity_kind}</h3>
                            <span className="text-[10px] text-faint">{item.aggregate_type}:{item.aggregate_id}</span>
                          </div>
                          <time className="mt-1 block text-[11px] text-muted">{timeLabel(item.occurred_at)}</time>
                        </div>
                        {conversationId && <button className="btn secondary shrink-0 text-[11px]" onClick={() => {
                          setActiveView("chat");
                          setSelectedId(conversationId);
                          setFocusMessageId(item.aggregate_type === "message" ? item.aggregate_id : null);
                        }}>{t("team_surface.open_activity")}</button>}
                      </div>
                    </article>;
                  })}
                </div>
              )}
            </div>
          </div>
        ) : !conversation ? (
          <div className="grid flex-1 place-items-center px-8 text-center">
            <div className="max-w-sm">
              <div className="mb-2 text-[15px] font-semibold text-ink">{t("team_surface.home_title")}</div>
              <p className="text-[13px] leading-relaxed text-muted">{t("team_surface.home_body")}</p>
            </div>
          </div>
        ) : (
          <>
            <header className="team-channel-header bg-panel px-6 py-2.5">
              <div className="team-channel-header-row flex items-center gap-3">
                {conversationAgent ? (
                  <AgentAvatar
                    agent={conversationAgent}
                    online={Boolean(presenceByAgent.get(conversationAgent.agent_id)?.state === "ready" || conversationAgent.status === "ready")}
                    className="h-9 w-9 rounded-lg"
                    iconSize={16}
                  />
                ) : (
                  <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accentSoft text-[16px] font-semibold text-accent shadow-2xs">
                    {conversation.conversation_kind === "direct" ? "@" : "#"}
                  </div>
                )}
                <div className="min-w-0">
                  <div className="flex items-center gap-2.5">
                    <h2 className="truncate text-[16px] font-semibold tracking-tight text-ink">{conversationLabel(conversation, agents)}</h2>
                    {conversation.conversation_kind === "direct" && <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted">{t("team_surface.coordinator")}</span>}
                    <nav className="ml-3 hidden items-center gap-1 border-l border-line pl-3 sm:flex" aria-label={t("team_surface.views_label")}>
                      <button
                        className="rounded-lg bg-paper px-2.5 py-1.5 text-[11px] font-semibold text-ink transition-colors"
                        onClick={() => setActiveView("chat")}
                        aria-current="page"
                      >
                        {t("team_surface.views.chat")}
                      </button>
                      <button
                        className="rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted transition-colors hover:bg-paper hover:text-ink"
                        onClick={() => setActiveView("files")}
                      >
                        {t("team_surface.views.files")}
                      </button>
                      <button
                        className="rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-muted transition-colors hover:bg-paper hover:text-ink"
                        onClick={() => setActiveView("tasks")}
                      >
                        {t("team_surface.views.tasks")}
                        {tasks.length > 0 && <span className="ml-1 rounded-full bg-paper px-1.5 py-0.5 text-[9px] text-muted">{tasks.length}</span>}
                      </button>
                    </nav>
                  </div>
                  <div className="mt-0.5 text-[11px] text-muted">{t("team_surface.member_count", { count: conversation.members?.length || 0 })}</div>
                </div>
                <div className="ml-auto flex items-center gap-1">
                  <button className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-paper hover:text-ink" aria-label={t("team_surface.thread")} title={t("team_surface.thread")} onClick={() => messages[0] && void openMessageThread(messages[0].message_id)}>
                    <Icon name="chat" size={16} />
                  </button>
                  <button className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-paper hover:text-ink" aria-label={t("sidebar.search")} title={t("sidebar.search")} onClick={() => chatSearchInputRef.current?.focus()}>
                    <Icon name="search" size={16} />
                  </button>
                  {conversation.conversation_kind === "group" && (
                    <button className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-paper hover:text-ink" aria-label={t("team_surface.group_settings")} title={t("team_surface.group_settings")} onClick={() => setGroupSettingsOpen((value) => !value)}>
                      <Icon name="users" size={16} />
                    </button>
                  )}
                  <button className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-paper hover:text-ink" aria-label={contextRailOpen ? t("topbar.hide_side_panel") : t("topbar.show_side_panel")} title={contextRailOpen ? t("topbar.hide_side_panel") : t("topbar.show_side_panel")} onClick={() => setContextRailOpen((value) => !value)}>
                    <Icon name="sidebarRight" size={16} />
                  </button>
                  <button className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-paper hover:text-ink" aria-label={t("team_surface.notifications")} title={t("team_surface.notifications")} onClick={() => setNotificationsOpen((value) => !value)}>
                    <Icon name="moreHorizontal" size={16} />
                  </button>
                </div>
              </div>
            </header>
            {groupSettingsOpen && conversation.conversation_kind === "group" && <section className="border-b border-line bg-paper/50 px-6 py-4" aria-label={t("team_surface.group_settings")}>
              <div className="mx-auto max-w-3xl">
                <div className="mb-3 flex items-center gap-2"><h3 className="text-[12px] font-semibold text-ink">{t("team_surface.group_settings")}</h3><span className="text-[10px] text-muted">{t("team_surface.member_count", { count: conversation.members?.length || 0 })}</span></div>
                <div className="mb-3 rounded-lg bg-panel p-3"><div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-faint">{t("team_surface.members")}</div><div className="space-y-1">{conversation.agent_member_ids?.map((agentId) => { const agent = agents.find((item) => item.agent_id === agentId); return <div key={agentId} className="flex items-center gap-2 text-[11px] text-ink"><span className="min-w-0 flex-1 truncate">{agent?.display_name || agent?.handle || agentId}</span><button className="text-[10px] text-danger hover:underline" disabled={busy || (conversation.agent_member_ids?.length || 0) <= 2} onClick={() => void removeGroupAgent(agentId)}>{t("team_surface.remove_member")}</button></div>; })}</div><div className="mt-2 flex gap-2"><select className="min-w-0 flex-1 rounded-md border border-line bg-paper px-2 py-1.5 text-[11px] text-ink outline-none" value={groupMemberDraft} onChange={(event) => setGroupMemberDraft(event.target.value)}><option value="">{t("team_surface.add_member")}</option>{agents.filter((agent) => !conversation.agent_member_ids?.includes(agent.agent_id)).map((agent) => <option key={agent.agent_id} value={agent.agent_id}>{agent.display_name || agent.handle || agent.agent_id}</option>)}</select><button className="btn secondary text-[10px]" disabled={busy || !groupMemberDraft} onClick={() => void addGroupAgent()}>{t("team_surface.add_member")}</button></div></div>
                <div className="grid gap-2 md:grid-cols-2">
                  <label className="text-[11px] text-muted">{t("team_surface.group_name")}<input className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-[12px] text-ink outline-none" value={groupTitleDraft} onChange={(event) => setGroupTitleDraft(event.target.value)} /></label>
                  <label className="text-[11px] text-muted">{t("team_surface.reply_mode")}<select className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-[12px] text-ink outline-none" value={groupReplyModeDraft} onChange={(event) => setGroupReplyModeDraft(event.target.value)}><option value="mention_only">{t("team_surface.reply_mode_mention")}</option><option value="coordinator">{t("team_surface.reply_mode_coordinator")}</option><option value="all_messages">{t("team_surface.reply_mode_all")}</option></select></label>
                  <label className="text-[11px] text-muted">{t("team_surface.coordinator")}<select className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-[12px] text-ink outline-none" value={groupCoordinatorDraft} onChange={(event) => setGroupCoordinatorDraft(event.target.value)}><option value="">{t("team_surface.unassigned")}</option>{agents.filter((agent) => conversation.agent_member_ids?.includes(agent.agent_id)).map((agent) => <option key={agent.agent_id} value={agent.agent_id}>{agent.display_name || agent.handle || agent.agent_id}</option>)}</select></label>
                  <label className="text-[11px] text-muted">{t("team_surface.max_fanout")}<input className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-[12px] text-ink outline-none" type="number" min="1" max="64" value={groupMaxFanoutDraft} onChange={(event) => setGroupMaxFanoutDraft(event.target.value)} /></label>
                </div>
                <label className="mt-3 flex items-center gap-2 text-[11px] text-muted"><input type="checkbox" checked={groupAllowAtAllDraft} onChange={(event) => setGroupAllowAtAllDraft(event.target.checked)} />{t("team_surface.allow_at_all")}</label>
                <div className="mt-3 flex flex-wrap gap-2"><button className="btn primary text-[11px]" disabled={groupSettingsLoading || !groupTitleDraft.trim()} onClick={() => void saveGroupSettings()}>{groupSettingsLoading ? t("team_surface.saving") : t("team_surface.save_settings")}</button>{conversation.status === "archived" ? <button className="btn secondary text-[11px]" disabled={busy} onClick={() => void transitionConversation("restore")}>{t("team_surface.restore_group")}</button> : <button className="btn secondary text-[11px]" disabled={busy} onClick={() => void transitionConversation("archive")}>{t("team_surface.archive_group")}</button>}<button className="text-[11px] text-danger hover:underline" disabled={busy} onClick={() => void transitionConversation("delete")}>{t("team_surface.delete_group")}</button></div>
              </div>
            </section>}
            <div className="team-conversation-body flex min-h-0 flex-1 bg-panel">
              <div className="team-message-scroll flex-1 overflow-y-auto px-5 py-5">
                {messages.length === 0 ? (
                  <div className="grid h-full place-items-center text-[13px] text-muted">{t("team_surface.no_messages")}</div>
                ) : (
                  <div className="w-full space-y-1">
                    {messages.map((message) => {
                      const isHuman = message.author_kind === "human";
                      const authorAgent = !isHuman ? agents.find((a) => a.actor_id === message.author_actor_id || a.agent_id === message.author_actor_id) : null;
                      const agentPresence = authorAgent ? presenceByAgent.get(authorAgent.agent_id) : null;
                      const isOnline = !isHuman && (agentPresence?.state === "ready" || agentPresence?.state === "working" || authorAgent?.status === "ready");
                      const reactionOptions = TEAM_REACTION_OPTIONS.map((option) => ({
                        ...option,
                        count: message.reactions?.filter((reaction) => reaction.emoji === option.emoji).length || 0,
                        active: Boolean(message.reactions?.some((reaction) => reaction.actor_id === overview?.actor_id && reaction.emoji === option.emoji)),
                      }));
                      const thumbsUpCount = reactionOptions.find((reaction) => reaction.emoji === "👍")?.count || 0;

                      return (
                        <article
                          key={message.message_id}
                          data-team-message-id={message.message_id}
                          className={"team-message group relative flex gap-3.5 rounded-xl px-3.5 py-3 transition-all duration-150 " + (isHuman ? "hover:bg-paper/40" : "bg-paper/25 hover:bg-paper/50 border border-line/30 shadow-2xs")}
                        >
                          {/* Floating Hover Action Toolbar */}
                          {message.status === "active" && (
                            <div className="absolute -top-3.5 right-4 z-10 hidden items-center gap-0.5 rounded-full border border-line/80 bg-panel px-1.5 py-0.5 shadow-md backdrop-blur-md transition-all group-hover:flex">
                              <div className="relative" data-team-reaction-picker>
                                <button
                                  type="button"
                                  className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink"
                                  onClick={() => setReactionMenuMessageId((current) => current === message.message_id ? null : message.message_id)}
                                  aria-label={t("team_surface.react")}
                                  aria-haspopup="menu"
                                  aria-expanded={reactionMenuMessageId === message.message_id}
                                  title={t("team_surface.react")}
                                >
                                  <span>👍</span>
                                  {thumbsUpCount > 0 && <span className="text-[10px] font-medium">{thumbsUpCount}</span>}
                                </button>
                                {reactionMenuMessageId === message.message_id && (
                                  <div className="team-reaction-picker" role="menu" aria-label={t("team_surface.reaction_picker")}>
                                    {reactionOptions.map((reaction) => {
                                      const label = t(reaction.labelKey);
                                      return (
                                        <button
                                          key={reaction.emoji}
                                          type="button"
                                          role="menuitem"
                                          className={"team-reaction-option" + (reaction.active ? " team-reaction-option-active" : "")}
                                          onClick={() => void toggleReaction(message, reaction.emoji)}
                                          aria-label={label}
                                          aria-pressed={reaction.active}
                                          title={label}
                                          data-tooltip={label}
                                        >
                                          <span aria-hidden="true">{reaction.emoji}</span>
                                        </button>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                              <button
                                className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink"
                                onClick={() => void openMessageThread(message.message_id)}
                                aria-label={t("team_surface.reply_in_thread")}
                                title={t("team_surface.reply_in_thread")}
                              >
                                <Icon name="chat" size={13} />
                                <span className="sr-only">{t("team_surface.reply_in_thread")}</span>
                              </button>
                              <button
                                className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink"
                                onClick={() => { setQuoteMessage(message); setDraft(""); }}
                                aria-label={t("team_surface.quote")}
                                title={t("team_surface.quote")}
                              >
                                <Icon name="quote" size={13} />
                                <span className="sr-only">{t("team_surface.quote")}</span>
                              </button>
                              <button
                                className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink disabled:opacity-50"
                                disabled={busy}
                                onClick={() => void createTaskFromMessage(message)}
                                aria-label={t("team_surface.convert_to_task")}
                                title={t("team_surface.convert_to_task")}
                              >
                                <Icon name="check" size={13} />
                                <span className="sr-only">{t("team_surface.convert_to_task")}</span>
                              </button>
                              <button
                                className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink disabled:opacity-50"
                                disabled={busy}
                                onClick={() => void createTaskFromMessage(message, true)}
                                aria-label={t("team_surface.send_as_task")}
                                title={t("team_surface.send_as_task")}
                              >
                                <Icon name="send" size={12} />
                                <span className="sr-only">{t("team_surface.send_as_task")}</span>
                              </button>
                              <button
                                className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink disabled:opacity-50"
                                disabled={busy || translatingMessageId === message.message_id}
                                onClick={() => void translateMessage(message)}
                                aria-label={t("team_surface.translate")}
                                title={translatingMessageId === message.message_id ? t("team_surface.translating") : t("team_surface.translate")}
                              >
                                <Icon name="translate" size={13} />
                                <span className="sr-only">{translatingMessageId === message.message_id ? t("team_surface.translating") : t("team_surface.translate")}</span>
                              </button>
                              {conversation.members?.some((member) => member.actor_id === overview?.actor_id && ["owner", "admin"].includes(member.member_role)) && (
                                <button
                                  className={"flex h-7 items-center gap-1 rounded-full px-2 text-[11px] hover:bg-paper " + (pinnedMessageIds.has(message.message_id) ? "font-medium text-accent" : "text-muted hover:text-ink")}
                                  onClick={() => void togglePin(message)}
                                  aria-label={pinnedMessageIds.has(message.message_id) ? t("team_surface.unpin") : t("team_surface.pin")}
                                  title={pinnedMessageIds.has(message.message_id) ? t("team_surface.unpin") : t("team_surface.pin")}
                                >
                                  <Icon name="pin" size={13} />
                                  <span className="sr-only">{pinnedMessageIds.has(message.message_id) ? t("team_surface.unpin") : t("team_surface.pin")}</span>
                                </button>
                              )}
                              <button
                                className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink"
                                onClick={() => void copyMessageLink(message.message_id)}
                                aria-label={t("team_surface.copy_link")}
                                title={t("team_surface.copy_link")}
                              >
                                <Icon name="copy" size={13} />
                                <span className="sr-only">{t("team_surface.copy_link")}</span>
                              </button>
                              {message.author_actor_id === overview?.actor_id && (
                                <>
                                  <button
                                    className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-muted hover:bg-paper hover:text-ink"
                                    onClick={() => beginEdit(message)}
                                    aria-label={t("team_surface.edit")}
                                    title={t("team_surface.edit")}
                                  >
                                    <Icon name="pencil" size={13} />
                                    <span className="sr-only">{t("team_surface.edit")}</span>
                                  </button>
                                  <button
                                    className="flex h-7 items-center gap-1 rounded-full px-2 text-[11px] text-danger hover:bg-danger/10 hover:text-danger"
                                    onClick={() => void removeMessage(message)}
                                    aria-label={t("team_surface.delete")}
                                    title={t("team_surface.delete")}
                                  >
                                    <Icon name="trash" size={13} />
                                    <span className="sr-only">{t("team_surface.delete")}</span>
                                  </button>
                                </>
                              )}
                            </div>
                          )}

                          {/* Avatar */}
                          <div className="relative shrink-0">
                            {isHuman ? (
                              <div className="grid h-8 w-8 place-items-center rounded-lg border border-accent/20 bg-accentSoft text-[12px] font-bold text-accent">
                                Y
                              </div>
                            ) : (
                              <AgentAvatar
                                agent={authorAgent}
                                online={isOnline}
                                className="h-8 w-8 rounded-lg transition-transform group-hover:scale-105"
                                iconSize={14}
                              />
                            )}
                          </div>

                          {/* Message Body */}
                          <div className="min-w-0 flex-1">
                            <div className="mb-1 flex items-center gap-2">
                              <span className="text-[13px] font-semibold text-ink">
                                {isHuman ? t("team_surface.you") : (authorAgent?.display_name || authorAgent?.handle || t("team_surface.agent"))}
                              </span>
                              {!isHuman && (
                                <span className="inline-flex items-center gap-1 rounded-full border border-line/80 bg-panel px-2 py-0.5 text-[10px] font-medium text-muted shadow-2xs">
                                  <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" aria-hidden="true" />
                                  {authorAgent?.base_role || "AI"}
                                </span>
                              )}
                              <time className="text-[11px] text-faint ml-auto sm:ml-0">{timeLabel(message.created_at)}</time>
                              {message.edited_at && <span className="text-[10px] text-faint">({t("team_surface.edited")})</span>}
                            </div>

                            {message.status === "active" && message.quote_message_id && (() => {
                              const quoted = messages.find((item) => item.message_id === message.quote_message_id);
                              return (
                                <div className="mb-2 rounded-md border-l-2 border-accent/50 bg-paper/70 px-3 py-1.5 text-[11px] text-muted">
                                  <span className="font-medium text-ink">{t("team_surface.quoted_message")}:</span> {quoted?.body || message.quote_message_id}
                                </div>
                              );
                            })()}

                            {editingMessageId === message.message_id ? (
                              <div className="space-y-2 rounded-lg border border-line bg-panel p-2.5">
                                <textarea
                                  autoFocus
                                  className="min-h-16 w-full rounded-lg border border-line bg-panel px-2.5 py-2 text-[13px] text-ink outline-none focus:border-accent"
                                  value={editDraft}
                                  onChange={(event) => setEditDraft(event.target.value)}
                                  onKeyDown={(event) => {
                                    if (event.key === "Escape") cancelEdit();
                                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                                      event.preventDefault();
                                      void saveEdit();
                                    }
                                  }}
                                />
                                <div className="flex items-center gap-2">
                                  <button
                                    className="btn primary text-[11px]"
                                    disabled={busy || !editDraft.trim()}
                                    onClick={() => void saveEdit()}
                                  >
                                    {t("team_surface.save_edit")}
                                  </button>
                                  <button className="btn secondary text-[11px]" onClick={cancelEdit}>
                                    {t("team_surface.cancel_edit")}
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <>
                                {message.status === "active" && message.reasoning && (
                                  <ThinkingBlock text={message.reasoning} showThinking={showThinking} />
                                )}
                                {message.status === "active" && message.metadata?.tool_calls && message.metadata.tool_calls.length > 0 && (
                                  <div className="my-2 space-y-1.5" data-testid={`tool-calls-${message.message_id}`}>
                                    {message.metadata.tool_calls.map((tc, idx) => (
                                      <details key={idx} className="group rounded-lg border border-line/60 bg-paper/60 text-[11px] text-muted overflow-hidden">
                                        <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5 font-mono text-[11px] font-medium text-ink/90 hover:bg-panel/60">
                                          <Icon name="wrench" size={13} className="text-accent" />
                                          <span>{tc.name}</span>
                                          {tc.result !== undefined && (
                                            <span className="ml-auto rounded bg-okSoft px-1.5 py-0.2 text-[9px] font-semibold text-ok uppercase">
                                              done
                                            </span>
                                          )}
                                        </summary>
                                        <div className="border-t border-line/40 bg-panel/30 p-2.5 space-y-1.5 font-mono text-[10px]">
                                          {tc.arguments && Object.keys(tc.arguments).length > 0 && (
                                            <div>
                                              <span className="font-semibold text-faint block mb-0.5">Parameters:</span>
                                              <pre className="overflow-x-auto rounded bg-panel p-1.5 text-muted break-words whitespace-pre-wrap">
                                                {JSON.stringify(tc.arguments, null, 2)}
                                              </pre>
                                            </div>
                                          )}
                                          {tc.result !== undefined && (
                                            <div>
                                              <span className="font-semibold text-faint block mb-0.5">Result:</span>
                                              <pre className="overflow-x-auto rounded bg-panel p-1.5 text-ink/90 break-words whitespace-pre-wrap max-h-40">
                                                {typeof tc.result === "string" ? tc.result : JSON.stringify(tc.result, null, 2)}
                                              </pre>
                                            </div>
                                          )}
                                        </div>
                                      </details>
                                    ))}
                                  </div>
                                )}
                                <p className={"whitespace-pre-wrap break-words text-[14px] leading-7 " + (message.status === "tombstoned" ? "italic text-faint" : "text-ink")}>
                                  {message.status === "tombstoned" ? t("team_surface.deleted_message") : message.body}
                                </p>
                              </>
                            )}

                            {message.status === "active" && (() => {
                              const language = (typeof navigator !== "undefined" ? navigator.language : "en").split("-")[0] || "en";
                              const translation = messageTranslations[message.message_id]
                                || message.translations?.find((item) => item.language.toLowerCase() === language.toLowerCase());
                              return translation ? (
                                <div className="mt-2.5 rounded-lg border border-line bg-paper/80 px-3 py-2" data-testid={`translation-${message.message_id}`}>
                                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
                                    {t("team_surface.translated", { language: translation.language })}
                                  </div>
                                  <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink">{translation.body}</p>
                                </div>
                              ) : null;
                            })()}

                            {message.attachments?.length > 0 && (
                              <div className="mt-2.5 flex flex-wrap gap-2">
                                {message.attachments.map((attachment) => (
                                  <a
                                    key={attachment.attachment_id}
                                    className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-paper px-2.5 py-1.5 text-[11px] font-medium text-accent hover:border-accent hover:text-ink"
                                    href={teamMessageAttachmentUrl(message.message_id, attachment.attachment_id)}
                                    download={attachment.filename}
                                  >
                                    <Icon name={attachment.media_type.startsWith("image/") ? "image" : "file"} size={13} />
                                    <span className="max-w-48 truncate">{attachment.filename}</span>
                                  </a>
                                ))}
                              </div>
                            )}

                            {/* Reaction Chips */}
                            {message.status === "active" && reactionOptions.some((reaction) => reaction.count > 0) && (
                              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                {reactionOptions.filter((reaction) => reaction.count > 0).map((reaction) => {
                                  const label = t(reaction.labelKey);
                                  return (
                                    <button
                                      key={reaction.emoji}
                                      type="button"
                                      className={"inline-flex items-center gap-1 rounded-md border px-2.5 py-0.5 text-[11px] hover:bg-paper " + (reaction.active ? "border-accent bg-accent/10 text-accent" : "border-line bg-panel text-ink")}
                                      onClick={() => void toggleReaction(message, reaction.emoji)}
                                      aria-label={label}
                                      aria-pressed={reaction.active}
                                      title={label}
                                    >
                                      <span aria-hidden="true">{reaction.emoji}</span>
                                      <span className="font-semibold">{reaction.count}</span>
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
              </div>
              {thread && <aside className="team-thread-panel w-[300px] shrink-0 border-l border-line bg-paper/40 px-4 py-4" aria-label={t("team_surface.thread")}>
                <div className="mb-3 flex items-center gap-2">
                  <h3 className="min-w-0 flex-1 truncate text-[13px] font-semibold text-ink">{t("team_surface.thread")}</h3>
                  <button className="text-[10px] text-muted hover:text-ink" onClick={() => { setFocusMessageId(thread.root_message_id); setThread(null); setThreadMessages([]); }}>{t("team_surface.view_in_group")}</button>
                  <button className="text-[11px] text-muted hover:text-ink" onClick={() => { setThread(null); setThreadMessages([]); }} aria-label={t("common.close")}>×</button>
                </div>
                {threadLoading ? <div className="text-[11px] text-faint">{t("team_surface.loading")}</div> : <div className="space-y-3 overflow-y-auto">
                  {threadMessages.map((message) => <article key={message.message_id} className="border-b border-line pb-2"><div className="text-[10px] text-faint">{message.author_kind === "human" ? t("team_surface.you") : t("team_surface.agent")}</div><p className="mt-1 whitespace-pre-wrap break-words text-[12px] text-ink">{message.status === "tombstoned" ? t("team_surface.deleted_message") : message.body}</p></article>)}
                </div>}
              </aside>}
            </div>
            <div className="team-composer-shell bg-panel px-5 py-4">
              {quoteMessage && (
                <div className="mx-auto mb-2.5 flex max-w-3xl items-start gap-2.5 rounded-xl border-l-2 border-accent bg-paper/90 px-3.5 py-2 text-[12px] text-muted shadow-xs">
                  <span className="min-w-0 flex-1 truncate">{t("team_surface.replying_to")}: {quoteMessage.body}</span>
                  <button className="shrink-0 rounded-md p-0.5 text-muted hover:bg-chromeHover hover:text-ink" onClick={() => setQuoteMessage(null)} aria-label={t("team_surface.cancel_quote")}>
                    <Icon name="x" size={14} />
                  </button>
                </div>
              )}
              {pendingAttachments.length > 0 && (
                <div className="mx-auto mb-2.5 flex max-w-3xl flex-wrap gap-2" aria-label={t("team_surface.pending_attachments")}>
                  {pendingAttachments.map((file, index) => (
                    <span key={`${file.name}-${file.size}-${file.lastModified}`} className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-paper px-2.5 py-1 text-[11px] text-muted shadow-2xs">
                      <Icon name="file" size={12} className="text-accent" />
                      <span className="max-w-48 truncate font-medium text-ink">{file.name}</span>
                      <button className="ml-1 rounded-md p-0.5 text-faint hover:bg-chromeHover hover:text-ink" aria-label={`${t("team_surface.remove_attachment")} ${file.name}`} onClick={() => setPendingAttachments((current) => current.filter((_item, itemIndex) => itemIndex !== index))}>
                        <Icon name="x" size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="team-composer mx-auto w-full rounded-xl border border-line bg-panel p-2 shadow-sm transition-all focus-within:border-accent/80 focus-within:ring-2 focus-within:ring-accent/15">
                <div className="relative min-w-0 flex-1">
                  {mentionCandidates.length > 0 && (
                    <div className="absolute bottom-full left-0 right-0 mb-2 max-h-48 overflow-auto rounded-lg border border-line bg-panel p-1.5 shadow-xl" role="listbox" aria-label={t("team_surface.mention_suggestions")}>
                      {mentionCandidates.map((agent) => (
                        <button
                          key={agent.agent_id}
                          className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12px] text-ink transition-colors hover:bg-paper"
                          onClick={() => {
                            const match = draft.match(/(?:^|\s)@[\w-]*$/u);
                            const prefix = match ? draft.slice(0, draft.length - match[0].length) : draft;
                            setDraft(`${prefix}@${agent.handle} `);
                          }}
                        >
                          <div className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accentSoft text-accent">
                            <Icon name="sparkle" size={12} />
                          </div>
                          <span className="font-semibold text-ink">@{agent.handle}</span>
                          <span className="truncate text-muted">{agent.display_name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  <textarea
                    className="min-h-11 w-full resize-none bg-transparent px-3 py-2 text-[14px] leading-relaxed text-ink placeholder:text-muted/50 outline-none"
                    placeholder={t("team_surface.message_placeholder")}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                  />
                </div>
                <div className="flex items-center justify-between border-t border-line/40 px-2 pt-2">
                  <div className="flex items-center gap-0.5">
                    <label className="grid h-8 w-8 cursor-pointer place-items-center rounded-lg text-muted transition-colors hover:bg-panel hover:text-ink" aria-label={t("team_surface.attach_file")} title={t("team_surface.attach_file")}>
                      <Icon name="image" size={15} />
                      <input
                        className="sr-only"
                        type="file"
                        multiple
                        accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,.txt,.md,.csv,.json,.log,.py,.js,.jsx,.ts,.tsx,.css,.html,.xml,.yaml,.yml,.toml,.sql,.sh,.diff"
                        onChange={(event) => { addAttachments(event.target.files); event.currentTarget.value = ""; }}
                      />
                    </label>
                    <label className="grid h-8 w-8 cursor-pointer place-items-center rounded-lg text-muted transition-colors hover:bg-panel hover:text-ink" aria-label={t("team_surface.attach_file")} title={t("team_surface.attach_file")}>
                      <Icon name="paperclip" size={15} />
                      <input
                        className="sr-only"
                        type="file"
                        multiple
                        accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,.txt,.md,.csv,.json,.log,.py,.js,.jsx,.ts,.tsx,.css,.html,.xml,.yaml,.yml,.toml,.sql,.sh,.diff"
                        onChange={(event) => { addAttachments(event.target.files); event.currentTarget.value = ""; }}
                      />
                    </label>
                    <div className="relative">
                      <button
                        type="button"
                        className={"grid h-8 w-8 place-items-center rounded-lg transition-colors " + (emojiPickerOpen ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel hover:text-ink")}
                        onClick={() => setEmojiPickerOpen((open) => !open)}
                        title={t("team_surface.insert_emoji")}
                        aria-label={t("team_surface.insert_emoji")}
                        aria-expanded={emojiPickerOpen}
                      >
                        <Icon name="smile" size={15} />
                      </button>
                      {emojiPickerOpen && (
                        <EmojiPicker
                          onSelect={(emoji) => {
                            insertEmoji(emoji);
                            setEmojiPickerOpen(false);
                          }}
                          onClose={() => setEmojiPickerOpen(false)}
                        />
                      )}
                    </div>
                    <button
                      type="button"
                      className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors hover:bg-panel hover:text-ink"
                      onClick={toggleDraftBold}
                      title={t("team_surface.format_message")}
                      aria-label={t("team_surface.format_message")}
                    >
                      <span className="font-serif text-[16px] font-semibold">T</span>
                    </button>
                  </div>
                  <button
                    className="flex h-8 items-center gap-1.5 rounded-full bg-accent px-4 text-[12px] font-semibold text-white shadow-sm transition-all hover:bg-accent/90 hover:scale-102 active:scale-98 disabled:opacity-40 disabled:pointer-events-none"
                    disabled={busy || !draft.trim()}
                    onClick={() => void send()}
                    aria-label={t("common.send")}
                  >
                    <Icon name="send" size={12} />
                    <span>{t("common.send")}</span>
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </main>
      {contextRailOpen && (
        <aside className="team-context-rail" aria-label={t("team_surface.context_title")}>
          <div className="team-context-header">
            <h2>{t("team_surface.context_title")}</h2>
            <button
              className="team-context-close"
              aria-label={t("topbar.hide_side_panel")}
              title={t("topbar.hide_side_panel")}
              onClick={() => setContextRailOpen(false)}
            >
              <Icon name="x" size={15} />
            </button>
          </div>

          <section className="team-context-block team-context-about">
            <div className="team-context-section-label">{t("team_surface.about")}</div>
            <div className="team-context-identity">
              {conversationAgent ? (
                <AgentAvatar agent={conversationAgent} className="h-11 w-11 rounded-xl" iconSize={18} />
              ) : (
                <span className="team-context-channel-mark">#</span>
              )}
              <div className="min-w-0">
                <div className="team-context-name">{conversation ? conversationLabel(conversation, agents) : t("team_surface.home_title")}</div>
                <div className="team-context-subtitle">
                  {conversation ? t("team_surface.member_count", { count: conversation.members?.length || 0 }) : t("team_surface.home_body")}
                </div>
              </div>
            </div>
            <div className="team-context-chips">
              <span className="team-context-chip team-context-chip-success"><i aria-hidden="true" />{t("team_surface.active")}</span>
              {conversation?.reply_mode && <span className="team-context-chip">{conversation.reply_mode === "mention_only" ? "@ mentions" : conversation.reply_mode}</span>}
            </div>
            <p className="team-context-description">
              {conversation?.reply_mode === "coordinator"
                ? t("team_surface.tip_coordinator_agents")
                : conversation?.reply_mode === "all_messages"
                  ? t("team_surface.tip_all_agents")
                  : t("team_surface.tip_mention_agents")}
            </p>
          </section>

          <section className="team-context-block">
            <div className="team-context-section-head">
              <div className="team-context-section-label">{t("team_surface.members")} <span>{contextAgents.length}</span></div>
              <button className="team-context-link" onClick={() => setActiveView("agents")}>{t("team_surface.view_all")}</button>
            </div>
            <div className="team-agent-list">
              {contextAgents.length === 0 ? (
                <div className="team-context-empty">{t("team_surface.agent_empty")}</div>
              ) : contextAgents.map((agent) => {
                const state = presenceByAgent.get(agent.agent_id)?.state || agent.status;
                const online = state === "ready" || state === "working" || state === "waiting";
                return (
                  <button key={agent.agent_id} className="team-context-agent" onClick={() => { setActiveView("agents"); void loadAgentProfile(agent.agent_id); }}>
                    <AgentAvatar agent={agent} online={online} className="h-8 w-8 rounded-lg" iconSize={14} />
                    <span className="team-context-agent-body">
                      <span className="team-context-agent-name">{agent.display_name || agent.handle || agent.agent_id}</span>
                      <span className="team-context-agent-role">{agent.base_role || t("team_surface.agent")}</span>
                    </span>
                    <Icon name="chevronRight" size={13} className="team-context-agent-arrow" />
                  </button>
                );
              })}
            </div>
            <div className="team-context-owner">
              <span className="team-context-user-avatar">Y</span>
              <span><strong>{t("team_surface.you")}</strong><small>{t("team_surface.owner")}</small></span>
            </div>
          </section>

          <section className="team-context-block">
            <div className="team-context-section-head">
              <div className="team-context-section-label">{t("team_surface.shared_files")} <span>{artifacts.length}</span></div>
              <button className="team-context-link" onClick={() => setActiveView("files")}>{t("team_surface.view_all")}</button>
            </div>
            {artifacts.length === 0 ? (
              <div className="team-context-empty">{t("team_surface.no_shared_files")}</div>
            ) : (
              <div className="team-file-list">
                {artifacts.slice(0, 4).map((artifact) => {
                  const revision = artifact.revisions[artifact.revisions.length - 1];
                  const fileAgent = agents.find((agent) => agent.actor_id === revision?.publisher_actor_id || agent.agent_id === revision?.publisher_actor_id);
                  const filename = revision?.filename || artifact.artifact_id;
                  const kind = artifactKind(filename);
                  return (
                    <button key={artifact.artifact_id} className="team-context-file" onClick={() => { setActiveView("files"); void loadArtifactPreview(artifact.artifact_id); }}>
                      <span className={`team-file-kind team-file-kind-${kind}`}>{kind === "video" ? "▶" : kind.toUpperCase()}</span>
                      <span className="team-context-file-body">
                        <span className="team-context-file-name">{filename}</span>
                        <span className="team-context-file-meta">{fileAgent?.display_name || t("team_surface.agent")} · {ageLabel(revision?.created_at || artifact.created_at)}</span>
                      </span>
                      <span className="team-context-file-size">{artifactSize(revision?.size_bytes || 0)}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="team-context-block team-context-activity">
            <div className="team-context-section-head">
              <div className="team-context-section-label">{t("team_surface.activity_title")}</div>
              <button className="team-context-link" onClick={() => setActiveView("activity")}>{t("team_surface.view_all")}</button>
            </div>
            {activities.length === 0 ? (
              <div className="team-context-empty">{t("team_surface.no_recent_activity")}</div>
            ) : (
              <div className="team-activity-list">
                {activities.slice(0, 5).map((activity) => {
                  const actor = agents.find((agent) => agent.actor_id === activity.actor_id || agent.agent_id === activity.actor_id);
                  return (
                    <button key={activity.activity_id} className="team-context-activity-row" onClick={() => { setActiveView("activity"); setFocusMessageId(activity.aggregate_type === "message" ? activity.aggregate_id : null); }}>
                      <span className="team-context-activity-time">{ageLabel(activity.occurred_at)}</span>
                      <AgentAvatar agent={actor} className="h-6 w-6 rounded-md" iconSize={11} />
                      <span className="team-context-activity-copy"><strong>{actor?.display_name || t("team_surface.agent")}</strong> {activity.activity_kind.replace(/_/gu, " ")}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        </aside>
      )}
    </div>
  );
}
