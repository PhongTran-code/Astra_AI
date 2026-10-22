import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { type CloudStatus, type Connector, type McpServer, type SlackStatus } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import { AddMcpModal, CustomMcpGroup } from "./CustomMcp";
import { Icon } from "../Icon";

// Codex-style Plugin List View
export function CodexPluginList({
  connectors,
  mcpServers,
  cloud,
  slack: _slack,
  onOpen,
  onChanged,
}: {
  connectors: Connector[];
  mcpServers: McpServer[];
  cloud: CloudStatus | null;
  slack: SlackStatus | null;
  onOpen: (name: string) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState<"public" | "personal">("public");
  const [connecting, setConnecting] = useState<string | null>(null);
  const [addingMcp, setAddingMcp] = useState(false);

  const q = filter.trim().toLowerCase();
  const match = (c: Connector) => !q || c.title.toLowerCase().includes(q) || c.name.includes(q);

  const connected = useMemo(() => connectors.filter((c) => c.connected && match(c)), [connectors, q]);
  const available = useMemo(() => connectors.filter((c) => !c.connected && c.available && match(c)), [connectors, q]);
  const customMcp = useMemo(() => mcpServers.filter((s) => !q || s.name.toLowerCase().includes(q)), [mcpServers, q]);

  // Featured connectors (e.g. top tools)
  const featured = useMemo(() => {
    return available.slice(0, 8);
  }, [available]);

  const connectingC = connecting ? connectors.find((c) => c.name === connecting) : null;

  return (
    <div className="space-y-6">
      {/* Search Input Bar (Codex Style) */}
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-muted">
          <Icon name="search" size={15} />
        </div>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t("connector.search_placeholder", "Tìm kiếm plugin")}
          className="w-full pl-10 pr-4 py-2.5 rounded-full border border-line bg-paper/60 text-[13px] text-ink placeholder:text-muted outline-none focus:border-accent focus:bg-paper transition-all"
        />
      </div>

      {/* Installed Icon Strip (Đã cài đặt) */}
      <div>
        <div className="mb-3">
          <span className="text-[13px] font-semibold text-ink">
            {t("connector.installed", "Đã cài đặt")}
          </span>
        </div>

        {connected.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3">
            {connected.map((c) => (
              <button
                key={c.name}
                data-testid={`connector-${c.name}`}
                onClick={() => onOpen(c.name)}
                className="group relative flex items-center justify-center p-2 rounded-xl border border-line bg-panel hover:bg-paper hover:border-lineStrong hover:scale-105 transition-all shadow-xs"
                title={`${c.title} (${c.connected ? "Connected" : ""})`}
              >
                <ConnectorBadge connector={c} size={30} title={c.title} />
              </button>
            ))}
          </div>
        ) : (
          <div className="text-[13px] text-muted py-2">
            {t("connector.no_installed", "Chưa có plugin nào được cài đặt.")}
          </div>
        )}
      </div>

      {/* Filter Tabs: Công khai | Cá nhân (MCP) */}
      <div className="flex items-center justify-between border-b border-line pb-2">
        <div className="flex items-center gap-4 text-[13px]">
          <button
            type="button"
            className={`font-medium transition-colors ${
              activeFilter === "public" ? "text-ink border-b-2 border-ink pb-2 -mb-2" : "text-muted hover:text-ink"
            }`}
            onClick={() => setActiveFilter("public")}
          >
            {t("connector.filter_public", "Công khai")}
          </button>
          <button
            type="button"
            className={`font-medium transition-colors ${
              activeFilter === "personal" ? "text-ink border-b-2 border-ink pb-2 -mb-2" : "text-muted hover:text-ink"
            }`}
            onClick={() => setActiveFilter("personal")}
          >
            {t("connector.filter_personal", "Cá nhân (MCP)")}
          </button>
        </div>

        {activeFilter === "personal" && (
          <button
            type="button"
            onClick={() => setAddingMcp(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-accent text-white text-[12px] font-medium hover:opacity-90 transition-opacity"
          >
            <Icon name="plus" size={13} />
            <span>{t("connector.add_custom_mcp", "Thêm máy chủ MCP")}</span>
          </button>
        )}
      </div>
      {/* Featured / Available Grid (Codex 2-column cards) */}
      {activeFilter === "public" ? (
        <div>
          <div className="text-[14px] font-semibold text-ink mb-3">Featured</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {featured.map((c) => (
              <div
                key={c.name}
                data-testid={`connector-${c.name}`}
                onClick={() => onOpen(c.name)}
                className="flex items-center justify-between p-3.5 rounded-xl border border-line bg-panel hover:border-lineStrong hover:bg-paper transition-all cursor-pointer group"
              >
                <div className="flex items-center gap-3 min-w-0 mr-3">
                  <ConnectorBadge connector={c} size={32} title={c.title} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-ink truncate group-hover:text-accent transition-colors">
                      {c.title}
                    </div>
                    <div className="text-[12px] text-muted truncate">
                      {c.blurb || t("connector.no_description", "Mở rộng năng lực kết nối")}
                    </div>
                  </div>
                </div>

                <button
                  className="px-3 py-1.5 rounded-full border border-line bg-paper text-[12px] font-medium text-ink hover:bg-chromeHover shrink-0 transition-colors"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConnecting(c.name);
                  }}
                >
                  {t("connector.install", "Cài đặt")}
                </button>
              </div>
            ))}
          </div>

          {available.length > featured.length && (
            <div className="pt-2">
              <div className="text-[13px] font-medium text-muted mb-3">
                {t("connector.other_plugins", "Plugin khác")}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {available.slice(featured.length).map((c) => (
                  <div
                    key={c.name}
                    data-testid={`connector-${c.name}`}
                    onClick={() => onOpen(c.name)}
                    className="flex items-center justify-between p-3.5 rounded-xl border border-line bg-panel hover:border-lineStrong hover:bg-paper transition-all cursor-pointer group"
                  >
                    <div className="flex items-center gap-3 min-w-0 mr-3">
                      <ConnectorBadge connector={c} size={32} title={c.title} />
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium text-ink truncate group-hover:text-accent transition-colors">
                          {c.title}
                        </div>
                        <div className="text-[12px] text-muted truncate">{c.blurb}</div>
                      </div>
                    </div>

                    <button
                      className="px-3 py-1.5 rounded-full border border-line bg-paper text-[12px] font-medium text-ink hover:bg-chromeHover shrink-0 transition-colors"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConnecting(c.name);
                      }}
                    >
                      {t("connector.install", "Cài đặt")}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        /* Personal / Custom MCP View */
        <div className="pt-2">
          <CustomMcpGroup
            servers={customMcp}
            onOpen={(name) => onOpen("mcp:" + name)}
            onChanged={onChanged}
          />
        </div>
      )}

      {connectingC && (
        <AddConnectionModal
          c={connectingC}
          cloud={cloud}
          onClose={() => setConnecting(null)}
          onChanged={() => {
            setConnecting(null);
            onChanged();
          }}
        />
      )}

      {addingMcp && (
        <AddMcpModal
          onClose={() => setAddingMcp(false)}
          onChanged={() => {
            setAddingMcp(false);
            onChanged();
          }}
        />
      )}
    </div>
  );
}
