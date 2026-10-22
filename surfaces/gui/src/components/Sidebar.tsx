import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getI18n, useTranslation } from "react-i18next";
import {
  announceCloudChanged,
  AUTOMATIONS_CHANGED,
  CLOUD_CHANGED,
  cloudLogin,
  cloudLogout,
  getAutomations,
  getCloudStatus,
  getPersonas,
  getSettings,
  INBOX_UNLOCK,
  PERSONAS_CHANGED,
  setNavLayout,
  waitForCloudSignIn,
  type Automation,
  type CloudStatus,
  type Persona,
  type RecentWorkspace,
  type SurfaceVisibility,
  type TeamSearchRecord,
  type TeamSearchKind,
} from "../api";
import type { SessionInfo } from "../types";
import { isProjectScoped, shortPersonaName } from "../personaScope";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { Icon, type IconName } from "./Icon";
import { personaGlyph } from "./personaIcon";
import { SearchModal } from "./SearchModal";
import { baseName } from "../paths";

// Session surfaces shown as accordions, in display order. The surfaced personas drive this list
// (so third-party / Ops personas appear); the hardcoded set is the fallback before personas load.
const SURFACES: { key: string; label: string; icon: IconName; cls: string }[] = [
  { key: "cowork", label: "Coworker", icon: "diamond", cls: "ico-cowork" },
  { key: "chat", label: "Chat", icon: "chat", cls: "ico-chat" },
  { key: "code", label: "Code", icon: "code", cls: "ico-code" },
];

const surfaceFromPersona = (p: Persona) => ({
  key: p.id,
  label: shortPersonaName(p.name, p.id),
  icon: personaGlyph(p.icon, p.requires_folder),
  cls: `ico-${p.icon || "cowork"}`,
});

// Attention = Inbox items awaiting a session (an accent count that bubbles session → persona →
// footer Inbox — all views of the one Inbox queue, never a second list).
function AttnBadge({ n }: { n: number }) {
  const { t } = useTranslation();
  if (!n) return null;
  return (
    <span
      className="text-[11px] font-medium text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/50 border border-amber-200 dark:border-amber-800/60 rounded-full px-1.5 leading-[15px] shrink-0"
      title={t("sidebar.awaiting_attention", { n })}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

function UnseenBadge({ n, failed }: { n: number; failed?: boolean }) {
  const { t } = useTranslation();
  if (!n) return null;
  return (
    <span
      className={
        "text-[11px] font-medium rounded-full px-1.5 leading-[15px] shrink-0 " +
        (failed
          ? "text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-800/60"
          : "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/50 border border-blue-200 dark:border-blue-800/60")
      }
      title={failed ? t("sidebar.unseen_failed", { count: n }) : t("sidebar.unseen_new", { count: n })}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

function LiveDot({ state }: { state?: "working" | "sleeping" | "idle" }) {
  const { t } = useTranslation();
  if (state !== "working" && state !== "sleeping") return null;
  return state === "working" ? (
    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" title={t("sidebar.status_working")} />
  ) : (
    <span
      className="w-1.5 h-1.5 rounded-full bg-faint/60 shrink-0"
      title={t("sidebar.status_sleeping")}
    />
  );
}

function OriginIcon({ s }: { s: SessionInfo }) {
  const { t } = useTranslation();
  if (s.origin !== "slack") return null;
  return (
    <ConnectorIcon
      connector={{ logo: "slack", brand_color: "#611f69" }}
      size={12}
      title={s.origin_label || t("sidebar.from_slack")}
    />
  );
}

function ConnectorDot({ subs }: { subs?: string[] }) {
  if (!subs || subs.length === 0) return null;
  return (
    <span
      className="w-1.5 h-1.5 rounded-full bg-faint shrink-0"
      data-brand={subs[0]}
      title={subs.join(", ")}
    />
  );
}

/** 16px glyph lane icon / placeholder slot */
function GlyphSlot({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <span
      aria-hidden={children ? undefined : "true"}
      className={"flex size-4 shrink-0 items-center justify-center " + (className || "")}
    >
      {children}
    </span>
  );
}

interface Props {
  agent: string;
  workspace: string;
  surfaces: SurfaceVisibility;
  sessions: SessionInfo[];
  projects: RecentWorkspace[];
  activeSession: string;
  onSwitchAgent: (agent: string) => void;
  onNewSession: (agent: string) => void;
  onSelectSession: (id: string, workspace: string, agent: string) => void;
  onNewProject: (persona: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onArchiveSession: (id: string, archived: boolean) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  onManage: () => void;
  // Grouped-nav gear entry point (§7). "Manage coworkers…" moved to the composer's
  // setup-row picker (UX-029).
  onOpenPersona: (id: string) => void;
  onOpenScheduled: () => void;
  onOpenTeam?: () => void;
  // Scheduled-band row click: open the Automations surface ON that automation (UX-023).
  onOpenAutomation: (id: string) => void;
  onOpenIntegrations: () => void;
  onOpenAudit: () => void;
  onOpenInbox: () => void;
  // Presents the full sign-in surface. Optional during the transition so isolated sidebar
  // consumers can retain the compact browser redirect behavior.
  onOpenCloudSignIn?: () => void;
  onSelectTeamSearch?: (result: TeamSearchRecord & { kind: TeamSearchKind }) => void;
  scheduledActive: boolean;
  teamActive?: boolean;
  integrationsActive: boolean;
  auditActive: boolean;
  inboxActive: boolean;
  // Collapse controls (⌘B / hover-peek). `onCollapse` docks/undocks; `onPeekLeave` hides the
  // floating peek when the pointer leaves the panel.
  collapsed?: boolean;
  onCollapse?: () => void;
  onPeekLeave?: () => void;
}

// Compact age for project session rows: "now" / "5m" / "6h" / "3d" / "2w" / "4mo" / "2y".
const compactAge = (iso?: string | null): string => {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const tt = getI18n().getFixedT(null, "translation");
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return tt("sidebar.age_now");
  const mins = Math.floor(secs / 60);
  if (mins < 60) return tt("sidebar.age_m", { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return tt("sidebar.age_h", { n: hrs });
  const days = Math.floor(hrs / 24);
  if (days < 7) return tt("sidebar.age_d", { n: days });
  const weeks = Math.floor(days / 7);
  if (days < 30) return tt("sidebar.age_w", { n: weeks });
  const months = Math.floor(days / 30);
  if (days < 365) return tt("sidebar.age_mo", { n: months });
  return tt("sidebar.age_y", { n: Math.floor(days / 365) });
};

// Sessions shown per group before "Show more" comes from Settings (sessions_peek, default 5).

export function Sidebar(props: Props) {
  const { t } = useTranslation();
  const [searchModalOpen, setSearchModalOpen] = useState(false);
  const [appMenuOpen, setAppMenuOpen] = useState(false);
  // The account row (§26): cloud sign-in status drives the avatar/name/dot; refreshed on
  // focus and whenever the menu opens (sign-in completes out-of-band in the browser).
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  // Inbox chip sticky unlock (§26): absent until the product first parks an item (or a
  // session first goes Unattended), then permanent. Per-device, like nav collapse.
  const [inboxUnlocked, setInboxUnlocked] = useState(
    () => {
      try {
        return typeof localStorage !== "undefined" && localStorage.getItem("ocw:inbox-unlocked") === "1";
      } catch {
        return false;
      }
    },
  );
  const refreshCloud = () => getCloudStatus().then(setCloud).catch(() => {});
  useEffect(() => {
    refreshCloud();
    const onFocus = () => refreshCloud();
    window.addEventListener("focus", onFocus);
    window.addEventListener(CLOUD_CHANGED, onFocus);
    const unlock = () => {
      localStorage.setItem("ocw:inbox-unlocked", "1");
      setInboxUnlocked(true);
    };
    window.addEventListener(INBOX_UNLOCK, unlock);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(CLOUD_CHANGED, onFocus);
      window.removeEventListener(INBOX_UNLOCK, unlock);
    };
  }, []);
  // UX-023: automations feed the nav row's badge + the Scheduled band. The 15s poll
  // is the baseline; mutations announce AUTOMATIONS_CHANGED for an instant refresh
  // (mark-seen must clear the badge the moment the detail opens).
  const [automations, setAutomations] = useState<Automation[]>([]);
  useEffect(() => {
    const load = () => getAutomations().then(setAutomations).catch(() => {});
    load();
    const t = setInterval(load, 15_000);
    window.addEventListener(AUTOMATIONS_CHANGED, load);
    return () => {
      clearInterval(t);
      window.removeEventListener(AUTOMATIONS_CHANGED, load);
    };
  }, []);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  // Two-step delete inside the row's ⋮ menu: Delete arms ("Delete?"), a second click deletes.
  // Archive is the primary way to put a conversation away — one click, reversible.
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);
  // The open row-actions ⋮ menu (one at a time). Fixed-position, not absolute: the expanded
  // accordion group clips overflow (its rounded fill), so an absolute popover on its lower rows
  // would be cut off — same constraint as SlackDetail's person picker.
  const [rowMenu, setRowMenu] = useState<{
    id: string;
    top: number;
    left: number;
    anchor: HTMLElement;
  } | null>(null);
  const closeRowMenu = () => {
    setRowMenu(null);
    setConfirmDelId(null);
  };
  const openRowMenu = (id: string, anchor: HTMLElement) => {
    const r = anchor.getBoundingClientRect();
    const MENU_W = 160; // w-40
    const MENU_H = 150; // ~4 items + divider; only used to flip upward near the window bottom
    setConfirmDelId(null);
    setRowMenu({
      id,
      top: r.bottom + 4 + MENU_H > window.innerHeight ? r.top - MENU_H : r.bottom + 4,
      left: Math.max(8, r.right - MENU_W),
      anchor,
    });
  };
  useEffect(() => {
    if (!rowMenu) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRowMenu();
    // Scrolling an ANCESTOR of the anchor row detaches the fixed menu from it — dismiss.
    // Filter by containment: unrelated scrollers (the transcript auto-follow during a
    // streaming turn fires constantly) must not close the menu.
    const onScroll = (e: Event) => {
      const t = e.target;
      if (t === document || (t instanceof Node && t.contains(rowMenu.anchor))) closeRowMenu();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowMenu]);
  const [showArchived, setShowArchived] = useState(false);
  // Surfaced + enabled personas drive the surface list + family-aware behavior.
  // Refetched on the personas-changed event so an enable/install/delete in Settings
  // shows up here immediately (no page refresh).
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  useEffect(() => {
    const load = () =>
      getPersonas()
        .then(setPersonas)
        .catch(() => setPersonas(null));
    load();
    window.addEventListener(PERSONAS_CHANGED, load);
    return () => window.removeEventListener(PERSONAS_CHANGED, load);
  }, []);
  const personaOf = (id: string) => personas?.find((p) => p.id === id);

  // Sidebar layout (§7): "grouped" = the per-coworker accordion; "flat" = a single
  // ungrouped list (Pinned + Recent). Flat stays the default even with Coworkers shipped
  // (UX-029 flips the flag for the picker, not the nav shape — the flat chronological
  // list default is the 2026-07-20 owner call). An explicit stored choice always wins.
  const defaultLayout: "flat" | "grouped" = "flat";
  const [layout, setLayout] = useState<"flat" | "grouped">(defaultLayout);
  // Sessions shown per group before "Show more" — Settings ▸ Appearance ▸ Sidebar.
  const [peek, setPeek] = useState(5);
  useEffect(() => {
    getSettings()
      .then((s) => {
        setLayout(
          s.nav_layout === "flat" ? "flat" : s.nav_layout === "grouped" ? "grouped" : defaultLayout,
        );
        if (s.sessions_peek) setPeek(s.sessions_peek);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const setGroupBy = (next: "flat" | "grouped") => {
    setLayout(next);
    setNavLayout(next).catch(() => {});
  };
  // Chronological RECENT list: cap at RECENT_PEEK with a Show more/less toggle so the sidebar
  // doesn't grow unbounded.
  const RECENT_PEEK = 4;
  const [recentExpanded, setRecentExpanded] = useState(false);
  // The RECENT-header group/filter popover (§20). Filter = show only these personas (empty = all).
  const [groupMenuOpen, setGroupMenuOpen] = useState(false);
  const [filterPersonas, setFilterPersonas] = useState<Set<string>>(new Set());
  const toggleFilterPersona = (id: string) =>
    setFilterPersonas((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  const personaVisible = (agent: string) =>
    filterPersonas.size === 0 || filterPersonas.has(agent);

  // Which accordion body is expanded. Decoupled from the active session (props.agent): expanding
  // a persona BROWSES its sessions without switching the chat area. Selecting a session or "New
  // session" is what switches (and re-opens that persona). Falls back to the active persona.
  const [openKey, setOpenKey] = useState<string | null>(props.agent);
  useEffect(() => setOpenKey(props.agent), [props.agent]);
  const browseKey = openKey ?? props.agent; // the persona whose sessions the body shows

  // Per-project collapse + "Show more". The active workspace's folder is open by default; toggling
  // any folder flips it (XOR). `projShowAll` lifts the peek cap for a given folder;
  // `personaShowAll` does the same for a (non-project) persona's flat session list.
  const [projToggled, setProjToggled] = useState<Set<string>>(new Set());
  const [projShowAll, setProjShowAll] = useState<Set<string>>(new Set());
  const [personaShowAll, setPersonaShowAll] = useState<Set<string>>(new Set());
  const toggleSet = (set: Set<string>, key: string) => {
    const next = new Set(set);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  };

  // Pinned sessions across ALL personas — the cross-persona band at the top (manual pins only).
  const pinnedSessions = props.sessions.filter(
    (s) => s.pinned && !s.session_id.startsWith("__") && !s.archived,
  );
  // §31 (revised 2026-07-21): mention-spawned sessions list chronologically in Recent like any
  // other session — the OriginIcon in the row's indicator cluster marks where they came from.
  // The separate collapsed "From Slack" band hid fresh mentions below week-old sessions.
  // A row in the account menu (§26): closes the menu, then runs the destination.
  const appMenuItem = (
    icon: IconName,
    label: string,
    onClick: () => void,
    active?: boolean,
    trailing?: ReactNode,
  ) => (
    <button
      className={
        "w-full flex items-center gap-2.5 px-3 py-1.5 rounded-md text-[13px] text-left transition-colors " +
        (active ? "text-ink bg-chromeHover font-medium" : "text-muted hover:bg-chromeHover hover:text-ink")
      }
      onClick={() => {
        setAppMenuOpen(false);
        onClick();
      }}
    >
      <Icon name={icon} size={15} className="shrink-0 text-muted" />
      <span className="flex-1">{label}</span>
      {trailing != null && <span aria-hidden>{trailing}</span>}
    </button>
  );

  // Display identity for the account row: the cloud profile only carries the email, so the
  // row shows the capitalized local part ("rohit@…" → "Rohit"); the menu header shows it all.
  const accountEmail = cloud?.signed_in ? cloud.account : "";
  const accountName = accountEmail
    ? accountEmail.split("@")[0].replace(/^./, (c) => c.toUpperCase())
    : "";

  // Roll the per-session attention/liveness up to the persona header and the footer Inbox: the
  // accent count bubbles (sum), the liveness dot aggregates (working wins over sleeping).
  const attnByPersona = new Map<string, number>();
  const liveByPersona = new Map<string, "working" | "sleeping">();
  let totalAttention = 0;
  for (const s of props.sessions) {
    if (s.session_id.startsWith("__") || s.archived) continue;
    const a = s.attention || 0;
    if (a > 0) {
      attnByPersona.set(s.agent, (attnByPersona.get(s.agent) || 0) + a);
      totalAttention += a;
    }
    if (s.liveness === "working") liveByPersona.set(s.agent, "working");
    else if (s.liveness === "sleeping" && liveByPersona.get(s.agent) !== "working")
      liveByPersona.set(s.agent, "sleeping");
  }

  // First pending item ever observed → the inbox chip unlocks and stays (§26 sticky unlock).
  useEffect(() => {
    if (totalAttention > 0 && !inboxUnlocked) {
      localStorage.setItem("ocw:inbox-unlocked", "1");
      setInboxUnlocked(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalAttention]);

  // Body data is keyed to the BROWSED persona (only one body renders at a time). Pinned sessions are
  // EXCLUDED here: they live in the cross-persona Pinned band only, so they don't repeat inside the
  // persona group / project list (matching the flat layout's Recent, which also drops pinned).
  const all = props.sessions.filter(
    (s) =>
      s.agent === browseKey &&
      !s.session_id.startsWith("__") &&
      s.team?.role !== "worker", // workers nest under their lead, never top-level
  );
  const mine = all.filter((s) => !s.archived && !s.pinned);
  const archived = all.filter((s) => s.archived);
  // Only PROJECT-SCOPED personas group sessions by project (git-bound Code, project-bound Ops).
  // Scratch/deliverable conversations are orphan (each has its own per-conversation scratch dir),
  // so they list flat. Workspace-aware (not id-aware) — any git/project persona gets Projects.
  const workspaceSurface = isProjectScoped(personaOf(browseKey));

  // Search now lives in the SearchModal (command-palette overlay), so the sidebar lists never filter
  // in place — these stay constant and the `.filter(matches)` / `normalizedQuery ? …` call sites
  // below are intentional no-ops kept to avoid churn.
  const normalizedQuery = "";
  const matches = (_s: SessionInfo) => true;

  // Recent = every non-pinned, non-archived, real session across ALL personas, newest first
  // (by updated_at; missing timestamps keep store order), search-filtered. Drives the flat layout.
  // Team workers never appear top-level: they nest under their lead's ONE expandable entry.
  const recentSessions = [...props.sessions]
    .filter((s) => !s.archived && !s.session_id.startsWith("__") && !s.pinned)
    .filter((s) => s.team?.role !== "worker")
    .filter((s) => personaVisible(s.agent))
    .filter(matches)
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  // Row actions live behind ONE ⋮ kebab per row (FB-011: four hover icons read as clutter) —
  // the menu offers Rename · Pin/Unpin · Archive/Unarchive · Delete, with the two-step delete
  // confirm kept inside it. Shared by BOTH row styles, so the chronological cardRow offers the
  // same actions as the persona accordion's sessionRow (owner ask 2026-07-09).
  const rowActions = (s: SessionInfo, title: string) => {
    const menuOpen = rowMenu?.id === s.session_id;
    const item = (testid: string, icon: IconName, label: string, onClick: () => void) => (
      <button
        className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[13px] text-left text-muted hover:bg-chromeHover hover:text-ink transition-colors"
        data-testid={testid}
        role="menuitem"
        onClick={() => {
          closeRowMenu();
          onClick();
        }}
      >
        <Icon name={icon} size={13} className="shrink-0 text-muted" />
        <span className="flex-1">{label}</span>
      </button>
    );
    return (
      <span
        className={(menuOpen ? "flex" : "hidden group-hover:flex") + " items-center shrink-0"}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          title={t("sidebar.session_actions")}
          aria-label={t("sidebar.session_actions")}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          data-testid="row-menu"
          className={
            "w-5 h-5 grid place-items-center rounded transition-colors " +
            (menuOpen ? "text-ink bg-chromeHover" : "text-faint hover:text-ink hover:bg-chromeHover")
          }
          onClick={(e) => (menuOpen ? closeRowMenu() : openRowMenu(s.session_id, e.currentTarget))}
        >
          <Icon name="moreHorizontal" size={14} className="rotate-90" />
        </button>
        {menuOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={closeRowMenu} />
            <div
              className="fixed z-50 w-44 rounded-xl border border-line bg-panel shadow-xl py-1 px-1"
              style={{ top: rowMenu!.top, left: rowMenu!.left }}
              role="menu"
            >
              {item("row-menu-rename", "pencil", t("sidebar.rename"), () => {
                setEditingId(s.session_id);
                setEditValue(title);
              })}
              {item("row-menu-pin", "pin", s.pinned ? t("sidebar.unpin") : t("sidebar.pin"), () =>
                props.onTogglePin(s.session_id, !s.pinned),
              )}
              {item("row-menu-archive", "archive", s.archived ? t("sidebar.unarchive") : t("sidebar.archive"), () =>
                props.onArchiveSession(s.session_id, !s.archived),
              )}
              <div className="h-px bg-line my-1 mx-1" />
              {confirmDelId === s.session_id ? (
                <button
                  title={t("sidebar.confirm_delete")}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[13px] text-left font-medium text-danger bg-dangerSoft hover:bg-dangerSoft/80 transition-colors"
                  data-testid="row-menu-delete"
                  role="menuitem"
                  onClick={() => {
                    closeRowMenu();
                    props.onDeleteSession(s.session_id);
                  }}
                >
                  <Icon name="trash" size={13} className="shrink-0" />
                  <span className="flex-1">{t("sidebar.delete_confirm")}</span>
                </button>
              ) : (
                <button
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[13px] text-left text-danger hover:bg-dangerSoft transition-colors"
                  data-testid="row-menu-delete"
                  role="menuitem"
                  onClick={() => setConfirmDelId(s.session_id)}
                >
                  <Icon name="trash" size={13} className="shrink-0" />
                  <span className="flex-1">{t("sidebar.delete")}</span>
                </button>
              )}
            </div>
          </>
        )}
      </span>
    );
  };

  // A compact session row (OpenWork style): 16px glyph lane + title + indicators + ⋮
  const sessionRow = (s: SessionInfo, opts: { showTime?: boolean } = {}) => {
    const title = s.title || s.session_id;
    const editing = editingId === s.session_id;
    const active = s.session_id === props.activeSession;
    const commitRename = () => {
      const next = editValue.trim();
      if (next && next !== title) props.onRenameSession(s.session_id, next);
      setEditingId(null);
    };
    return (
      <div
        key={s.session_id}
        className={
          "group relative flex h-8 items-center gap-2 px-2.5 rounded-lg text-left cursor-pointer transition-all select-none " +
          (active
            ? "bg-dls-hover font-medium text-dls-text shadow-sm"
            : "text-dls-secondary hover:bg-dls-hover hover:text-dls-text")
        }
        onClick={() => {
          if (!editing) props.onSelectSession(s.session_id, s.workspace, s.agent);
        }}
        title={editing ? undefined : title}
      >
        {editing ? (
          <input
            className="flex-1 min-w-0 px-2 py-0.5 rounded bg-panel border border-accent text-[13px] text-ink outline-none"
            value={editValue}
            autoFocus
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") commitRename();
              else if (e.key === "Escape") setEditingId(null);
            }}
          />
        ) : (
          <>
            <span className="min-w-0 flex-1 truncate text-[13px] leading-none">
              {title}
            </span>
            <span
              className={
                "flex items-center gap-1.5 shrink-0 group-hover:hidden" +
                (rowMenu?.id === s.session_id ? " hidden" : "")
              }
            >
              {s.pinned && <Icon name="pin" size={12} className="text-faint shrink-0" />}
              {opts.showTime && compactAge(s.updated_at) && (
                <span className="text-[11px] text-faint tabular-nums">{compactAge(s.updated_at)}</span>
              )}
              <OriginIcon s={s} />
              <LiveDot state={s.liveness} />
              <AttnBadge n={s.attention || 0} />
            </span>
            {rowActions(s, title)}
          </>
        )}
      </div>
    );
  };

  // A single-line card row matching OpenWork sidebar session row
  const cardRow = (s: SessionInfo) => {
    const active = s.session_id === props.activeSession;
    const title = s.title || s.session_id;
    const editing = editingId === s.session_id;
    const commitRename = () => {
      const next = editValue.trim();
      if (next && next !== title) props.onRenameSession(s.session_id, next);
      setEditingId(null);
    };
    return (
      <div
        key={s.session_id}
        className={
          "group relative flex h-8 w-full items-center gap-2 px-2.5 rounded-lg cursor-pointer text-left transition-all select-none " +
          (active
            ? "bg-dls-hover font-medium text-dls-text shadow-sm"
            : "text-dls-secondary hover:bg-dls-hover hover:text-dls-text")
        }
        title={editing ? undefined : title}
        onClick={() => {
          if (!editing) props.onSelectSession(s.session_id, s.workspace, s.agent);
        }}
      >
        {editing ? (
          <input
            className="flex-1 min-w-0 px-2 py-0.5 rounded bg-panel border border-accent text-[13px] text-ink outline-none"
            value={editValue}
            autoFocus
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") commitRename();
              else if (e.key === "Escape") setEditingId(null);
            }}
          />
        ) : (
          <>
            <span className="min-w-0 flex-1 truncate text-[13px] leading-none">
              {title}
            </span>
            <span
              className={
                "flex items-center gap-1.5 shrink-0 group-hover:hidden" +
                (rowMenu?.id === s.session_id ? " hidden" : "")
              }
            >
              {s.pinned && <Icon name="pin" size={12} className="text-faint shrink-0" />}
              <OriginIcon s={s} />
              <ConnectorDot subs={s.subscriptions} />
              <LiveDot state={s.liveness} />
              <AttnBadge n={s.attention || 0} />
            </span>
            {rowActions(s, title)}
          </>
        )}
      </div>
    );
  };

  // The cross-persona Pinned band (manual pins only) — icon-free rows. Appears in BOTH layouts
  // The cross-persona Pinned band (manual pins only)
  const pinnedBand = () =>
    pinnedSessions.length > 0 ? (
      <div>
        <div className="flex h-6 items-center px-2.5 text-[11px] font-normal text-faint">
          {t("sidebar.pinned")}
        </div>
        <div className="space-y-0.5">
          {pinnedSessions.map((s) => cardRow(s))}
        </div>
      </div>
    ) : null;

  // UX-023: the Scheduled band — ONE entry per automation
  const scheduledBand = () =>
    automations.length > 0 ? (
      <div data-testid="scheduled-band">
        <div className="flex h-6 items-center px-2.5 text-[11px] font-normal text-faint">
          {t("sidebar.scheduled")}
        </div>
        <div className="space-y-0.5">
          {automations.map((a) => (
            <button
              key={a.id}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-left text-muted hover:bg-chromeHover hover:text-ink transition-colors"
              data-testid={`scheduled-${a.id}`}
              title={a.title}
              onClick={() => props.onOpenAutomation(a.id)}
            >
              <GlyphSlot>
                <Icon name="clock" size={14} className="text-muted" />
              </GlyphSlot>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-ink truncate font-medium">{a.title}</div>
                <div className="text-[11px] text-faint truncate">{a.schedule}</div>
              </div>
              <UnseenBadge n={a.unseen_runs || 0} failed={a.unseen_failed} />
            </button>
          ))}
        </div>
      </div>
    ) : null;

  // RECENT header with the group/filter control
  const recentHeader = () => {
    const filterPersonaList = (personas || []).filter(
      (p) => (p.enabled && p.surfaced) || agentsWithSessions.has(p.id),
    );
    return (
    <div className="relative flex h-6 items-center justify-between px-2.5" data-testid="recent-header">
      <span className="text-[11px] font-normal text-faint">
        {t("sidebar.recent")}
      </span>
      <button
        className="w-5 h-5 grid place-items-center rounded text-faint hover:text-ink hover:bg-chromeHover -mr-1 transition-colors"
        title={t("sidebar.group_and_filter_short")}
        aria-label={t("sidebar.group_and_filter")}
        onClick={() => setGroupMenuOpen((v) => !v)}
      >
        <Icon name="sliders" size={13} />
      </button>
      {groupMenuOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setGroupMenuOpen(false)} />
          <div
            className="absolute right-0 top-7 z-50 w-56 rounded-xl border border-line bg-panel shadow-xl p-1.5"
            role="menu"
            data-testid="group-filter-menu"
          >
            <div className="px-2 pt-1 pb-1 text-[11px] font-normal text-faint">
              {t("sidebar.group_by")}
            </div>
            {([["grouped", t("sidebar.group_persona")], ["flat", t("sidebar.group_chrono")]] as ["flat" | "grouped", string][]).map(
              ([key, label]) => (
                <button
                  key={key}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[13px] text-left hover:bg-paper"
                  onClick={() => setGroupBy(key)}
                >
                  <span className="flex-1">{label}</span>
                  {layout === key && <span className="text-accent text-[12px]">✓</span>}
                </button>
              ),
            )}
            {filterPersonaList.length > 1 && (
              <>
                <div className="my-1 border-t border-line" />
                <div className="px-2 pt-1 pb-1 flex items-center justify-between">
                  <span className="text-[11px] font-normal text-faint">
                    {t("sidebar.filter_coworker")}
                  </span>
                  {filterPersonas.size > 0 && (
                    <button className="text-[11px] text-accent hover:underline" onClick={() => setFilterPersonas(new Set())}>
                      {t("sidebar.clear")}
                    </button>
                  )}
                </div>
                <div className="max-h-52 overflow-y-auto">
                  {filterPersonaList.map((p) => {
                    const checked = filterPersonas.has(p.id);
                    return (
                      <button
                        key={p.id}
                        className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[13px] text-left hover:bg-paper"
                        onClick={() => toggleFilterPersona(p.id)}
                      >
                        <span
                          className={
                            "w-3.5 h-3.5 rounded border grid place-items-center shrink-0 text-white " +
                            (checked ? "bg-accent border-accent" : "border-line")
                          }
                        >
                          {checked && <span className="text-[9px] leading-none">✓</span>}
                        </span>
                        <span className="flex-1 truncate">{p.name}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="px-2 pt-1 pb-0.5 text-[11px] text-faint leading-snug">
                  {t("sidebar.filter_all_hint")}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
    );
  };
  // Code/Cowork group by project; Chat is a flat recents list.
  const byProject = useMemo(() => {
    const grouped = new Map<string, SessionInfo[]>();
    for (const s of mine) {
      if (!grouped.has(s.workspace)) grouped.set(s.workspace, []);
      grouped.get(s.workspace)!.push(s);
    }
    return grouped;
  }, [mine]);

  const filteredByProject = useMemo(() => {
    const grouped = new Map<string, SessionInfo[]>();
    for (const [proj, list] of byProject) grouped.set(proj, list.filter(matches));
    return grouped;
  }, [byProject, normalizedQuery]);

  // Projects are tracked PER SURFACE: a folder appears under Code only if it has Code sessions,
  // under Cowork only if it has Cowork sessions (+ the currently-open folder). No cross-bleed.
  const projectOrder: string[] = [];
  const seen = new Set<string>();
  // Pin the active folder at top only when browsing the active persona (else it belongs elsewhere).
  if (props.workspace && browseKey === props.agent) {
    projectOrder.push(props.workspace);
    seen.add(props.workspace);
  }
  for (const s of mine) {
    if (s.workspace && !seen.has(s.workspace)) {
      seen.add(s.workspace);
      projectOrder.push(s.workspace);
    }
  }

  // Surfaced + enabled personas drive the surface list (default persona first); fall back to the
  // static set until loaded. A persona that has live sessions ALWAYS gets a section, surfaced or
  // not — every session must have a home in the grouped layout (a picker preference can hide the
  // persona from New Session, never orphan its conversations).
  const agentsWithSessions = new Set(
    props.sessions
      .filter(
        (s) =>
          !s.archived &&
          !s.session_id.startsWith("__") &&
          s.team?.role !== "worker",
      )
      .map((s) => s.agent),
  );
  const visibleSurfaces = (
    personas
      ? personas
          .filter((p) => (p.enabled && p.surfaced) || agentsWithSessions.has(p.id))
          .sort((a, b) => Number(b.default) - Number(a.default)) // default leads
          .map(surfaceFromPersona)
      : SURFACES.filter(
          (s) => s.key === "cowork" || props.surfaces[s.key as keyof SurfaceVisibility],
        )
  ).filter((s) => personaVisible(s.key));

  const isCurrent = (key: string) => props.agent === key; // the active session's persona
  const isExpanded = (key: string) => openKey === key; // its body is open
  // Expand ≠ switch: clicking a header only browses (toggles the accordion). The chat area
  // changes only when a session is selected or "New session" is clicked.
  const onHeaderClick = (key: string) => setOpenKey((k) => (k === key ? null : key));

  // The expanded body for the active surface: a "New session" action, then the project-grouped
  // (or flat) session list, then the archived disclosure.
  const surfaceBody = () => {
    return (
      <div className="space-y-1 px-1.5 pb-2 pt-0.5">
        {workspaceSurface ? (
          <>
            <div className="flex h-6 items-center justify-between px-2.5 pt-1 mb-0.5">
              <span className="text-[11px] font-normal text-faint">
                {t("sidebar.projects")}
              </span>
              <button
                className="flex size-5 items-center justify-center rounded text-faint hover:text-ink hover:bg-chromeHover transition-colors"
                title={t("sidebar.new_project")}
                aria-label={t("sidebar.new_project")}
                onClick={() => props.onNewProject(browseKey)}
              >
                <Icon name="folderPlus" size={13} />
              </button>
            </div>
            <div className="space-y-0.5">
              {projectOrder.length === 0 && (
                <div className="px-2.5 py-1.5 text-[12px] text-faint leading-snug">
                  {t("sidebar.no_projects_yet")}
                </div>
              )}
              {projectOrder.map((proj) => {
                const list = filteredByProject.get(proj) || [];
                if (normalizedQuery && list.length === 0) return null;
                const isActive = proj === props.workspace;
                const activeInOrder = !!props.workspace && projectOrder.includes(props.workspace);
                const defaultOpen = isActive || (!activeInOrder && proj === projectOrder[0]);
                const open = !!normalizedQuery || defaultOpen !== projToggled.has(proj);
                const showAll = !!normalizedQuery || projShowAll.has(proj);
                const shown = showAll ? list : list.slice(0, peek);
                return (
                  <div key={proj}>
                    <div
                      className={
                        "flex h-8 items-center gap-2 px-2.5 rounded-md cursor-pointer select-none transition-colors " +
                        (isActive ? "text-ink font-medium bg-chromeHover" : "text-muted hover:bg-chromeHover hover:text-ink")
                      }
                      onClick={() => setProjToggled((s) => toggleSet(s, proj))}
                      title={proj}
                    >
                      <span className="flex size-2 shrink-0 rounded-full bg-accent/80" />
                      <span
                        className={
                          "truncate min-w-0 flex-1 text-[13px] " + (isActive ? "font-medium text-ink" : "text-muted")
                        }
                      >
                        {baseName(proj)}
                      </span>
                      <Icon
                        name={open ? "chevronDown" : "chevronRight"}
                        size={12}
                        className="text-faint shrink-0 ml-auto"
                      />
                    </div>
                    {open &&
                      (list.length > 0 ? (
                        <div className="space-y-0.5 pl-3 border-l border-line ml-3.5 my-1">
                          {shown.map((s) => sessionRow(s, { showTime: true }))}
                          {!showAll && list.length > peek && (
                            <button
                              className="px-2 py-1 text-[12px] text-faint hover:text-muted transition-colors"
                              onClick={() => setProjShowAll((s) => toggleSet(s, proj))}
                            >
                              {t("sidebar.show_more_n", { n: list.length - peek })}
                            </button>
                          )}
                        </div>
                      ) : (
                        <div className="px-2.5 py-1.5 pl-3 text-[12px] text-faint leading-snug">
                          {t("sidebar.no_project_convos")}
                        </div>
                      ))}
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div className="space-y-0.5">
            {mine.filter(matches).length === 0 ? (
              <div className="px-2.5 py-1.5 text-[12px] text-faint leading-snug">
                {normalizedQuery ? t("sidebar.no_matching") : t("sidebar.no_conversations")}
              </div>
            ) : (
              <>
                {(personaShowAll.has(browseKey)
                  ? mine.filter(matches)
                  : mine.filter(matches).slice(0, peek)
                ).map((s) => sessionRow(s))}
                {!personaShowAll.has(browseKey) && mine.filter(matches).length > peek && (
                  <button
                    className="px-2.5 py-1 text-[12px] text-faint hover:text-muted transition-colors"
                    onClick={() => setPersonaShowAll((s) => toggleSet(s, browseKey))}
                  >
                    {t("sidebar.show_more_n", { n: mine.filter(matches).length - peek })}
                  </button>
                )}
              </>
            )}
          </div>
        )}

        {archived.length > 0 && (
          <div className="mt-2 pt-1.5 border-t border-line">
            <button
              className="w-full flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[12px] text-faint hover:text-ink transition-colors"
              onClick={() => setShowArchived((v) => !v)}
            >
              <Icon name={showArchived ? "chevronDown" : "chevronRight"} size={13} className="shrink-0" />
              {t("sidebar.archived_n", { n: archived.length })}
            </button>
            {showArchived && (
              <div className="space-y-0.5 mt-0.5">{archived.filter(matches).map((s) => sessionRow(s))}</div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      className="sidebar flex flex-col min-h-0 bg-dls-sidebar text-dls-text select-none border-r border-dls-border"
      onMouseLeave={props.onPeekLeave}
    >
      {/* Top action list matching OpenWork primary navigation */}
      <div className="px-2 pt-2.5 space-y-0.5">
        {/* New Task / New Session */}
        <button
          className="group w-full flex h-8 items-center gap-2 px-2.5 rounded-lg text-[13px] text-left text-dls-text font-medium hover:bg-dls-hover active:scale-[0.98] transition-all"
          onClick={() => props.onNewSession(props.agent)}
        >
          <GlyphSlot>
            <Icon name="plus" size={15} className="text-dls-secondary group-hover:text-dls-text transition-colors" />
          </GlyphSlot>
          <span className="flex-1 truncate">{t("sidebar.new_session") || "New session"}</span>
        </button>

        {/* Search */}
        <button
          className="group w-full flex h-8 items-center gap-2 px-2.5 rounded-lg text-[13px] text-left text-dls-secondary hover:bg-dls-hover hover:text-dls-text active:scale-[0.98] transition-all"
          onClick={() => setSearchModalOpen(true)}
        >
          <GlyphSlot>
            <Icon name="search" size={15} className="text-dls-secondary group-hover:text-dls-text transition-colors" />
          </GlyphSlot>
          <span className="flex-1 truncate">{t("sidebar.search") || "Search sessions..."}</span>
        </button>

        {/* Automations */}
        <button
          className={
            "group w-full flex h-8 items-center gap-2 px-2.5 rounded-lg text-[13px] text-left active:scale-[0.98] transition-all " +
            (props.scheduledActive
              ? "text-dls-text font-medium bg-dls-hover"
              : "text-dls-secondary hover:bg-dls-hover hover:text-dls-text")
          }
          data-testid="nav-automations"
          onClick={props.onOpenScheduled}
        >
          <GlyphSlot>
            <Icon
              name="clock"
              size={15}
              className={props.scheduledActive ? "text-dls-text" : "text-dls-secondary group-hover:text-dls-text transition-colors"}
            />
          </GlyphSlot>
          <span className="flex-1 truncate">{t("sidebar.automations") || "Automations"}</span>
        </button>

        {/* Plugin / Connectors */}
        <button
          className={
            "group w-full flex h-8 items-center gap-2 px-2.5 rounded-lg text-[13px] text-left active:scale-[0.98] transition-all " +
            (props.integrationsActive
              ? "text-dls-text font-medium bg-dls-hover"
              : "text-dls-secondary hover:bg-dls-hover hover:text-dls-text")
          }
          data-testid="nav-integrations"
          onClick={props.onOpenIntegrations}
        >
          <GlyphSlot>
            <Icon
              name="plug"
              size={15}
              className={props.integrationsActive ? "text-dls-text" : "text-dls-secondary group-hover:text-dls-text transition-colors"}
            />
          </GlyphSlot>
          <span className="flex-1 truncate">Plugin</span>
        </button>

        {/* Team */}
        <button
          className={
            "group w-full flex h-8 items-center gap-2 px-2.5 rounded-lg text-[13px] text-left active:scale-[0.98] transition-all " +
            (props.teamActive
              ? "text-dls-text font-medium bg-dls-hover"
              : "text-dls-secondary hover:bg-dls-hover hover:text-dls-text")
          }
          data-testid="nav-team"
          onClick={props.onOpenTeam}
        >
          <GlyphSlot>
            <Icon
              name="users"
              size={15}
              className={props.teamActive ? "text-dls-text" : "text-dls-secondary group-hover:text-dls-text transition-colors"}
            />
          </GlyphSlot>
          <span className="flex-1 truncate">{t("nav.team") || "Team"}</span>
        </button>
      </div>

      {/* Workspaces & Sessions Area */}
      {!props.teamActive && (
        <div className="flex-1 overflow-y-auto px-2 mt-4 pb-2">
          <div className="space-y-4">
            {pinnedBand()}
            {scheduledBand()}
            <div>
              {recentHeader()}
              {layout === "grouped" ? (
                <div className="space-y-1 mt-1">
                  {visibleSurfaces.map((s) => {
                    const expanded = isExpanded(s.key);
                    return (
                      <div key={s.key}>
                        <div
                          className={
                            "flex h-8 items-center gap-2 px-2.5 rounded-md cursor-pointer select-none transition-colors " +
                            (expanded
                              ? "text-ink font-medium bg-chromeHover"
                              : isCurrent(s.key)
                                ? "text-ink font-medium bg-chromeHover/60"
                                : "text-muted hover:bg-chromeHover hover:text-ink")
                          }
                          onClick={() => onHeaderClick(s.key)}
                        >
                          <span className="flex size-2 shrink-0 rounded-full bg-rose-500" />
                          <span className="min-w-0 flex-1 truncate text-[13px]">
                            {s.label}
                          </span>
                          <LiveDot state={liveByPersona.get(s.key)} />
                          <AttnBadge n={attnByPersona.get(s.key) || 0} />
                          <Icon
                            name={expanded ? "chevronDown" : "chevronRight"}
                            size={12}
                            className="text-faint shrink-0 ml-auto"
                          />
                        </div>
                        {expanded && surfaceBody()}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="space-y-0.5 mt-1">
                  {recentSessions.length === 0 ? (
                    <div className="px-2.5 py-2 text-[12px] text-faint leading-snug">
                      {normalizedQuery ? t("sidebar.no_matching") : t("sidebar.no_conversations")}
                    </div>
                  ) : (
                    <>
                      {(recentExpanded
                        ? recentSessions
                        : recentSessions.slice(0, RECENT_PEEK)
                      ).map((s) => cardRow(s))}
                      {recentSessions.length > RECENT_PEEK && (
                        <button
                          className="w-full text-left px-2.5 py-1 text-[12px] text-faint hover:text-muted transition-colors"
                          onClick={() => setRecentExpanded((v) => !v)}
                        >
                          {recentExpanded
                            ? t("sidebar.show_less")
                            : t("sidebar.show_n_more", { n: recentSessions.length - RECENT_PEEK })}
                        </button>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {props.teamActive && <div className="flex-1" aria-hidden="true" />}

      {/* Footer: User Profile / Account Switcher */}
      <div className="p-2 border-t border-line mt-auto">
        <div className="relative">
          {appMenuOpen && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => setAppMenuOpen(false)} />
              <div
                className="absolute z-40 bottom-full left-0 right-0 mb-1.5 rounded-xl border border-line bg-panel shadow-2xl p-1.5"
                data-testid="account-menu"
                role="menu"
              >
                {cloud?.signed_in ? (
                  <div
                    className="px-3 py-1.5 mb-1 text-[11px] text-faint truncate border-b border-line"
                    title={accountEmail}
                  >
                    {accountEmail}
                  </div>
                ) : (
                  <>
                    <div className="px-3 py-1.5 text-[11px] text-faint border-b border-line">
                      {t("sidebar.not_signed_in")}
                    </div>
                    <button
                      className="w-full flex items-center gap-2.5 px-3 py-1.5 mb-1 rounded-md text-[13px] text-left text-accent hover:bg-paper transition-colors font-medium"
                      data-testid="account-sign-in"
                      onClick={() => {
                        setAppMenuOpen(false);
                        if (props.onOpenCloudSignIn) {
                          props.onOpenCloudSignIn();
                          return;
                        }
                        void cloudLogin().catch(() => {});
                        waitForCloudSignIn((s) => {
                          if (s) setCloud(s);
                          if (s?.signed_in) announceCloudChanged();
                        });
                      }}
                    >
                      <Icon name="plug" size={15} className="shrink-0 text-accent" /> {t("sidebar.sign_in")}
                    </button>
                  </>
                )}
                {appMenuItem(
                  "inbox",
                  t("nav.inbox"),
                  props.onOpenInbox,
                  props.inboxActive,
                  <AttnBadge n={totalAttention} />,
                )}
                <div className="h-px bg-line my-1 mx-1" />
                {appMenuItem(
                  "gear",
                  t("nav.settings"),
                  props.onManage,
                  false,
                  <span className="text-[11px] text-faint">⌘ ,</span>,
                )}
                {appMenuItem("audit", t("nav.activity"), props.onOpenAudit, props.auditActive)}
                {cloud?.signed_in && (
                  <>
                    <div className="h-px bg-line my-1 mx-1" />
                    {appMenuItem("signOut", t("sidebar.sign_out"), async () => {
                      await cloudLogout().catch(() => {});
                      announceCloudChanged();
                    })}
                  </>
                )}
              </div>
            </>
          )}

          {/* OpenWork style User button: circle avatar + Name / email + kebab */}
          <button
            className={
              "w-full flex items-center gap-2.5 px-2 py-1.5 rounded-lg text-left transition-all " +
              (appMenuOpen ? "bg-dls-hover" : "hover:bg-dls-hover")
            }
            data-testid="account-row"
            onClick={() => {
              if (!appMenuOpen) refreshCloud();
              setAppMenuOpen((v) => !v);
            }}
            aria-haspopup="menu"
            aria-expanded={appMenuOpen}
            aria-label={cloud?.signed_in ? t("sidebar.account_aria", { email: accountEmail }) : t("sidebar.account_not_signed_in_aria")}
          >
            <span
              className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent/10 text-[11px] font-semibold text-accent"
              aria-hidden
            >
              {cloud?.signed_in ? accountName.slice(0, 2).toUpperCase() : "PT"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12.5px] font-medium text-dls-text leading-tight">
                {cloud?.signed_in ? accountName : "PhongTran"}
              </span>
              <span className="block truncate text-[11px] leading-tight text-dls-secondary">
                {cloud?.signed_in ? accountEmail : "admin123@gmail.com"}
              </span>
            </span>
            <Icon
              name="moreHorizontal"
              size={15}
              className="text-dls-secondary shrink-0"
            />
          </button>
        </div>
      </div>

      {searchModalOpen && (
        <SearchModal
          sessions={props.sessions}
          personas={personas ?? undefined}
          onSelect={(id, ws, ag) => {
            setSearchModalOpen(false);
            props.onSelectSession(id, ws, ag);
          }}
          onSelectTeam={props.onSelectTeamSearch}
          onClose={() => setSearchModalOpen(false)}
        />
      )}
    </div>
  );
}
