import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getConnectors,
  getDmRoute,
  getInboxRouting,
  getRecentChannels,
  getSessions,
  getSubscriptions,
  getUnrouted,
  setDmRoute,
  setInboxBinding,
  subscribeChannel,
  unsubscribeChannel,
  type RecentChannel,
  type Connector,
  type Subscription,
  type UnroutedItem,
} from "../api";
import type { SessionInfo } from "../types";
import { ChannelPicker } from "./SubscriptionsChip";
import { Icon } from "./Icon";

const CARD = "rounded-xl border border-line bg-panel";
const SELECT = "px-3 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT_SM = "text-[12px] px-3 py-1.5 rounded-lg bg-accent text-white font-medium disabled:opacity-50 transition-colors cursor-pointer";

export function InboxConfigure() {
  const { t } = useTranslation();
  const dmCardRef = useRef<HTMLDivElement | null>(null);

  const focusDmCard = () => {
    if (dmCardRef.current) {
      dmCardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
      const select = dmCardRef.current.querySelector("select");
      if (select) {
        select.focus();
        dmCardRef.current.classList.add("ring-2", "ring-accent/40");
        setTimeout(() => {
          dmCardRef.current?.classList.remove("ring-2", "ring-accent/40");
        }, 1800);
      }
    }
  };

  return (
    <div data-testid="inbox-configure" className="space-y-4">
      {/* Top 2 Configuration Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <InboxRoutingCard />
        <DmRouteCard ref={dmCardRef} />
      </div>

      {/* Channel Subscriptions */}
      <SubscriptionsCard />

      {/* Unassigned & Failed Messages */}
      <div className="mt-6" data-testid="unrouted-section">
        <div className="mb-3">
          <h3 className="text-[15px] font-semibold text-ink">
            {t("inbox.unrouted_title", "Unassigned & Failed Messages")}
          </h3>
          <p className="text-[13px] text-muted mt-0.5">
            {t(
              "inbox.unrouted_sub",
              "Messages from external apps (Telegram, Slack) that could not be delivered to a conversation."
            )}
          </p>
        </div>
        <UnroutedTable onAssignDm={focusDmCard} />
      </div>
    </div>
  );
}

// Where approval buttons get mirrored (e.g. to a Slack channel).
function InboxRoutingCard() {
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [target, setTarget] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { t: tt } = useTranslation();

  const load = () => {
    getRecentChannels().then(setRecent).catch(() => setRecent([]));
    getConnectors().then(setConnectors).catch(() => setConnectors([]));
    getInboxRouting()
      .then((bs) => {
        const def = bs.find((b) => b.name === "default");
        setTarget(def?.channel ? `${def.channel}:${def.target}` : "");
      })
      .catch(() => setTarget(""));
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const save = async () => {
    const addr = draft.trim();
    if (!addr) return;
    const [platform, id] = addr.includes(":") ? addr.split(":", 2) : ["slack", addr];
    const result = await setInboxBinding("default", platform, id);
    if (!result.ok) {
      setError(result.error || tt("inbox.routing_update_failed", "Failed to update channel"));
      return;
    }
    setError(null);
    setDraft("");
    load();
  };

  const clear = async () => {
    const result = await setInboxBinding("default", null, "");
    if (!result.ok) {
      setError(result.error || tt("inbox.routing_clear_failed", "Failed to disconnect channel"));
      return;
    }
    setError(null);
    load();
  };

  const draftAddr = draft.trim();
  const [draftPlatform, draftTarget] = draftAddr.includes(":")
    ? draftAddr.split(":", 2)
    : ["slack", draftAddr];
  const slack = connectors.find((c) => c.name === "slack");
  const teamId =
    draftPlatform === "slack" && draftTarget.includes("/")
      ? draftTarget.split("/", 1)[0]
      : null;
  const owners =
    draftPlatform !== "slack"
      ? []
      : teamId
        ? slack?.workspaces?.find((w) => w.team_id === teamId)?.approval_owner_ids ?? []
        : slack?.approval_owner_ids ?? [];
  const missingSlackOwner =
    draftPlatform === "slack" && draftTarget.length > 0 && owners.length === 0;

  const known = recent.find((c) => c.channel === target)?.name;

  return (
    <div className={CARD + " p-4 flex flex-col justify-between"} data-testid="inbox-mirror-card">
      <div>
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <div className="flex items-center gap-2">
            <div className="size-7 rounded-lg bg-chromeHover border border-line/60 grid place-items-center text-muted shrink-0">
              <Icon name="shield" size={15} />
            </div>
            <span className="font-semibold text-[13px] text-ink">
              {tt("inbox.unattended_approvals", "Remote Approvals")}
            </span>
          </div>

          {target ? (
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-800/40">
              <span className="size-1.5 rounded-full bg-emerald-500 shrink-0" />
              <span>{known ? `#${known}` : target}</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium text-muted bg-paper border border-line/60">
              <span className="size-1.5 rounded-full bg-muted/60 shrink-0" />
              <span>{tt("inbox.in_app_inbox_only", "In-app Inbox only")}</span>
            </span>
          )}
        </div>

        <p className="text-[12px] text-muted mb-3 leading-relaxed">
          {tt(
            "inbox.mirror_desc_prefix",
            "Send approval requests to Slack so you can review tasks from anywhere."
          )}
        </p>
      </div>

      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex-1 min-w-[180px]">
            <ChannelPicker value={draft} onChange={setDraft} recent={recent} onSubmit={save} />
          </div>
          <button
            className={BTN_ACCENT_SM}
            disabled={!draft.trim() || missingSlackOwner}
            onClick={save}
          >
            {tt("inbox.set", "Connect")}
          </button>
          {target && (
            <button
              className="text-[12px] text-muted hover:text-danger px-2 py-1 transition-colors cursor-pointer"
              onClick={clear}
              title="Disconnect channel"
            >
              {tt("inbox.clear", "Disconnect")}
            </button>
          )}
        </div>

        {missingSlackOwner && (
          <p className="text-[12px] text-warnInk mt-2">
            {tt("inbox.missing_slack_owner", "No approval owners configured in this Slack workspace.")}
          </p>
        )}
        {error && <p className="text-[12px] text-warnInk mt-2">{error}</p>}
      </div>
    </div>
  );
}

// Select which session handles private 1-on-1 messages sent to the bot.
import React from "react";
const DmRouteCard = React.forwardRef<HTMLDivElement, {}>(function DmRouteCard(_, ref) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [dm, setDm] = useState<string>("");
  const { t: tt } = useTranslation();

  const load = () => {
    getSessions().then(setSessions).catch(() => setSessions([]));
    getDmRoute().then((s) => setDm(s || "")).catch(() => setDm(""));
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const real = sessions.filter((s) => !s.session_id.startsWith("__"));
  const choose = async (sessionId: string) => {
    setDm(sessionId);
    await setDmRoute(sessionId);
    load();
  };

  return (
    <div
      ref={ref}
      id="dm-route-card"
      className={CARD + " p-4 flex flex-col justify-between transition-shadow duration-300"}
    >
      <div>
        <div className="flex items-center gap-2 mb-1.5">
          <div className="size-7 rounded-lg bg-chromeHover border border-line/60 grid place-items-center text-muted shrink-0">
            <Icon name="chat" size={15} />
          </div>
          <span className="font-semibold text-[13px] text-ink">
            {tt("inbox.direct_messages", "Incoming Direct Messages")}
          </span>
        </div>
        <p className="text-[12px] text-muted mb-3 leading-relaxed">
          {tt(
            "inbox.dm_desc",
            "Choose which conversation receives private 1-on-1 messages sent to your bot from Telegram or Slack."
          )}
        </p>
      </div>

      <div>
        <div className="flex items-center gap-2">
          <select
            className={"flex-1 " + SELECT}
            value={dm}
            onChange={(e) => choose(e.target.value)}
          >
            <option value="">
              {tt("inbox.dm_no_session", "No conversation (messages stay unassigned)")}
            </option>
            {real.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {s.title || s.session_id}
              </option>
            ))}
          </select>
        </div>
        <p className="text-[11px] text-muted mt-2">
          💡 Messages from external senders will be routed to this conversation.
        </p>
      </div>
    </div>
  );
});

// Channel subscriptions: which sessions listen to group channels.
function SubscriptionsCard() {
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [addSession, setAddSession] = useState("");
  const [addChannel, setAddChannel] = useState("");
  const { t: tt } = useTranslation();

  const load = () => {
    getSubscriptions().then(setSubs).catch(() => setSubs([]));
    getSessions().then(setSessions).catch(() => setSessions([]));
    getRecentChannels().then(setRecent).catch(() => setRecent([]));
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const real = sessions.filter((s) => !s.session_id.startsWith("__"));
  const add = async () => {
    if (!addSession || !addChannel.trim()) return;
    await subscribeChannel(addSession, addChannel.trim());
    setAddChannel("");
    load();
  };

  const remove = async (sessionId: string, channel: string) => {
    await unsubscribeChannel(sessionId, channel);
    load();
  };

  return (
    <div className={CARD + " overflow-hidden"}>
      <div className="px-4 py-3 border-b border-line flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="size-6 rounded-md bg-chromeHover grid place-items-center text-muted shrink-0">
            <Icon name="plug" size={14} />
          </div>
          <span className="font-semibold text-[13px] text-ink">
            {tt("inbox.channel_subscriptions", "Channel Subscriptions")}
          </span>
          <span className="text-[12px] text-muted hidden sm:inline">
            {tt(
              "inbox.subscriptions_sub",
              "— conversations that monitor and reply to group channels"
            )}
          </span>
        </div>
      </div>

      {subs && subs.length > 0 ? (
        <div className="overflow-x-auto hairline-scroll">
          <table className="w-full text-[13px]">
            <thead className="text-[11px] uppercase tracking-[0.04em] text-faint bg-paper/50">
              <tr className="text-left">
                <th className="font-medium px-4 py-2">{tt("inbox.col_session", "Conversation")}</th>
                <th className="font-medium px-4 py-2">{tt("inbox.col_listens_to", "Monitored Channel")}</th>
                <th className="font-medium px-4 py-2">{tt("inbox.col_inbox_routes_to", "Inbox Routes To")}</th>
                <th className="px-4 py-2 w-16 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {subs.map((s, i) => (
                <tr className="border-t border-line/60 hover:bg-chromeHover/20 transition-colors" key={i}>
                  <td className="px-4 py-2.5 truncate max-w-[14rem] font-medium text-ink" title={s.session_title}>
                    {s.session_title}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="inline-flex items-center gap-1.5" title={s.channel}>
                      <span className="text-muted shrink-0">
                        <Icon name="plug" size={13} />
                      </span>
                      <span className="text-ink">{s.channel_name ? `#${s.channel_name}` : s.channel}</span>
                      {s.channel_name && (
                        <span className="text-[11px] font-mono text-faint">({s.channel})</span>
                      )}
                    </span>
                    {s.collision && (
                      <span
                        className="ml-2 text-[10px] text-warnInk bg-warnSoft/70 border border-warnInk/20 rounded px-1.5 py-0.5"
                        title={tt("inbox.collision_title", "Channel conflict")}
                      >
                        {tt("inbox.collides", "⚠ collides")}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted">{s.routing_target || "—"}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      className="text-muted hover:text-danger text-[12px] px-2 py-1 rounded transition-colors cursor-pointer"
                      title={tt("inbox.unsubscribe", "Unsubscribe")}
                      onClick={() => remove(s.session_id, s.channel)}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-4 py-4 text-[13px] text-muted">
          {tt(
            "inbox.no_subscriptions",
            "No channel subscriptions yet — select a conversation and channel below to start monitoring."
          )}
        </div>
      )}

      {/* Add Subscription Row */}
      <div className="border-t border-line px-4 py-3 bg-paper/40 flex items-center gap-2 flex-wrap">
        <select
          className={SELECT}
          value={addSession}
          onChange={(e) => setAddSession(e.target.value)}
        >
          <option value="">{tt("inbox.choose_session", "Choose conversation…")}</option>
          {real.map((s) => (
            <option key={s.session_id} value={s.session_id}>
              {s.title || s.session_id}
            </option>
          ))}
        </select>
        <div className="min-w-[180px] flex-1">
          <ChannelPicker value={addChannel} onChange={setAddChannel} recent={recent} onSubmit={add} />
        </div>
        <button
          className={BTN_ACCENT_SM}
          disabled={!addSession || !addChannel.trim()}
          onClick={add}
        >
          {tt("inbox.subscribe", "+ Subscribe")}
        </button>
      </div>
    </div>
  );
}

// Table of unassigned inbound messages or failed delivery attempts.
function UnroutedTable({ onAssignDm }: { onAssignDm: () => void }) {
  const [items, setItems] = useState<UnroutedItem[] | null>(null);
  const { t: tt } = useTranslation();

  useEffect(() => {
    const load = () => getUnrouted().then(setItems).catch(() => setItems([]));
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  if (items && items.length === 0) {
    return (
      <div className={CARD + " p-6 text-center"}>
        <div className="inline-flex size-9 rounded-full bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 items-center justify-center mb-2">
          <Icon name="check" size={16} />
        </div>
        <p className="text-[13px] font-medium text-ink">
          {tt("inbox.unrouted_empty", "All messages delivered — no unassigned or dropped messages.")}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Helpful banner explaining how to resolve unassigned messages */}
      <div className="px-3.5 py-2.5 rounded-xl bg-amber-50/70 dark:bg-amber-950/30 border border-amber-200/60 dark:border-amber-800/40 text-[12px] text-amber-900 dark:text-amber-200 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-amber-600 dark:text-amber-400 font-bold">💡</span>
          <span>
            To automatically route future incoming messages, choose a conversation under{" "}
            <strong>Incoming Direct Messages</strong> above.
          </span>
        </div>
        <button
          onClick={onAssignDm}
          className="px-2.5 py-1 rounded-lg bg-amber-200/60 dark:bg-amber-800/50 hover:bg-amber-200 dark:hover:bg-amber-800 text-amber-950 dark:text-amber-100 font-medium text-[11px] transition-colors cursor-pointer shrink-0"
        >
          Configure Default Chat ↑
        </button>
      </div>

      <div className={CARD + " overflow-hidden"}>
        <div className="overflow-x-auto hairline-scroll">
          <table className="w-full text-[13px]">
            <thead className="text-[11px] uppercase tracking-[0.04em] text-faint bg-paper/50">
              <tr className="text-left">
                <th className="font-medium px-4 py-2 w-28 whitespace-nowrap">{tt("inbox.col_when", "Time")}</th>
                <th className="font-medium px-3 py-2 w-24 whitespace-nowrap">Platform</th>
                <th className="font-medium px-3 py-2 w-32 whitespace-nowrap">{tt("inbox.col_source", "Source / Sender")}</th>
                <th className="font-medium px-3 py-2 w-28 whitespace-nowrap">{tt("inbox.col_reason", "Status")}</th>
                <th className="font-medium px-3 py-2">{tt("inbox.col_message", "Message")}</th>
                <th className="px-4 py-2 w-24 whitespace-nowrap text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {(items ?? []).map((it, i) => {
                const { platform, cleanSource } = parseSource(it.source);
                const isNoDm = it.reason?.includes("no DM session designated");
                const d = new Date(it.ts * 1000);
                const timeStr = `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")} · ${d.getDate().toString().padStart(2, "0")}/${(d.getMonth() + 1).toString().padStart(2, "0")}`;

                return (
                  <tr className="border-t border-line/60 hover:bg-chromeHover/20 transition-colors" key={i}>
                    <td className="px-4 py-2.5 text-muted whitespace-nowrap text-[12px]">
                      {timeStr}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      <PlatformBadge platform={platform} />
                    </td>
                    <td className="px-3 py-2.5 font-mono text-[12px] text-ink truncate max-w-[8rem]" title={it.sender || it.source}>
                      {it.sender || cleanSource}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {isNoDm ? (
                        <span className="whitespace-nowrap inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border border-amber-200/60 dark:border-amber-800/40">
                          <span className="size-1.5 rounded-full bg-amber-500 shrink-0" />
                          <span>Needs Chat</span>
                        </span>
                      ) : (
                        <span className="whitespace-nowrap inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950/40 border border-rose-200/60 dark:border-rose-800/40">
                          <span className="size-1.5 rounded-full bg-rose-500 shrink-0" />
                          <span>{formatReason(it.reason)}</span>
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-ink truncate max-w-[22rem]" title={it.text}>
                      {it.text}
                    </td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      {isNoDm && (
                        <button
                          onClick={onAssignDm}
                          className="whitespace-nowrap px-2.5 py-1 rounded-md border border-accent/40 bg-accent/5 hover:bg-accent/15 text-accent font-medium text-[11px] transition-colors cursor-pointer inline-flex items-center gap-1 shrink-0"
                          title="Assign conversation for incoming messages"
                        >
                          <span>Assign</span>
                          <span>↑</span>
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function formatReason(reason?: string): string {
  if (!reason) return "Delivery error";
  if (reason.toLowerCase().includes("connection error")) return "Connection Error";
  if (reason.toLowerCase().includes("no dm session")) return "Needs Chat";
  return reason.replace(/\.$/, "");
}

function parseSource(source: string): { platform: "telegram" | "slack" | "system"; cleanSource: string } {
  if (source.startsWith("telegram:")) {
    return { platform: "telegram", cleanSource: source.replace("telegram:", "") };
  }
  if (source.startsWith("slack:")) {
    return { platform: "slack", cleanSource: source.replace("slack:", "") };
  }
  return { platform: "system", cleanSource: source };
}

function PlatformBadge({ platform }: { platform: "telegram" | "slack" | "system" }) {
  if (platform === "telegram") {
    return (
      <span className="whitespace-nowrap inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-300 border border-sky-200/60 dark:border-sky-800/40">
        <span>✈</span>
        <span>Telegram</span>
      </span>
    );
  }

  if (platform === "slack") {
    return (
      <span className="whitespace-nowrap inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-purple-50 dark:bg-purple-950/40 text-purple-700 dark:text-purple-300 border border-purple-200/60 dark:border-purple-800/40">
        <span>#</span>
        <span>Slack</span>
      </span>
    );
  }

  return (
    <span className="whitespace-nowrap inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-paper text-muted border border-line/60">
      <span>System</span>
    </span>
  );
}
