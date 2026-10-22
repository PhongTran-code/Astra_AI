import { useEffect, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  getTeamComputers,
  getTeamComputerSession,
  createTeamComputerSession,
  reconnectTeamComputerSession,
  getTeamComputerObservation,
  getTeamComputerScreenshot,
  navigateTeamComputerSession,
  sendTeamComputerAction,
  acquireTeamScreenLease,
  releaseTeamScreenLease,
  type TeamComputer,
  type TeamComputerSession,
  type TeamComputerObservation,
  type TeamScreenLease,
} from "../api";
import { Icon } from "./Icon";

interface TeamComputerControlProps {
  initialComputerId?: string;
  initialSessionId?: string;
  onClose?: () => void;
}

export function TeamComputerControl({
  initialComputerId,
  initialSessionId,
  onClose,
}: TeamComputerControlProps) {
  const { t } = useTranslation();
  const [computers, setComputers] = useState<TeamComputer[]>([]);
  const [selectedComputerId, setSelectedComputerId] = useState<string>(initialComputerId || "");
  const [session, setSession] = useState<TeamComputerSession | null>(null);
  const [observation, setObservation] = useState<TeamComputerObservation | null>(null);
  const [screenshotB64, setScreenshotB64] = useState<string>("");
  const [activeLease, setActiveLease] = useState<TeamScreenLease | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState("");
  const [urlInput, setUrlInput] = useState("https://google.com");
  const [typeInput, setTypeInput] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastActionStatus, setLastActionStatus] = useState("");

  const imageRef = useRef<HTMLImageElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Load computers on mount
  useEffect(() => {
    async function loadComputers() {
      try {
        const res = await getTeamComputers({ limit: 50 });
        if (res.computers && res.computers.length > 0) {
          setComputers(res.computers);
          if (!selectedComputerId) {
            setSelectedComputerId(res.computers[0].computer_id);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : t("team_computer.load_error"));
      }
    }
    void loadComputers();
  }, [selectedComputerId, t]);

  // Create or attach to session
  const initSession = useCallback(async (computerId: string) => {
    if (!computerId) return;
    setLoading(true);
    setError("");
    try {
      if (initialSessionId) {
        const existing = await getTeamComputerSession(initialSessionId);
        if (existing.error) throw new Error(existing.error);
        setSession(existing);
        await refreshScreen(existing.session_id);
      } else {
        const created = await createTeamComputerSession(computerId);
        if (created.error) throw new Error(created.error);
        setSession(created);
        await refreshScreen(created.session_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.session_error"));
    } finally {
      setLoading(false);
    }
  }, [initialSessionId, t]);

  useEffect(() => {
    if (selectedComputerId && !session) {
      void initSession(selectedComputerId);
    }
  }, [selectedComputerId, session, initSession]);

  // Refresh observation / screenshot
  const refreshScreen = async (sessionId: string) => {
    try {
      const obs = await getTeamComputerObservation(sessionId);
      if (obs && !obs.error) {
        setObservation(obs);
        if (obs.url) setUrlInput(obs.url);
        if (obs.screenshot_b64) {
          setScreenshotB64(obs.screenshot_b64);
        } else {
          const shot = await getTeamComputerScreenshot(sessionId);
          if (shot.data_b64) setScreenshotB64(shot.data_b64);
        }
      }
    } catch {
      // Ignored for polling
    }
  };

  // Auto polling
  useEffect(() => {
    if (!autoRefresh || !session || session.status !== "active") return;
    const interval = setInterval(() => {
      void refreshScreen(session.session_id);
    }, 2000);
    return () => clearInterval(interval);
  }, [autoRefresh, session]);

  // Screen Lease helpers
  const ensureLease = async (): Promise<string> => {
    if (activeLease && activeLease.session_id === session?.session_id) {
      const expires = new Date(activeLease.expires_at).getTime();
      if (expires > Date.now() + 5000) {
        return activeLease.lease_id;
      }
    }
    if (!session) throw new Error("No active session");
    const acquired = await acquireTeamScreenLease(session.session_id, 120);
    if (acquired.error) throw new Error(acquired.error);
    setActiveLease(acquired);
    return acquired.lease_id;
  };

  const handleTakeOverScreen = async () => {
    if (!session) return;
    setActionBusy(true);
    setError("");
    try {
      const lease = await acquireTeamScreenLease(session.session_id, 300);
      if (lease.error) throw new Error(lease.error);
      setActiveLease(lease);
      setLastActionStatus(t("team_computer.lease_acquired"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.lease_error"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleReleaseScreen = async () => {
    if (!session || !activeLease) return;
    setActionBusy(true);
    try {
      await releaseTeamScreenLease(session.session_id, activeLease.lease_id);
      setActiveLease(null);
      setLastActionStatus(t("team_computer.lease_released"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.lease_error"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleReconnect = async () => {
    if (!session) return;
    setActionBusy(true);
    setError("");
    try {
      const reconnected = await reconnectTeamComputerSession(session.session_id);
      if (reconnected.error) throw new Error(reconnected.error);
      setSession(reconnected);
      await refreshScreen(reconnected.session_id);
      setLastActionStatus(t("team_computer.reconnected"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.reconnect_error"));
    } finally {
      setActionBusy(false);
    }
  };

  // Browser Navigation
  const handleNavigate = async () => {
    if (!session || !urlInput.trim()) return;
    setActionBusy(true);
    setError("");
    try {
      const res = await navigateTeamComputerSession(session.session_id, urlInput.trim());
      if (res.error) throw new Error(res.error);
      setObservation(res);
      if (res.screenshot_b64) setScreenshotB64(res.screenshot_b64);
      setLastActionStatus(`${t("team_computer.navigated_to")}: ${urlInput}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.navigate_error"));
    } finally {
      setActionBusy(false);
    }
  };

  // Actions
  const handleCanvasClick = async (event: React.MouseEvent<HTMLImageElement>) => {
    if (!session || !imageRef.current || actionBusy) return;
    const rect = imageRef.current.getBoundingClientRect();
    const naturalWidth = imageRef.current.naturalWidth || session.viewport?.width || 1280;
    const naturalHeight = imageRef.current.naturalHeight || session.viewport?.height || 800;

    const clickX = event.clientX - rect.left;
    const clickY = event.clientY - rect.top;

    const scaleX = naturalWidth / rect.width;
    const scaleY = naturalHeight / rect.height;

    const targetX = Math.round(clickX * scaleX);
    const targetY = Math.round(clickY * scaleY);

    setActionBusy(true);
    try {
      const leaseId = await ensureLease();
      const res = await sendTeamComputerAction(
        session.session_id,
        { action: "click", coordinate: [targetX, targetY] },
        leaseId
      );
      if (res.error) throw new Error(res.error);
      setObservation(res);
      if (res.screenshot_b64) setScreenshotB64(res.screenshot_b64);
      setLastActionStatus(`${t("team_computer.clicked_at")} (${targetX}, ${targetY})`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.action_error"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleTypeText = async () => {
    if (!session || !typeInput || actionBusy) return;
    setActionBusy(true);
    try {
      const leaseId = await ensureLease();
      const res = await sendTeamComputerAction(
        session.session_id,
        { action: "type", text: typeInput },
        leaseId
      );
      if (res.error) throw new Error(res.error);
      setObservation(res);
      if (res.screenshot_b64) setScreenshotB64(res.screenshot_b64);
      setTypeInput("");
      setLastActionStatus(t("team_computer.typed_text"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.action_error"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleKeyPress = async (key: string) => {
    if (!session || actionBusy) return;
    setActionBusy(true);
    try {
      const leaseId = await ensureLease();
      const res = await sendTeamComputerAction(
        session.session_id,
        { action: "key", text: key },
        leaseId
      );
      if (res.error) throw new Error(res.error);
      setObservation(res);
      if (res.screenshot_b64) setScreenshotB64(res.screenshot_b64);
      setLastActionStatus(`${t("team_computer.pressed_key")}: ${key}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.action_error"));
    } finally {
      setActionBusy(false);
    }
  };

  const handleScroll = async (direction: "up" | "down") => {
    if (!session || actionBusy) return;
    setActionBusy(true);
    try {
      const leaseId = await ensureLease();
      const delta = direction === "down" ? 300 : -300;
      const res = await sendTeamComputerAction(
        session.session_id,
        { action: "scroll", delta_y: delta },
        leaseId
      );
      if (res.error) throw new Error(res.error);
      setObservation(res);
      if (res.screenshot_b64) setScreenshotB64(res.screenshot_b64);
      setLastActionStatus(`${t("team_computer.scrolled")} ${direction}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("team_computer.action_error"));
    } finally {
      setActionBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col bg-canvas text-ink">
      {/* Top Header */}
      <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2.5">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 rounded-full bg-ok" />
            <h2 className="text-sm font-semibold text-ink">{t("team_computer.title")}</h2>
          </div>
          {computers.length > 1 && (
            <select
              className="rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink outline-none"
              value={selectedComputerId}
              onChange={(e) => {
                setSelectedComputerId(e.target.value);
                setSession(null);
              }}
            >
              {computers.map((comp) => (
                <option key={comp.computer_id} value={comp.computer_id}>
                  {comp.computer_id} ({comp.provider_kind})
                </option>
              ))}
            </select>
          )}
          {session && (
            <span className="rounded bg-accentSoft px-2 py-0.5 text-[10px] font-mono text-accent">
              {session.session_id}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Screen Lease Control */}
          {activeLease ? (
            <div className="flex items-center gap-2 rounded-md bg-warningSoft px-2.5 py-1 text-xs text-warning">
              <span>{t("team_computer.human_controlling")}</span>
              <button
                type="button"
                className="rounded bg-warning px-2 py-0.5 text-[11px] font-medium text-white hover:opacity-90"
                disabled={actionBusy}
                onClick={() => void handleReleaseScreen()}
              >
                {t("team_computer.release_to_bot")}
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn secondary text-xs"
              disabled={actionBusy || !session}
              onClick={() => void handleTakeOverScreen()}
            >
              <Icon name="users" size={13} className="mr-1.5 inline" />
              {t("team_computer.take_over_screen")}
            </button>
          )}

          <button
            type="button"
            className="btn secondary text-xs"
            disabled={actionBusy || !session}
            onClick={() => void handleReconnect()}
          >
            <Icon name="refresh" size={13} className="mr-1.5 inline" />
            {t("team_computer.reconnect")}
          </button>

          <button
            type="button"
            className={`btn secondary text-xs ${autoRefresh ? "text-accent font-medium" : "text-muted"}`}
            onClick={() => setAutoRefresh(!autoRefresh)}
            title={t("team_computer.toggle_auto_refresh")}
          >
            <Icon name="refresh" size={13} className={`mr-1 inline ${autoRefresh ? "animate-spin" : ""}`} />
            {autoRefresh ? t("team_computer.live") : t("team_computer.paused")}
          </button>

          {onClose && (
            <button
              type="button"
              className="rounded p-1 text-muted hover:bg-surface hover:text-ink"
              onClick={onClose}
              aria-label={t("common.close")}
            >
              <Icon name="x" size={15} />
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className="border-b border-danger/30 bg-danger/5 px-4 py-2 text-xs text-danger" role="alert">
          {error}
        </div>
      )}

      {/* Browser URL bar & Actions */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2 text-xs">
        <button
          type="button"
          className="rounded border border-line bg-panel p-1.5 text-muted hover:text-ink disabled:opacity-40"
          disabled={actionBusy || !session}
          onClick={() => session && void refreshScreen(session.session_id)}
          title={t("team_computer.reload")}
        >
          <Icon name="refresh" size={13} />
        </button>

        <div className="flex min-w-[280px] flex-1 items-center rounded-md border border-line bg-panel px-2.5 py-1">
          <Icon name="search" size={13} className="mr-2 text-muted" />
          <input
            type="url"
            className="w-full bg-transparent text-xs text-ink outline-none"
            placeholder="https://example.com"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void handleNavigate()}
          />
        </div>

        <button
          type="button"
          className="btn primary text-xs"
          disabled={actionBusy || !session || !urlInput.trim()}
          onClick={() => void handleNavigate()}
        >
          {t("team_computer.go")}
        </button>

        <div className="h-4 w-px bg-line" />

        {/* Text Input & Key Tools */}
        <div className="flex items-center gap-1.5">
          <input
            type="text"
            className="w-40 rounded-md border border-line bg-panel px-2 py-1 text-xs text-ink outline-none"
            placeholder={t("team_computer.type_placeholder")}
            value={typeInput}
            onChange={(e) => setTypeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void handleTypeText()}
          />
          <button
            type="button"
            className="btn secondary text-xs"
            disabled={actionBusy || !session || !typeInput}
            onClick={() => void handleTypeText()}
          >
            {t("team_computer.type")}
          </button>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            className="rounded border border-line bg-panel px-2 py-1 text-[11px] font-mono text-muted hover:text-ink"
            disabled={actionBusy || !session}
            onClick={() => void handleKeyPress("Enter")}
          >
            Enter
          </button>
          <button
            type="button"
            className="rounded border border-line bg-panel px-2 py-1 text-[11px] font-mono text-muted hover:text-ink"
            disabled={actionBusy || !session}
            onClick={() => void handleKeyPress("Tab")}
          >
            Tab
          </button>
          <button
            type="button"
            className="rounded border border-line bg-panel px-2 py-1 text-[11px] font-mono text-muted hover:text-ink"
            disabled={actionBusy || !session}
            onClick={() => void handleKeyPress("Escape")}
          >
            Esc
          </button>
          <button
            type="button"
            className="rounded border border-line bg-panel px-2 py-1 text-[11px] text-muted hover:text-ink"
            disabled={actionBusy || !session}
            onClick={() => void handleScroll("up")}
            title={t("team_computer.scroll_up")}
          >
            ↑
          </button>
          <button
            type="button"
            className="rounded border border-line bg-panel px-2 py-1 text-[11px] text-muted hover:text-ink"
            disabled={actionBusy || !session}
            onClick={() => void handleScroll("down")}
            title={t("team_computer.scroll_down")}
          >
            ↓
          </button>
        </div>
      </div>

      {/* Main Viewport */}
      <div
        ref={containerRef}
        className="relative flex flex-1 items-center justify-center overflow-auto bg-black/90 p-4"
      >
        {loading ? (
          <div className="text-center text-sm text-faint">
            <Icon name="refresh" size={24} className="mx-auto mb-2 animate-spin text-accent" />
            <p>{t("team_computer.initializing_session")}</p>
          </div>
        ) : screenshotB64 ? (
          <div className="relative inline-block overflow-hidden rounded-lg shadow-2xl">
            <img
              ref={imageRef}
              src={`data:image/png;base64,${screenshotB64}`}
              alt={observation?.title || "Computer Screen Viewport"}
              className="max-h-[calc(100vh-220px)] max-w-full cursor-crosshair object-contain select-none"
              onClick={(e) => void handleCanvasClick(e)}
            />
            {actionBusy && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/30 backdrop-blur-[1px]">
                <Icon name="refresh" size={24} className="animate-spin text-white" />
              </div>
            )}
          </div>
        ) : (
          <div className="text-center text-sm text-muted">
            <Icon name="server" size={32} className="mx-auto mb-2 text-faint" />
            <p>{t("team_computer.no_viewport_data")}</p>
            {session && (
              <button
                type="button"
                className="btn primary mt-3 text-xs"
                onClick={() => void refreshScreen(session.session_id)}
              >
                {t("team_computer.capture_screen")}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Status Bar */}
      <footer className="flex items-center justify-between border-t border-line bg-panel px-4 py-1.5 text-[11px] text-muted">
        <div className="flex items-center gap-3">
          <span>{observation?.title || session?.session_id || t("team_computer.ready")}</span>
          {lastActionStatus && <span className="text-accent">({lastActionStatus})</span>}
        </div>
        <div>
          {session?.viewport ? `${session.viewport.width} × ${session.viewport.height} px` : "1280 × 800 px"}
        </div>
      </footer>
    </div>
  );
}
