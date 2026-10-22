/** @jsxImportSource react */
import { MarbleAvatar } from "./MarbleAvatar";
import { Icon } from "../Icon";

export type ExtensionLayout = "grid" | "list";

export type ExtensionCardProps = {
  name: string;
  description: string;
  iconSrc?: string;
  taxonomy?: "skill" | "command" | "agent" | "mcp" | "plugin" | "connection" | "app";
  layout?: ExtensionLayout;
  connected?: boolean;
  connectedLabel?: string;
  connecting?: boolean;
  disabled?: boolean;
  hidden?: boolean;
  meta?: string | null;
  actionLabel?: string;
  onClick?: () => void;
};

const taxonomyStyle: Record<string, string> = {
  app: "bg-teal-50 text-teal-700 dark:bg-teal-950/40 dark:text-teal-300",
  connection: "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
  mcp: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  skill: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  command: "bg-orange-50 text-orange-700 dark:bg-orange-950/40 dark:text-orange-300",
  agent: "bg-pink-50 text-pink-700 dark:bg-pink-950/40 dark:text-pink-300",
  plugin: "bg-purple-50 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300",
};

export function ExtensionCard(props: ExtensionCardProps) {
  const {
    name,
    description,
    iconSrc,
    taxonomy = "skill",
    layout = "grid",
    connected = false,
    connectedLabel = "Connected",
    connecting = false,
    disabled = false,
    hidden = false,
    meta = null,
    actionLabel = "View details",
    onClick,
  } = props;

  const shellState = connected
    ? "border-emerald-500/30 bg-emerald-50/40 dark:border-emerald-500/20 dark:bg-emerald-950/10"
    : "border-dls-border bg-dls-surface hover:bg-dls-hover";

  const iconElement = (
    <div className="relative shrink-0">
      <div
        className={`flex ${layout === "list" ? "size-8" : "size-10"} items-center justify-center rounded-lg border ${
          connected ? "border-emerald-500/30 bg-emerald-50/70 dark:bg-emerald-950/20" : "border-dls-border bg-dls-surface"
        }`}
      >
        {iconSrc ? (
          <div className={`flex ${layout === "list" ? "size-5" : "size-6"} items-center justify-center rounded-md bg-white p-0.5`}>
            <img src={iconSrc} alt="" className="size-full object-contain" loading="lazy" />
          </div>
        ) : (
          <MarbleAvatar
            seed={`${taxonomy}:${name}`}
            className={`${layout === "list" ? "size-6" : "size-7"} rounded-md shadow-inner`}
          />
        )}
      </div>
      {connected && layout !== "list" && (
        <div className="absolute -bottom-0.5 -right-0.5 flex size-4 items-center justify-center rounded-full border-2 border-white bg-emerald-600 dark:border-zinc-900">
          <Icon name="check" size={10} className="text-white" />
        </div>
      )}
    </div>
  );

  if (layout === "list") {
    return (
      <button
        type="button"
        disabled={disabled || connecting}
        onClick={onClick}
        className={`group flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-dls-hover ${
          hidden ? "opacity-60" : ""
        }`}
      >
        {iconElement}
        <div className="flex w-48 shrink-0 items-center gap-1.5">
          <h4 className="min-w-0 truncate text-[13px] font-medium text-dls-text">{name}</h4>
          {connected && <span className="size-1.5 shrink-0 rounded-full bg-emerald-500" />}
        </div>
        <div className="flex w-24 shrink-0 items-center">
          <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${taxonomyStyle[taxonomy] ?? taxonomyStyle.skill}`}>
            {taxonomy}
          </span>
        </div>
        <p className="min-w-0 flex-1 truncate text-xs text-dls-secondary">{description}</p>
        {meta && <div className="hidden shrink-0 text-[11px] text-dls-secondary md:block">{meta}</div>}
        <Icon name="chevronRight" size={14} className="shrink-0 text-dls-secondary group-hover:translate-x-0.5 transition-transform" />
      </button>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled || connecting}
      onClick={onClick}
      className={`group w-full rounded-xl border p-4 text-left transition-all ${shellState} ${
        hidden ? "border-dashed opacity-70" : ""
      }`}
    >
      <div className="flex items-start gap-3">
        {iconElement}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <h4 className="min-w-0 break-words text-sm font-semibold text-dls-text">{name}</h4>
            {connected ? (
              <span className="shrink-0 rounded-md bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                {connectedLabel}
              </span>
            ) : (
              <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${taxonomyStyle[taxonomy] ?? taxonomyStyle.skill}`}>
                {taxonomy}
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-dls-secondary leading-relaxed">{description}</p>
          {meta && <div className="mt-2 text-[11px] text-dls-secondary">{meta}</div>}
          {actionLabel && (
            <div className="mt-3 text-[11px] font-medium text-dls-text underline-offset-2 transition-colors group-hover:underline">
              {actionLabel}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}
