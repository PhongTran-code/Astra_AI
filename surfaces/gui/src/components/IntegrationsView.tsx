import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getConnectors } from "../api";
import { ConnectorsSection } from "./connectors/ConnectorsSection";
import { CodexSkillsList } from "./connectors/CodexSkillsList";

export function IntegrationsView({
  onCreateSkill,
  initialTab = "plugins",
}: {
  onCreateSkill?: (description: string) => void;
  initialTab?: "plugins" | "skills";
}) {
  const { t: tt } = useTranslation();
  const [activeTab, setActiveTab] = useState<"plugins" | "skills">(initialTab);

  useEffect(() => {
    void getConnectors().catch(() => {});
  }, []);

  return (
    <main className="flex-1 min-w-0 flex bg-paper [scrollbar-gutter:stable]">
      <div className="flex-1 min-w-0 overflow-y-scroll hairline-scroll [scrollbar-gutter:stable]">
        {/* Top Header with Tab Switcher: Plugin | Kỹ năng */}
        <div className="max-w-4xl w-full mx-auto px-7 pt-6 pb-2">
          <div className="flex items-center gap-2 mb-6">
            <button
              type="button"
              onClick={() => setActiveTab("plugins")}
              className={`px-3.5 py-1.5 rounded-lg text-[13px] font-medium transition-colors ${
                activeTab === "plugins"
                  ? "bg-chromeHover text-ink shadow-2xs font-semibold"
                  : "text-muted hover:text-ink hover:bg-chromeHover/50"
              }`}
            >
              {tt("integrations.tab_plugins", "Plugin")}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("skills")}
              className={`px-3.5 py-1.5 rounded-lg text-[13px] font-medium transition-colors ${
                activeTab === "skills"
                  ? "bg-chromeHover text-ink shadow-2xs font-semibold"
                  : "text-muted hover:text-ink hover:bg-chromeHover/50"
              }`}
            >
              {tt("integrations.tab_skills", "Kỹ năng")}
            </button>
          </div>

          {/* Title & Subtitle based on active tab with fixed min-height to prevent layout jump */}
          <div className="mb-6 min-h-[70px]">
            <h1 className="text-[26px] font-semibold tracking-tight text-ink leading-tight">
              {activeTab === "plugins"
                ? tt("integrations.plugin_title", "Plugin")
                : tt("integrations.skills_title", "Kỹ năng")}
            </h1>
            <p className="text-[13px] text-muted mt-1 leading-relaxed">
              {activeTab === "plugins"
                ? tt("integrations.plugin_sub", "Làm việc với Astra trên các công cụ bạn yêu thích")
                : tt("integrations.skills_sub", "Mở rộng Astra bằng các kỹ năng theo tác vụ")}
            </p>
          </div>

          {/* Tab Content - Keep both mounted and toggle visibility to eliminate mount lag and reflow */}
          <section className="pb-16 min-h-[500px]">
            <div className={activeTab === "plugins" ? "block" : "hidden"}>
              <ConnectorsSection />
            </div>
            <div className={activeTab === "skills" ? "block" : "hidden"}>
              <CodexSkillsList onCreateSkill={onCreateSkill} />
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

export function PanelHead({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-[20px] font-semibold tracking-tight">{title}</h2>
      <p className="text-[13px] text-muted mt-0.5">{sub}</p>
    </div>
  );
}
