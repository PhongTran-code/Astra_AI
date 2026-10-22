import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAudit, type AuditEvent } from "../api";
import { Icon, type IconName } from "./Icon";

type FilterTab = "all" | "inquiry" | "tools" | "system";

export function AuditView() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<FilterTab>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const { t } = useTranslation();

  const refresh = () => {
    setLoading(true);
    getAudit({ limit: 150 })
      .then(setEvents)
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const filteredEvents = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return events.filter((ev) => {
      // Tab filter
      if (activeTab === "inquiry" && ev.tool !== "ask_user") return false;
      if (activeTab === "tools" && (!ev.tool || ev.tool === "ask_user")) return false;
      if (activeTab === "system" && Boolean(ev.tool)) return false;

      // Text search
      if (!q) return true;
      const haystack = [
        ev.tool,
        ev.connector,
        ev.stage,
        ev.status,
        ev.session_id,
        ev.resource,
        ev.reason,
        ev.result_preview,
        ev.args ? JSON.stringify(ev.args) : "",
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [events, activeTab, searchQuery]);

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <div className="flex-1 min-w-0 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-7 py-6">
          {/* Header */}
          <div className="flex items-start justify-between gap-4 mb-6">
            <div>
              <h1 className="text-[24px] font-semibold tracking-tight text-ink">
                {t("audit.title", "Activity")}
              </h1>
              <p className="text-[13px] text-muted mt-1 leading-relaxed">
                {t(
                  "audit.sub",
                  "Audit trail of assistant actions, tool invocations, and user interactions."
                )}
              </p>
            </div>
            <button
              onClick={refresh}
              disabled={loading}
              className="px-3 py-1.5 rounded-lg border border-line bg-panel hover:bg-chromeHover text-[13px] font-medium text-ink flex items-center gap-1.5 transition-colors disabled:opacity-50 shrink-0 cursor-pointer"
              title="Refresh activity feed"
            >
              <Icon
                name="refresh"
                size={14}
                className={loading ? "animate-spin text-muted" : "text-muted"}
              />
              <span>{loading ? "Refreshing..." : "Refresh"}</span>
            </button>
          </div>

          {/* Controls: Search + Filter Tabs */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
            {/* Filter Tabs */}
            <div className="flex items-center gap-1 p-1 bg-panel border border-line/80 rounded-xl w-fit">
              <button
                type="button"
                onClick={() => setActiveTab("all")}
                className={`px-3 py-1 rounded-lg text-[12px] font-medium transition-colors cursor-pointer ${
                  activeTab === "all"
                    ? "bg-chromeHover text-ink font-semibold shadow-2xs"
                    : "text-muted hover:text-ink hover:bg-chromeHover/40"
                }`}
              >
                All ({events.length})
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("inquiry")}
                className={`px-3 py-1 rounded-lg text-[12px] font-medium transition-colors cursor-pointer ${
                  activeTab === "inquiry"
                    ? "bg-chromeHover text-ink font-semibold shadow-2xs"
                    : "text-muted hover:text-ink hover:bg-chromeHover/40"
                }`}
              >
                Inquiries
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("tools")}
                className={`px-3 py-1 rounded-lg text-[12px] font-medium transition-colors cursor-pointer ${
                  activeTab === "tools"
                    ? "bg-chromeHover text-ink font-semibold shadow-2xs"
                    : "text-muted hover:text-ink hover:bg-chromeHover/40"
                }`}
              >
                Tools
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("system")}
                className={`px-3 py-1 rounded-lg text-[12px] font-medium transition-colors cursor-pointer ${
                  activeTab === "system"
                    ? "bg-chromeHover text-ink font-semibold shadow-2xs"
                    : "text-muted hover:text-ink hover:bg-chromeHover/40"
                }`}
              >
                System
              </button>
            </div>

            {/* Search Input */}
            <div className="relative flex-1 sm:max-w-xs">
              <div className="absolute inset-y-0 left-2.5 flex items-center pointer-events-none text-muted">
                <Icon name="search" size={14} />
              </div>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter by session, tool, keyword..."
                className="w-full pl-8 pr-8 py-1.5 rounded-lg border border-line bg-panel text-[13px] text-ink placeholder:text-muted/60 outline-none focus:border-accent transition-colors"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="absolute inset-y-0 right-2.5 flex items-center text-muted hover:text-ink text-[12px] cursor-pointer"
                >
                  ✕
                </button>
              )}
            </div>
          </div>

          {/* Feed List */}
          {filteredEvents.length === 0 ? (
            <div className="rounded-xl border border-line bg-panel p-8 text-center">
              <div className="inline-flex size-10 rounded-full bg-chromeHover items-center justify-center text-muted mb-2">
                <Icon name="search" size={18} />
              </div>
              <p className="text-[13px] font-medium text-ink">No activity records found</p>
              <p className="text-[12px] text-muted mt-0.5">
                {searchQuery
                  ? "Try adjusting your search terms or filter selection."
                  : "Activity events will appear here as the assistant executes tools."}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {filteredEvents.map((ev) => (
                <AuditCard ev={ev} key={ev.id} />
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

function AuditCard({ ev }: { ev: AuditEvent }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const isInquiry = ev.tool === "ask_user";
  const questionText = isInquiry ? ev.args?.question || "" : "";
  const rawOptions = isInquiry && Array.isArray(ev.args?.options) ? ev.args.options : [];

  // Parse user answer if available
  let userAnswer = "";
  if (ev.result_preview) {
    try {
      const parsed = JSON.parse(ev.result_preview);
      if (parsed && typeof parsed === "object" && parsed.answer) {
        userAnswer = String(parsed.answer);
      } else {
        userAnswer = String(ev.result_preview);
      }
    } catch {
      userAnswer = String(ev.result_preview);
    }
  }

  // Determine icon & human-readable tool name
  const { icon, label } = getToolMeta(ev);

  const copyJson = () => {
    navigator.clipboard.writeText(JSON.stringify(ev, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="rounded-xl border border-line bg-panel p-4 transition-all hover:border-line/90">
      {/* Top Meta Row */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="size-7 rounded-lg bg-chromeHover border border-line/50 grid place-items-center text-muted shrink-0">
            <Icon name={icon} size={15} />
          </div>
          <div className="flex items-baseline gap-2 min-w-0">
            <span className="text-[13px] font-semibold text-ink truncate">{label}</span>
            {ev.connector && (
              <span className="text-[11px] font-medium text-muted bg-paper border border-line/60 px-1.5 py-0.5 rounded">
                {ev.connector}
              </span>
            )}
            {ev.stage && (
              <span className="text-[11px] text-faint">
                · {formatStage(ev.stage)}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2.5 shrink-0 ml-auto">
          <StatusBadge status={ev.status} stage={ev.stage} />
          <span className="text-[11px] font-mono text-faint">{ev.timestamp}</span>
        </div>
      </div>

      {/* Main Content Area */}
      {isInquiry ? (
        <div className="mt-2.5 pl-9.5">
          {questionText && (
            <p className="text-[14px] font-medium text-ink leading-relaxed">
              {questionText}
            </p>
          )}

          {/* Options Pills */}
          {rawOptions.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {rawOptions.map((opt: any, idx: number) => {
                const optLabel = typeof opt === "string" ? opt : opt.label || "";
                const isSelected =
                  Boolean(userAnswer) &&
                  (userAnswer === optLabel ||
                    userAnswer.includes(optLabel) ||
                    optLabel.includes(userAnswer));

                return (
                  <span
                    key={idx}
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px] transition-colors ${
                      isSelected
                        ? "bg-accent/10 border border-accent/30 text-ink font-medium shadow-2xs"
                        : "bg-paper border border-line/60 text-muted"
                    }`}
                  >
                    {isSelected && <Icon name="check" size={13} className="text-accent" />}
                    <span>{optLabel}</span>
                  </span>
                );
              })}
            </div>
          )}

          {/* Non-option answer or custom response */}
          {userAnswer &&
            !rawOptions.some((opt: any) => {
              const optLabel = typeof opt === "string" ? opt : opt.label || "";
              return userAnswer === optLabel || userAnswer.includes(optLabel);
            }) && (
              <div className="mt-2 text-[12px] bg-paper border border-line/60 rounded-lg p-2 text-ink">
                <span className="text-muted font-medium mr-1.5">User Answer:</span>
                <span>{userAnswer}</span>
              </div>
            )}
        </div>
      ) : (
        /* Non-inquiry tool summary */
        <div className="mt-2 pl-9.5">
          {ev.resource && (
            <p className="text-[12px] font-mono text-muted mb-1 truncate">
              Resource: {ev.resource}
            </p>
          )}
          {ev.reason && (
            <p className="text-[13px] text-ink leading-relaxed mb-1">{ev.reason}</p>
          )}
          {renderToolSummary(ev)}
        </div>
      )}

      {/* Technical Details Accordion */}
      <div className="mt-3 pt-2 border-t border-line/50">
        <button
          type="button"
          onClick={() => setDetailsOpen(!detailsOpen)}
          className="text-[11px] text-faint hover:text-muted flex items-center gap-1 transition-colors w-full cursor-pointer"
        >
          <Icon name={detailsOpen ? "chevronDown" : "chevronRight"} size={11} />
          <span>{detailsOpen ? "Hide technical details" : "Technical details"}</span>
          <span className="ml-auto font-mono text-[10px] text-faint">
            session: {ev.session_id || "-"}
          </span>
        </button>

        {detailsOpen && (
          <div className="mt-2 p-2.5 rounded-lg bg-paper border border-line/60 text-[11px] font-mono space-y-1.5 text-muted overflow-x-auto hairline-scroll">
            <div className="flex items-center justify-between border-b border-line/40 pb-1 mb-1.5">
              <span>Event ID: {ev.id}</span>
              <button
                type="button"
                onClick={copyJson}
                className="text-[10px] text-muted hover:text-ink flex items-center gap-1 font-sans cursor-pointer"
              >
                <Icon name="copy" size={10} />
                <span>{copied ? "Copied!" : "Copy JSON"}</span>
              </button>
            </div>
            {ev.workspace && <div>workspace: {ev.workspace}</div>}
            {ev.agent && <div>agent: {ev.agent}</div>}
            {ev.tool && <div>tool: {ev.tool}</div>}
            {ev.args && Object.keys(ev.args).length > 0 && (
              <div>
                <div className="text-ink font-semibold mb-0.5 font-sans text-[11px]">Arguments:</div>
                <pre className="p-2 rounded bg-chrome/50 overflow-x-auto whitespace-pre-wrap break-all text-[11px]">
                  {JSON.stringify(ev.args, null, 2)}
                </pre>
              </div>
            )}
            {ev.result_preview && (
              <div>
                <div className="text-ink font-semibold mb-0.5 font-sans text-[11px]">Result Preview:</div>
                <pre className="p-2 rounded bg-chrome/50 overflow-x-auto whitespace-pre-wrap break-all text-[11px]">
                  {ev.result_preview}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function getToolMeta(ev: AuditEvent): { icon: IconName; label: string } {
  if (ev.tool === "ask_user") {
    return { icon: "chat", label: "User Inquiry" };
  }
  if (ev.tool === "todo_write") {
    return { icon: "listChecks", label: "Task List" };
  }
  if (ev.tool === "web_search") {
    return { icon: "search", label: "Web Search" };
  }
  if (ev.tool?.includes("code") || ev.tool?.includes("file")) {
    return { icon: "code", label: humanizeTool(ev.tool) };
  }
  if (ev.stage?.includes("mode")) {
    return { icon: "sliders", label: "Agent Mode" };
  }
  if (ev.stage?.includes("unattended")) {
    return { icon: "clock", label: "Supervision Policy" };
  }
  if (ev.connector) {
    return { icon: "plug", label: ev.connector };
  }
  if (ev.tool) {
    return { icon: "wrench", label: humanizeTool(ev.tool) };
  }
  return { icon: "activity", label: "System Event" };
}

function humanizeTool(name: string): string {
  return name
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function StatusBadge({ status, stage }: { status?: string; stage?: string }) {
  if (status === "ok" || status === "finished" || status === "success") {
    return (
      <span className="text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-800/40 text-[11px] font-medium px-2 py-0.5 rounded-full inline-flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-emerald-500 shrink-0" />
        <span>Completed</span>
      </span>
    );
  }

  if (status === "denied" || status === "failed" || status === "error") {
    return (
      <span className="text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950/40 border border-rose-200/60 dark:border-rose-800/40 text-[11px] font-medium px-2 py-0.5 rounded-full inline-flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-rose-500 shrink-0" />
        <span>Denied</span>
      </span>
    );
  }

  if (stage === "question_requested") {
    return (
      <span className="text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border border-amber-200/60 dark:border-amber-800/40 text-[11px] font-medium px-2 py-0.5 rounded-full inline-flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-amber-500 shrink-0" />
        <span>Awaiting Input</span>
      </span>
    );
  }

  if (stage === "proposed") {
    return (
      <span className="text-sky-700 dark:text-sky-300 bg-sky-50 dark:bg-sky-950/40 border border-sky-200/60 dark:border-sky-800/40 text-[11px] font-medium px-2 py-0.5 rounded-full inline-flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-sky-500 shrink-0" />
        <span>Proposed</span>
      </span>
    );
  }

  const label = status || stage;
  if (!label) return null;

  return (
    <span className="text-muted bg-paper border border-line/60 text-[11px] font-medium px-2 py-0.5 rounded-full">
      {label}
    </span>
  );
}

function formatStage(stage: string): string {
  switch (stage) {
    case "finished":
      return "finished";
    case "question_requested":
      return "question requested";
    case "proposed":
      return "proposed";
    case "started":
      return "started";
    case "mode_changed":
      return "mode changed";
    case "unattended_changed":
      return "supervision changed";
    default:
      return stage.replace(/_/g, " ");
  }
}

function renderToolSummary(ev: AuditEvent) {
  if (!ev.args || Object.keys(ev.args).length === 0) {
    return null;
  }

  if (ev.args.query) {
    return (
      <div className="text-[12px] text-muted mt-1 font-mono bg-paper border border-line/50 rounded px-2.5 py-1 inline-block">
        query: <span className="text-ink font-medium">{String(ev.args.query)}</span>
      </div>
    );
  }

  if (ev.args.path) {
    return (
      <div className="text-[12px] text-muted mt-1 font-mono bg-paper border border-line/50 rounded px-2.5 py-1 inline-block truncate max-w-full">
        path: <span className="text-ink font-medium">{String(ev.args.path)}</span>
      </div>
    );
  }

  if (ev.args.command) {
    return (
      <div className="text-[12px] text-muted mt-1 font-mono bg-paper border border-line/50 rounded px-2.5 py-1 block overflow-x-auto">
        <span className="text-ink font-medium">$ {String(ev.args.command)}</span>
      </div>
    );
  }

  if (ev.args.todos && Array.isArray(ev.args.todos)) {
    return (
      <div className="mt-1.5 space-y-1">
        {ev.args.todos.slice(0, 3).map((item: any, idx: number) => (
          <div key={idx} className="text-[12px] text-muted flex items-center gap-1.5">
            <span className="text-faint">•</span>
            <span>{typeof item === "string" ? item : item.text || item.title || JSON.stringify(item)}</span>
          </div>
        ))}
        {ev.args.todos.length > 3 && (
          <span className="text-[11px] text-faint">+{ev.args.todos.length - 3} more items</span>
        )}
      </div>
    );
  }

  return null;
}
