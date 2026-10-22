import { useEffect, useState } from "react";
import { getI18n, useTranslation } from "react-i18next";
import { getStoredLanguage, setLanguage as setI18nLanguage, type Lang } from "../i18n";
import {
  createTeamDatabaseBackup,
  CLOUD_CHANGED,
  deleteAllSessions,
  exportTeamWorkspace,
  getCloudStatus,
  getSettings,
  getTrustedWorkspaces,
  getWebSearch,
  importTeamWorkspace,
  setAutoApprove,
  setAutoApproveShadow,
  setCloudTelemetry,
  setCompactionSettings,
  setContextBar,
  setOnboarded,
  setPdfSettings,
  setScratchBase,
  setSessionsPeek,
  setWorkspaceTrusted,
  setWebSearch,
  type CompactionSettings,
  type CloudStatus,
  type ModelSettings,
  type PdfSettings,
  type WebSearchSettings,
  type WorkspaceCommandTrust,
} from "../api";
import {
  cancelDictationModelDownload,
  deleteDictationModel,
  downloadDictationModel,
  getAutostart,
  getDictationStatus,
  getKeepAwake,
  checkForUpdate,
  installUpdate,
  updatesEnabled,
  isTauri,
  listenDictationDownloadProgress,
  markDictationTestPassed,
  pickFolder,
  setAutostart,
  setKeepAwake,
  startDictation,
  stopDictation,
  verifyDictationModel,
  type DictationDownloadProgress,
  type DictationStatus,
} from "../tauri";
import {
  DESKTOP_NOTIFICATION_PREFERENCE_VALUES,
  isDesktopNotificationPreference,
  readDesktopNotificationPreference,
  writeDesktopNotificationPreference,
  type DesktopNotificationPreference,
} from "../preferences";
import { requestDesktopNotificationPermission } from "../desktopNotifications";
import { Toggle } from "./Toggle";
import { useThemePref, useSidebarTint, SIDEBAR_TINT_OPTIONS } from "../theme";
import { DayNightToggle } from "./DayNightToggle";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";
import { ModelsTab } from "./ManageTabs";
import { MemorySection } from "./MemorySection";
import { PersonasTab } from "./PersonasTab";
import { RemoteTab } from "./RemoteTab";
import { SkillsTab } from "./SkillsTab";
import { showPersonas } from "../flags";

// Settings, restructured (Option 2) into a full-page surface that mirrors IntegrationsView's shell:
// a left sub-nav (Appearance · Files · Models · Personas) + centered panel, replacing the old
// top-tab ManageModal. Local/app concerns live here; anything external (Connectors, Messaging, MCP,
// Activity) stays under Integrations. Appearance + Files are re-skinned to the mock's Tailwind idiom;
// Models + Personas host the existing tab components inside the page shell (field re-skin to follow).
// "appearance" is the General tab's stable key — callers deep-link with it, so the
// rename (UX-021) changed only the label. "files" folded into General as a card.
type SetTab = "appearance" | "models" | "remote" | "context" | "skills" | "voice" | "memory" | "personas" | "backup";

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[13px] font-medium text-ink";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT = "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[13px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";

const SET_GROUPS: {
  titleKey: string;
  defaultTitle: string;
  tabs: {
    key: SetTab;
    labelKey: string;
    icon: "sliders" | "code" | "server" | "mic" | "archive" | "sparkle" | "book" | "refresh" | "files" | "users";
  }[];
}[] = [
  {
    titleKey: "settings.group.personal",
    defaultTitle: "Cá nhân",
    tabs: [
      { key: "appearance", labelKey: "settings.tab.general", icon: "sliders" },
      { key: "models", labelKey: "settings.tab.models", icon: "code" },
      { key: "skills", labelKey: "settings.tab.skills", icon: "book" },
      { key: "voice", labelKey: "settings.tab.voice", icon: "mic" },
      { key: "memory", labelKey: "settings.tab.memory", icon: "archive" },
    ],
  },
  {
    titleKey: "settings.group.infra",
    defaultTitle: "Hạ tầng & Dữ liệu",
    tabs: [
      { key: "remote", labelKey: "settings.tab.remote", icon: "server" },
      { key: "context", labelKey: "settings.tab.context", icon: "refresh" },
      { key: "backup", labelKey: "settings.tab.backup", icon: "files" },
      { key: "personas", labelKey: "settings.tab.personas", icon: "users" },
    ],
  },
];

export function SettingsView({
  initialTab,
  onOpenPersona,
  onCreateSkill,
  showThinking,
  onShowThinkingChange,
  onClose,
}: {
  initialTab?: SetTab;
  onOpenPersona?: (id: string) => void;
  // Skills doorway (SKILLS-SPEC §5.2): start a new conversation with the description
  // prefilled — the worker builds the skill and proposes it via save_skill.
  onCreateSkill?: (description: string) => void;
  showThinking: boolean;
  onShowThinkingChange: (enabled: boolean) => void;
  onClose?: () => void;
}) {
  const { t } = useTranslation();
  // Personas is flag-gated (hidden for launch) — filter the tab AND coerce a stale
  // deep-link to it (openSettings("personas") callers) so the page never opens on a
  // section with no nav entry.
  const personas = showPersonas();
  const wanted = initialTab && (personas || initialTab !== "personas") ? initialTab : "appearance";
  const [tab, setTab] = useState<SetTab>(wanted);
  const [search, setSearch] = useState("");

  const filteredGroups = SET_GROUPS.map((group) => ({
    ...group,
    tabs: group.tabs
      .filter((tb) => (personas ? true : tb.key !== "personas"))
      .filter((tb) => {
        if (!search.trim()) return true;
        const label = t(tb.labelKey).toLowerCase();
        return label.includes(search.trim().toLowerCase());
      }),
  })).filter((group) => group.tabs.length > 0);

  return (
    <main className="settings-surface flex-1 min-w-0 min-h-0 flex h-full bg-paper overflow-hidden">
      {/* Left Settings Sidebar */}
      <nav className="w-[240px] shrink-0 bg-chrome flex flex-col h-full border-r border-line select-none">
        {/* Top: Back to app button */}
        <div className="h-[42px] flex items-center px-3.5 shrink-0 border-b border-line">
          {onClose && (
            <button
              onClick={onClose}
              className="flex items-center gap-2 text-[13px] font-medium text-muted hover:text-ink transition-colors py-1 px-2 rounded-lg hover:bg-chromeHover shrink-0"
              title={t("settings.back_to_app", "Quay lại ứng dụng")}
            >
              <Icon name="arrowLeft" size={15} />
              <span>{t("settings.back_to_app", "Quay lại ứng dụng")}</span>
            </button>
          )}
        </div>

        {/* Search input */}
        <div className="px-3.5 pt-2.5 mb-2 shrink-0">
          <div className="relative">
            <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint pointer-events-none">
              <Icon name="search" size={13} />
            </span>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("settings.search_placeholder", "Tìm cài đặt...")}
              className="w-full pl-8 pr-7 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink placeholder:text-faint outline-none focus:border-accent transition-colors"
              style={{ WebkitAppRegion: "no-drag" } as any}
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-faint hover:text-ink text-[11px]"
                style={{ WebkitAppRegion: "no-drag" } as any}
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Navigation list */}
        <div className="flex-1 overflow-y-auto hairline-scroll px-2.5 pb-4">
          {filteredGroups.map((group) => (
            <div key={group.titleKey} className="mb-4">
              <div className="px-3 mb-1 text-[11px] font-semibold text-faint uppercase tracking-wider">
                {t(group.titleKey, group.defaultTitle)}
              </div>
              <div className="flex flex-col gap-0.5">
                {group.tabs.map((tb) => {
                  const active = tab === tb.key;
                  return (
                    <button
                      key={tb.key}
                      className={
                        "w-full text-left px-3 py-2 rounded-xl text-[13px] flex items-center gap-2.5 transition-colors " +
                        (active
                          ? "bg-chromeHover text-ink font-semibold shadow-xs"
                          : "text-muted hover:bg-chromeHover/60 hover:text-ink")
                      }
                      onClick={() => setTab(tb.key)}
                      style={{ WebkitAppRegion: "no-drag" } as any}
                    >
                      <Icon name={tb.icon} size={15} className={active ? "text-ink" : "text-muted"} />
                      <span>{t(tb.labelKey)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </nav>

      {/* Right Content Pane */}
      <div className="flex-1 min-w-0 h-full bg-canvas flex flex-col overflow-hidden">
        <div className="flex-1 min-h-0 overflow-y-auto hairline-scroll px-10 py-6 pb-16">
          <div className={`${tab === "skills" ? "max-w-5xl" : "max-w-4xl"} mx-auto`}>
            {tab === "appearance" ? (
              <AppearanceSection
                showThinking={showThinking}
                onShowThinkingChange={onShowThinkingChange}
              />
            ) : tab === "models" ? (
            <section>
              <PanelHead
                title={t("settings.tab.models")}
                sub={t("settings.models_sub")}
              />
              <ModelsTab />
            </section>
          ) : tab === "remote" ? (
            <RemoteTab />
          ) : tab === "context" ? (
            <section>
              <PanelHead
                title="Context optimization"
                sub="How sessions spend tokens — attachment handling and long-history compaction."
              />
              <TokenSavingsCard />
              <CompactionCard />
            </section>
          ) : tab === "skills" ? (
            <SkillsTab onCreateSkill={onCreateSkill} />
          ) : tab === "voice" ? (
            <VoiceInputSection />
          ) : tab === "memory" ? (
            <MemorySection />
          ) : tab === "backup" ? (
            <WorkspaceBackupSection />
          ) : (
            <PersonasSection onOpenPersona={onOpenPersona} />
          )}
          </div>
        </div>
      </div>
    </main>
  );
}
// -- Voice input: deliberate model provisioning + compatibility + microphone test (§37) --------
const voiceError = (error: unknown) =>
  error instanceof Error
    ? error.message
    : typeof error === "string"
      ? error
      : getI18n().getFixedT(null, "translation")("settings.voice_action_failed");

const formatBytes = (bytes: number) => {
  if (!bytes) return "0 MiB";
  return `${Math.round(bytes / 1024 / 1024)} MiB`;
};

function VoiceInputSection() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [progress, setProgress] = useState<DictationDownloadProgress | null>(null);
  const [phase, setPhase] = useState<"idle" | "downloading" | "verifying" | "testing" | "transcribing">("idle");
  const [error, setError] = useState<string | null>(null);
  const [testTranscript, setTestTranscript] = useState("");
  const desktop = isTauri();

  const publish = (next: DictationStatus) => {
    setStatus(next);
    window.dispatchEvent(new CustomEvent("coworker:voice-input-changed", { detail: next }));
  };

  useEffect(() => {
    if (!desktop) return;
    let active = true;
    let unlisten = () => {};
    void listenDictationDownloadProgress((next) => {
      if (active) setProgress(next);
    }).then((stop) => {
      unlisten = stop;
    });
    void getDictationStatus().then(async (initial) => {
      if (!active || !initial) return;
      publish(initial);
      // One-time migration for models installed by the first STT cut, before verification markers.
      if (initial.model_installed && !initial.model_verified) {
        setPhase("verifying");
        try {
          const verified = await verifyDictationModel();
          if (active) publish(verified);
        } catch (verifyError) {
          if (active) setError(voiceError(verifyError));
        } finally {
          if (active) setPhase("idle");
        }
      }
    });
    return () => {
      active = false;
      unlisten();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop]);

  const download = async () => {
    setError(null);
    setProgress({ downloaded_bytes: 0, total_bytes: status?.model_bytes || 0 });
    setPhase("downloading");
    try {
      publish(await downloadDictationModel());
    } catch (downloadError) {
      setError(voiceError(downloadError));
      const latest = await getDictationStatus();
      if (latest) publish(latest);
    } finally {
      setPhase("idle");
    }
  };

  const cancelDownload = async () => {
    await cancelDictationModelDownload().catch(() => undefined);
  };

  const repair = async () => {
    setError(null);
    try {
      publish(await deleteDictationModel());
      await download();
    } catch (repairError) {
      setError(voiceError(repairError));
    }
  };

  const remove = async () => {
    if (!window.confirm(t("settings.voice_delete_confirm"))) return;
    setError(null);
    try {
      publish(await deleteDictationModel());
      setTestTranscript("");
      setProgress(null);
    } catch (deleteError) {
      setError(voiceError(deleteError));
    }
  };

  const toggleTest = async () => {
    if (!status?.supported || !status.model_verified) return;
    setError(null);
    try {
      if (status.recording) {
        setPhase("transcribing");
        const transcript = (await stopDictation()).trim();
        setTestTranscript(transcript);
        if (!transcript) throw new Error(t("settings.voice_no_speech"));
        publish(await markDictationTestPassed());
      } else {
        setTestTranscript("");
        setPhase("testing");
        publish(await startDictation());
      }
    } catch (testError) {
      setError(voiceError(testError));
      const latest = await getDictationStatus();
      if (latest) publish(latest);
    } finally {
      setPhase("idle");
    }
  };

  const downloading = phase === "downloading" || !!status?.download_in_progress;
  const progressTotal = progress?.total_bytes || status?.model_bytes || 1;
  const progressPercent = Math.min(100, Math.round(((progress?.downloaded_bytes || 0) / progressTotal) * 100));
  const ready = !!status?.supported && !!status?.model_verified && !!status?.test_passed;

  return (
    <section>
      <PanelHead
        title={t("settings.tab.voice")}
        sub={t("settings.voice_intro")}
      />

      {!desktop ? (
        <div className={CARD + " p-4 text-[13px] text-muted"}>{t("settings.voice_desktop_only")}</div>
      ) : (
        <div className="space-y-4">
          <div className="rounded-xl border border-green-200 bg-green-50/70 px-4 py-3 text-[13px] text-green-800">
            <span className="font-medium">{t("settings.voice_private_title")}</span>{" "}
            {t("settings.voice_private_body")}
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-start gap-3">
              <Icon name="code" size={18} className="text-accent mt-0.5" />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium">{t("settings.voice_device_title")}</div>
                <div className="text-[12px] text-muted mt-1">{status?.device_summary || t("settings.voice_checking")}</div>
                {status?.compatibility_reason && <div className="text-[12px] text-red-600 mt-1.5">{status.compatibility_reason}</div>}
              </div>
              {status && (
                <span className={"text-[12px] px-2 py-1 rounded-full " + (status.supported ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600")}>
                  {status.supported ? `● ${t("settings.voice_compatible")}` : t("settings.voice_unsupported")}
                </span>
              )}
            </div>
            <div className="border-t border-line bg-paper/50 px-4 py-3 grid grid-cols-2 gap-3 text-[12px] text-muted">
              <div><span className="block text-ink font-medium">{t("settings.voice_mac")}</span>{t("settings.voice_mac_detail")}</div>
              <div><span className="block text-ink font-medium">{t("settings.voice_windows")}</span>{t("settings.voice_windows_detail")}</div>
              <div><span className="block text-ink font-medium">{t("settings.voice_memory")}</span>{t("settings.voice_memory_detail")}</div>
              <div><span className="block text-ink font-medium">{t("settings.voice_processor")}</span>{t("settings.voice_processor_detail")}</div>
            </div>
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-accentSoft text-accent grid place-items-center font-semibold">W</div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium">{t("settings.voice_whisper_title")}</div>
                <div className="text-[12px] text-muted mt-0.5">
                  {status?.model_verified
                    ? t("settings.voice_installed", { size: formatBytes(status.model_bytes) })
                    : t("settings.voice_not_installed", { size: formatBytes(status?.model_bytes || 147_964_211) })}
                </div>
              </div>
              {status?.model_verified ? (
                <>
                  <span className="text-[12px] px-2 py-1 rounded-full bg-green-50 text-green-700">{t("settings.voice_verified")}</span>
                  <button className={BTN_BORDERED} onClick={() => void repair()}>{t("settings.voice_repair")}</button>
                  <button className="text-[12px] text-red-600 px-2 py-2" onClick={() => void remove()}>{t("settings.voice_delete")}</button>
                </>
              ) : downloading ? (
                <button className={BTN_BORDERED} onClick={() => void cancelDownload()}>{t("common.stop")}</button>
              ) : phase === "verifying" ? (
                <span className="text-[12px] text-muted">{t("settings.voice_verifying")}</span>
              ) : (
                <button className={BTN_ACCENT} disabled={!status?.supported} onClick={() => void download()}>{t("settings.voice_download")}</button>
              )}
            </div>
            {downloading && (
              <div className="border-t border-line px-4 py-3">
                <div className="h-1.5 rounded-full bg-line overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${progressPercent}%` }} /></div>
                <div className="mt-1.5 text-[12px] text-muted flex"><span>{t("settings.voice_dl_progress", { done: formatBytes(progress?.downloaded_bytes || 0), total: formatBytes(progressTotal) })}</span><span className="ml-auto">{progressPercent}%</span></div>
              </div>
            )}
          </div>

          <div className={CARD}>
            <div className="p-4 flex items-center gap-3">
              <Icon name="mic" size={18} className={ready ? "text-green-600" : "text-muted"} />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium">{t("settings.voice_mic_test_title")}</div>
                <div className="text-[12px] text-muted mt-0.5">
                  {ready ? t("settings.voice_mic_test_ready") : t("settings.voice_mic_test_pending")}
                </div>
              </div>
              {ready && <span className="text-[12px] px-2 py-1 rounded-full bg-green-50 text-green-700">● {t("settings.voice_ready_badge")}</span>}
              <button className={BTN_BORDERED} disabled={!status?.supported || !status?.model_verified || phase === "transcribing"} onClick={() => void toggleTest()}>
                {status?.recording
                  ? t("settings.voice_stop_check")
                  : phase === "transcribing"
                    ? t("settings.voice_transcribing")
                    : ready
                      ? t("settings.voice_test_again")
                      : t("settings.voice_test_mic")}
              </button>
            </div>
            {status?.recording && <div className="border-t border-line px-4 py-3 text-[12px] text-accent" role="status">{t("settings.voice_listening")}</div>}
            {testTranscript && <div className="border-t border-line bg-paper/50 px-4 py-3 text-[13px]">“{testTranscript}”</div>}
          </div>

          {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] text-red-700">{error}</div>}
        </div>
      )}
    </section>
  );
}

// -- Personas: installed/enabled/delete management, the dir/Git importer, and the
// entry point to the Persona Gallery (a screen-sized modal — installs finish back
// here, disabled pending consent; a gallery install re-mounts the list in place).
// The Gallery entry point is GONE (owner 2026-08-21) — coworkers install from
// GitHub / folder / zip only. GalleryModal stays in the tree for the gallery's
// possible return as a first-class distribution surface, but nothing mounts it.
function PersonasSection({ onOpenPersona }: { onOpenPersona?: (id: string) => void }) {
  const { t } = useTranslation();
  return (
    <section>
      <PanelHead title={t("settings.tab.personas")} sub={t("settings.personas_intro")} />
      <p className="text-[13px] text-muted leading-relaxed max-w-[560px] mt-5 mb-1">
        {t("settings.personas_desc")}
      </p>
      <PersonasTab onOpenPersona={onOpenPersona} />
    </section>
  );
}

// -- Appearance + app behaviour ------------------------------------------------
function AppearanceSection({
  showThinking,
  onShowThinkingChange,
}: {
  showThinking: boolean;
  onShowThinkingChange: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const [theme, setTheme] = useThemePref();
  const [tint, setTint] = useSidebarTint();
  const [autostart, setAuto] = useState(false);
  const [keepAwake, setKeep] = useState(false);
  const desktop = isTauri();
  // "system" = no explicit choice persisted; the app follows the OS locale.
  const [currentLang, setCurrentLang] = useState<Lang | "system">(() => getStoredLanguage() ?? "system");

  useEffect(() => {
    if (isTauri()) {
      getAutostart().then((v) => setAuto(!!v));
      getKeepAwake().then((v) => setKeep(!!v));
    }
  }, []);

  const toggleAuto = async (v: boolean) => setAuto(!!(await setAutostart(v)));
  const toggleKeep = async (v: boolean) => setKeep(!!(await setKeepAwake(v)));
  const runSetupAgain = async () => {
    await setOnboarded(false);
    window.dispatchEvent(new CustomEvent("coworker:open-onboarding"));
  };
  const changeLang = (lang: Lang | "system") => {
    setCurrentLang(lang);
    void setI18nLanguage(lang === "system" ? null : lang);
  };

  const isElectron = typeof window !== "undefined" && Boolean((window as any).desktop?.isElectron);
  const [menuBarVisible, setMenuBarVisible] = useState<boolean>(() => {
    try {
      return localStorage.getItem("coworker:menu-bar-visible:v1") === "1";
    } catch {
      return false;
    }
  });

  return (
    <section>
      <PanelHead title={t("settings.general_title")} sub={t("settings.general_sub")} />

      {isElectron && (
        <div className={CARD + " p-4 mb-4"}>
          <div className="flex items-center justify-between">
            <div>
              <div className={FIELD_LABEL}>{t("settings.app_menu_bar", "Application menu bar (File, Edit, View, Window)")}</div>
              <div className={FIELD_HELP + " mt-0.5"}>
                {t("settings.app_menu_bar_desc", "Show or hide the application menu bar at the top of the window")}
              </div>
            </div>
            <Toggle
              checked={menuBarVisible}
              onChange={(checked) => {
                setMenuBarVisible(checked);
                try {
                  localStorage.setItem("coworker:menu-bar-visible:v1", checked ? "1" : "0");
                } catch {}
                window.dispatchEvent(new CustomEvent("coworker:menu-bar-visible", { detail: checked }));
                (window as any).desktop?.setMenuBarVisibility?.(checked);
              }}
              title={t("settings.app_menu_bar", "Application menu bar")}
            />
          </div>
        </div>
      )}

      <div className={CARD + " p-4 mb-4"}>
        <div className="flex items-center justify-between">
          <div>
            <div className={FIELD_LABEL}>{t("settings.theme")}</div>
            <div className={FIELD_HELP + " mt-1"}>
              {theme === "auto"
                ? t("settings.theme_auto_help")
                : theme === "dark"
                  ? t("settings.theme_dark")
                  : t("settings.theme_light")}
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <DayNightToggle
              checked={
                theme === "dark" ||
                (theme === "auto" && typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches)
              }
              onChange={(checked) => setTheme(checked ? "dark" : "light")}
              size={13}
            />
            <button
              type="button"
              className={"theme-auto-chip" + (theme === "auto" ? " theme-auto-chip-active" : "")}
              onClick={() => setTheme("auto")}
              aria-pressed={theme === "auto"}
              title={t("settings.theme_auto_help")}
            >
              <span className="theme-auto-chip-icon">
                <Icon name="sliders" size={13} />
              </span>
              <span>{t("settings.theme_auto")}</span>
            </button>
          </div>
        </div>
      </div>

      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.sidebar_tint")}</div>
        <div className={FIELD_HELP + " mt-1 mb-3"}>{t("settings.sidebar_tint_help")}</div>
        <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-5 lg:grid-cols-5 gap-2.5" role="radiogroup" aria-label={t("settings.sidebar_tint")}>
          {SIDEBAR_TINT_OPTIONS.map((opt) => {
            const isSelected = tint === opt.id;
            const isDark =
              theme === "dark" ||
              (theme === "auto" && typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches);
            const previewBg = isDark ? opt.darkBg : opt.lightBg;
            const previewBorder = isDark ? opt.borderDark : opt.borderLight;

            return (
              <button
                key={opt.id}
                type="button"
                role="radio"
                aria-checked={isSelected}
                onClick={() => setTint(opt.id)}
                className={
                  "group relative flex flex-col items-center gap-2 p-2.5 rounded-xl border text-center transition-all " +
                  (isSelected
                    ? "border-accent bg-accentSoft/40 shadow-xs ring-2 ring-accent/20"
                    : "border-line bg-paper/60 hover:border-lineStrong hover:bg-paper")
                }
              >
                <div
                  className="w-full h-9 rounded-lg border shadow-2xs flex items-center justify-center transition-transform group-hover:scale-[1.03]"
                  style={{ backgroundColor: previewBg, borderColor: previewBorder }}
                >
                  {isSelected && (
                    <span className="w-2 h-2 rounded-full bg-accent shadow-xs" />
                  )}
                </div>
                <span className={"text-[12px] truncate w-full " + (isSelected ? "font-semibold text-ink" : "text-muted group-hover:text-ink")}>
                  {t(opt.nameKey)}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.language")}</div>
        <div className="seg mt-2.5" role="radiogroup" aria-label={t("settings.language_aria")}>
          {(["system", "en", "vi", "zh"] as const).map((lng) => (
            <button
              key={lng}
              className={lng === currentLang ? "active" : ""}
              onClick={() => changeLang(lng)}
            >
              {lng === "zh"
                ? t("settings.language_zh")
                : lng === "vi"
                ? t("settings.language_vi")
                : lng === "en"
                ? t("settings.language_en")
                : t("settings.language_system")}
            </button>
          ))}
        </div>
        <div className={FIELD_HELP}>{t("settings.language_help")}</div>
      </div>

      <PreferenceControls
        showThinking={showThinking}
        onShowThinkingChange={onShowThinkingChange}
      />
      <SidebarCard />

      <ContextBarCard />

      <AutoApproveCard />

      <FilesCard />

      <TrustedWorkspacesCard />

      {desktop && (
        <div className={CARD + " p-4"}>
          <div className={FIELD_LABEL + " mb-2.5"}>{t("settings.always_on")}</div>
          <label className="flex items-start gap-3 py-2">
            <input type="checkbox" className="mt-0.5" checked={autostart} onChange={(e) => toggleAuto(e.target.checked)} />
            <span>
              <span className="block text-[13px] text-ink">{t("settings.open_at_login")}</span>
              <span className="block text-[12px] text-muted">{t("settings.open_at_login_help")}</span>
            </span>
          </label>
          <label className="flex items-start gap-3 py-2">
            <input type="checkbox" className="mt-0.5" checked={keepAwake} onChange={(e) => toggleKeep(e.target.checked)} />
            <span>
              <span className="block text-[13px] text-ink">{t("settings.keep_awake")}</span>
              <span className="block text-[12px] text-muted">{t("settings.keep_awake_help")}</span>
            </span>
          </label>
        </div>
      )}

      {/* One card for the app-lifecycle actions (UX-021): the onboarding replay (§24 —
          every build, the browser dev shell runs the same first-run flow) and, on
          desktop, the manual update check (launch also checks automatically). */}
      <div className={CARD + " p-4 mt-4"}>
        <div className={FIELD_LABEL + " mb-2"}>{t("settings.setup_updates")}</div>
        <div className="flex items-center gap-2">
          <button className={BTN_BORDERED} onClick={runSetupAgain}>
            {t("settings.run_setup_again")}
          </button>
          {desktop && <UpdateInline />}
        </div>
        <div className={FIELD_HELP}>{t("settings.run_setup_help")}</div>
      </div>
    </section>
  );
}

function PreferenceControls({
  showThinking,
  onShowThinkingChange,
}: {
  showThinking: boolean;
  onShowThinkingChange: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const [desktopNotifications, setDesktopNotifications] = useState<DesktopNotificationPreference>(
    readDesktopNotificationPreference,
  );
  const [notificationPermission, setNotificationPermission] = useState<NotificationPermission | "unsupported">(() => {
    if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
    return window.Notification.permission;
  });
  const [notificationBusy, setNotificationBusy] = useState(false);

  const changeDesktopNotifications = async (value: string) => {
    if (!isDesktopNotificationPreference(value)) return;
    setDesktopNotifications(value);
    writeDesktopNotificationPreference(value);
    if (value === "off") return;

    setNotificationBusy(true);
    try {
      setNotificationPermission(await requestDesktopNotificationPermission());
    } finally {
      setNotificationBusy(false);
    }
  };

  return (
    <>
      <div className={CARD + " p-4 mb-4"} data-testid="settings-reasoning-card">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className={FIELD_LABEL}>{t("settings.show_model_reasoning")}</div>
            <div className={FIELD_HELP}>{t("settings.show_model_reasoning_desc")}</div>
          </div>
          <Toggle
            checked={showThinking}
            onChange={onShowThinkingChange}
            title={t("settings.show_model_reasoning")}
          />
        </div>
      </div>

      <div className={CARD + " p-4 mb-4"} data-testid="settings-desktop-notifications-card">
        <div className={FIELD_LABEL}>{t("settings.desktop_notifications_title")}</div>
        <div className={FIELD_HELP}>{t("settings.desktop_notifications_desc")}</div>
        <div className="mt-3 flex items-center gap-3">
          <label className="text-[13px] text-ink" htmlFor="desktop-notification-preference">
            {t("settings.desktop_notifications_mode")}
          </label>
          <select
            id="desktop-notification-preference"
            value={desktopNotifications}
            disabled={notificationBusy}
            aria-label={t("settings.desktop_notifications_mode")}
            data-testid="settings-desktop-notifications-select"
            className="w-44 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent disabled:opacity-50"
            onChange={(event) => void changeDesktopNotifications(event.target.value)}
          >
            {DESKTOP_NOTIFICATION_PREFERENCE_VALUES.map((value) => (
              <option key={value} value={value}>
                {t(`settings.desktop_notifications_${value}`)}
              </option>
            ))}
          </select>
        </div>
        {notificationPermission === "denied" && (
          <div className={FIELD_HELP} role="status">{t("settings.desktop_notifications_denied")}</div>
        )}
        {notificationPermission === "unsupported" && (
          <div className={FIELD_HELP} role="status">{t("settings.desktop_notifications_unsupported")}</div>
        )}
      </div>

      <PrivacyCard />
      <WebSearchCard />
      <ClearHistoryCard />
    </>
  );
}

function PrivacyCard() {
  const { t } = useTranslation();
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    const refresh = () => {
      void getCloudStatus().then(setCloud).catch(() => setCloud(null));
    };
    refresh();
    window.addEventListener(CLOUD_CHANGED, refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener(CLOUD_CHANGED, refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);

  const updateTelemetry = async (enabled: boolean) => {
    setSaving(true);
    setError(false);
    try {
      const result = await setCloudTelemetry(enabled);
      if (!result.ok) throw new Error("telemetry preference was rejected");
      setCloud((current) =>
        current
          ? { ...current, telemetry_enabled: result.telemetry_enabled ?? enabled }
          : current,
      );
    } catch {
      setError(true);
    } finally {
      setSaving(false);
    }
  };

  const telemetryEnabled = cloud?.telemetry_enabled !== false;

  return (
    <div className={CARD + " p-4 mb-4"} data-testid="settings-privacy-card">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className={FIELD_LABEL}>{t("settings.privacy_title")}</div>
          <div className={FIELD_HELP}>{t("settings.privacy_desc")}</div>
        </div>
        <Toggle
          checked={telemetryEnabled}
          disabled={!cloud || saving}
          onChange={(enabled) => void updateTelemetry(enabled)}
          title={t("settings.share_anonymous_usage")}
        />
      </div>
      <div className={FIELD_HELP + " mt-3 pt-3 border-t border-line"}>
        {cloud?.signed_in ? t("settings.privacy_signed_in") : t("settings.privacy_signed_out")}
      </div>
      {error && <div className="text-[12px] text-danger mt-2" role="alert">{t("settings.privacy_save_error")}</div>}
    </div>
  );
}

const WEB_SEARCH_KEY_PROVIDERS = new Set(["exa", "parallel", "tavily", "brave"]);

function WebSearchCard() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<WebSearchSettings | null>(null);
  const [provider, setProvider] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<"saved" | "error" | "key" | null>(null);

  useEffect(() => {
    void getWebSearch()
      .then((current) => {
        setSettings(current);
        setProvider(current.provider);
      })
      .catch(() => setMessage("error"));
  }, []);

  const needsKey = WEB_SEARCH_KEY_PROVIDERS.has(provider);
  const save = async () => {
    if (!settings || saving) return;
    const trimmedKey = apiKey.trim();
    const keepingExistingKey = provider === settings.provider && settings.has_key && !trimmedKey;
    if (needsKey && !trimmedKey && !keepingExistingKey) {
      setMessage("key");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const result = await setWebSearch(provider, trimmedKey || undefined);
      if (!result.ok) throw new Error(result.error || "web search provider rejected");
      setSettings({
        ...settings,
        provider,
        has_key: needsKey && (Boolean(trimmedKey) || keepingExistingKey),
      });
      setApiKey("");
      setMessage("saved");
    } catch {
      setMessage("error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={CARD + " p-4 mb-4"} data-testid="settings-web-search-card">
      <div className={FIELD_LABEL}>{t("settings.web_search_title")}</div>
      <div className={FIELD_HELP + " mt-1"}>{t("settings.web_search_desc")}</div>
      <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
        <label className="min-w-0">
          <span className={FIELD_LABEL + " block mb-1.5"}>{t("settings.web_search_provider")}</span>
          <select
            value={provider}
            disabled={!settings || saving}
            aria-label={t("settings.web_search_provider")}
            data-testid="settings-web-search-provider"
            className="w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent disabled:opacity-50"
            onChange={(event) => {
              setProvider(event.target.value);
              setMessage(null);
            }}
          >
            {settings?.providers.map((name) => (
              <option key={name} value={name}>
                {t(`settings.web_search_provider_${name}`, { defaultValue: name })}
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-0">
          <span className={FIELD_LABEL + " block mb-1.5"}>{t("settings.web_search_api_key")}</span>
          <input
            value={apiKey}
            type="password"
            autoComplete="off"
            disabled={!settings || saving}
            placeholder={needsKey ? t("settings.web_search_api_key_placeholder") : t("settings.web_search_no_key")}
            aria-label={t("settings.web_search_api_key")}
            data-testid="settings-web-search-api-key"
            className="w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent disabled:opacity-50"
            onChange={(event) => {
              setApiKey(event.target.value);
              setMessage(null);
            }}
          />
        </label>
        <button
          type="button"
          className={BTN_ACCENT}
          disabled={!settings || saving}
          data-testid="settings-web-search-save"
          onClick={() => void save()}
        >
          {saving ? t("common.saving") : t("settings.web_search_save")}
        </button>
      </div>
      <div className={FIELD_HELP + " mt-2"}>{t("settings.web_search_api_key_help")}</div>
      {message === "saved" && <div className="text-[12px] text-green-700 mt-2" role="status">{t("settings.web_search_saved")}</div>}
      {message === "key" && <div className="text-[12px] text-danger mt-2" role="alert">{t("settings.web_search_key_required")}</div>}
      {message === "error" && <div className="text-[12px] text-danger mt-2" role="alert">{t("settings.web_search_save_error")}</div>}
    </div>
  );
}

function TrustedWorkspacesCard() {
  const { t } = useTranslation();
  const [workspaces, setWorkspaces] = useState<WorkspaceCommandTrust[] | null>(null);

  const refresh = () =>
    getTrustedWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]));

  useEffect(() => {
    refresh();
  }, []);

  const revoke = async (path: string) => {
    if (!window.confirm(t("settings.trust_revoke_confirm", { path }))) return;
    await setWorkspaceTrusted(path, false);
    refresh();
  };

  return (
    <div className={CARD + " p-4 mb-4"} data-testid="trusted-workspaces-card">
      <div className={FIELD_LABEL}>{t("settings.trusted_workspaces")}</div>
      <div className={FIELD_HELP}>
        {t("settings.trusted_workspaces_help")}
      </div>
      {workspaces === null ? (
        <div className="text-[12px] text-muted mt-3">{t("settings.trust_loading")}</div>
      ) : workspaces.length === 0 ? (
        <div className="text-[12px] text-muted mt-3">{t("settings.trust_empty")}</div>
      ) : (
        <div className="mt-3 divide-y divide-line">
          {workspaces.map((workspace) => (
            <div key={workspace.workspace} className="py-2.5 flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[13px] text-ink break-all">{workspace.workspace}</div>
                <div className="text-[12px] text-muted mt-0.5">
                  {workspace.requested_commands.length
                    ? t("settings.trust_allowances", { count: workspace.requested_commands.length })
                    : t("settings.trust_no_allowances")}
                  {!workspace.exists ? t("settings.trust_folder_unavailable") : ""}
                </div>
              </div>
              <button
                className="text-[12px] text-red-600 px-2 py-1"
                onClick={() => void revoke(workspace.workspace)}
              >
                {t("settings.trust_revoke")}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function UpdateInline() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    void updatesEnabled().then((value) => {
      if (!cancelled) setEnabled(value);
    });
    return () => { cancelled = true; };
  }, []);
  const [state, setState] = useState<"idle" | "checking" | "none" | "found" | "installing" | "error">("idle");
  const [version, setVersion] = useState("");

  const check = async () => {
    setState("checking");
    try {
      const u = await checkForUpdate();
      if (u) {
        setVersion(u.version);
        setState("found");
      } else {
        setState("none");
      }
    } catch {
      setState("error");
    }
  };

  const install = async () => {
    setState("installing");
    try {
      await installUpdate(); // success restarts the app
    } catch {
      setState("error");
    }
  };

  if (enabled !== true) {
    return (
      <span className="text-[12px] text-muted" data-testid="settings-update-disabled">
        {t(enabled === null ? "settings.checking" : "settings.update_development")}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2.5">
      {state === "found" ? (
        <button className={BTN_BORDERED} onClick={install} data-testid="settings-update-install">
          {t("settings.update_install", { version })}
        </button>
      ) : (
        <button
          className={BTN_BORDERED}
          onClick={check}
          disabled={state === "checking" || state === "installing"}
          data-testid="settings-update-check"
        >
          {state === "checking" ? t("settings.checking") : t("settings.check_for_updates")}
        </button>
      )}
      {(state === "none" || state === "error" || state === "installing") && (
        <span className="text-[12px] text-muted">
          {state === "none"
            ? t("settings.update_latest")
            : state === "error"
              ? t("settings.update_error")
              : t("settings.update_downloading")}
        </span>
      )}
    </span>
  );
}

// Telemetry/Privacy card removed for this release (owner ask 2026-07-22); the
// setCloudTelemetry API stays for a future opt-out surface.

// -- Sidebar density -------------------------------------------------------------
// -- Token savings (PDF attachments; owner ask, 2026-07-17) ---------------------
// Attachments replay with EVERY turn, so a big PDF quietly multiplies token spend.
// This card is the attachment dial: attach thresholds + the fallback for models
// without native PDF support. (Long-history spend is handled by auto-compaction —
// the CompactionCard below, OPE-27.)
function TokenSavingsCard() {
  const { t } = useTranslation();
  const [pdf, setPdf] = useState<PdfSettings | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) =>
        setPdf({
          pdf_fallback: s.pdf_fallback || "text",
          pdf_max_pages: s.pdf_max_pages || 20,
          pdf_max_mb: s.pdf_max_mb || 10,
        }),
      )
      .catch(() => setPdf({ pdf_fallback: "text", pdf_max_pages: 20, pdf_max_mb: 10 }));
  }, []);

  const save = async (patch: Partial<PdfSettings>) => {
    setPdf((p) => (p ? { ...p, ...patch } : p));
    await setPdfSettings(patch);
  };

  if (!pdf) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="token-savings-card">
      <div className={FIELD_LABEL}>{t("settings.token_savings")}</div>
      <div className={FIELD_HELP}>
        {t("settings.token_savings_help")}
      </div>

      <div className="mt-3 text-[13px] text-ink">{t("settings.pdf_fallback_label")}</div>
      <div className="seg mt-2" role="radiogroup" aria-label={t("settings.pdf_fallback_aria")} data-testid="pdf-fallback">
        <button
          className={pdf.pdf_fallback === "text" ? "active" : ""}
          onClick={() => save({ pdf_fallback: "text" })}
        >
          {t("settings.pdf_extract_text")}
        </button>
        <button
          className={pdf.pdf_fallback === "images" ? "active" : ""}
          onClick={() => save({ pdf_fallback: "images" })}
        >
          {t("settings.pdf_send_images")}
        </button>
      </div>
      <div className={FIELD_HELP}>
        {t("settings.pdf_fallback_help")}
      </div>

      <div className="mt-3 flex items-center gap-5">
        <label className="flex items-center gap-2.5">
          <span className="text-[13px] text-ink">{t("settings.pdf_max_pages")}</span>
          <input
            type="number"
            min={1}
            max={100}
            value={pdf.pdf_max_pages}
            data-testid="pdf-max-pages"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            onChange={(e) => save({ pdf_max_pages: Math.max(1, Math.min(Number(e.target.value) || 20, 100)) })}
          />
        </label>
        <label className="flex items-center gap-2.5">
          <span className="text-[13px] text-ink">{t("settings.pdf_max_size")}</span>
          <input
            type="number"
            min={1}
            max={10}
            value={pdf.pdf_max_mb}
            data-testid="pdf-max-mb"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            onChange={(e) => save({ pdf_max_mb: Math.max(1, Math.min(Number(e.target.value) || 10, 10)) })}
          />
          <span className="text-[13px] text-muted">MB</span>
        </label>
      </div>
      <div className={FIELD_HELP}>
        {t("settings.pdf_limits_help")}
      </div>
    </div>
  );
}

// -- Context compaction (OPE-27) ------------------------------------------------
// Long sessions are summarized automatically when they approach the model's context
// limit, so work continues instead of hitting a raw provider error. Two spec'd
// overrides (trigger % + token cap) and the summarizer-model pin — nothing more.
function CompactionCard() {
  const [cfg, setCfg] = useState<CompactionSettings | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});

  useEffect(() => {
    getSettings()
      .then((s) => {
        setCfg({
          compaction_threshold_pct: s.compaction_threshold_pct ?? 0.8,
          compaction_cap_tokens: s.compaction_cap_tokens ?? 250_000,
          compaction_model: s.compaction_model ?? "",
        });
        setModels(s.models || []);
        setLabels(s.model_labels || {});
      })
      .catch(() =>
        setCfg({
          compaction_threshold_pct: 0.8,
          compaction_cap_tokens: 250_000,
          compaction_model: "",
        }),
      );
  }, []);

  const save = async (patch: Partial<CompactionSettings>) => {
    setCfg((p) => (p ? { ...p, ...patch } : p));
    await setCompactionSettings(patch);
  };

  if (!cfg) return null;
  const modelLabel = (id: string) => labels[id]?.split(" · ")[0] || id;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="compaction-card">
      <div className={FIELD_LABEL}>Context compaction</div>
      <div className={FIELD_HELP}>
        Long sessions are compacted automatically: older turns are summarized so the
        coworker keeps working instead of running out of context. Your visible transcript
        is never changed — a small marker shows where compaction happened.
      </div>

      <div className="mt-3 flex items-center gap-5 flex-wrap">
        <label className="flex items-center gap-2.5">
          <span className="text-[13px] text-ink">Compact at</span>
          <input
            type="number"
            min={10}
            max={95}
            value={Math.round(cfg.compaction_threshold_pct * 100)}
            data-testid="compaction-threshold"
            className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            onChange={(e) =>
              save({
                compaction_threshold_pct:
                  Math.max(10, Math.min(Number(e.target.value) || 80, 95)) / 100,
              })
            }
          />
          <span className="text-[13px] text-muted">% of the context window</span>
        </label>
        <label className="flex items-center gap-2.5">
          <span className="text-[13px] text-ink">or at</span>
          <input
            type="number"
            min={10_000}
            max={2_000_000}
            step={10_000}
            value={cfg.compaction_cap_tokens}
            data-testid="compaction-cap"
            className="w-28 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
            onChange={(e) =>
              save({
                compaction_cap_tokens: Math.max(
                  10_000,
                  Math.min(Number(e.target.value) || 250_000, 2_000_000),
                ),
              })
            }
          />
          <span className="text-[13px] text-muted">tokens, whichever is smaller</span>
        </label>
      </div>
      <div className={FIELD_HELP}>
        The cap makes very-large-context models compact early — quality and speed degrade
        well before their nominal limit.
      </div>

      <div className="mt-3 flex items-center gap-2.5">
        <span className="text-[13px] text-ink">Summarizer model</span>
        <select
          value={cfg.compaction_model}
          data-testid="compaction-model"
          className="px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
          onChange={(e) => save({ compaction_model: e.target.value })}
        >
          <option value="">Session&rsquo;s own model (default)</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {modelLabel(m)}
            </option>
          ))}
        </select>
      </div>
      <div className={FIELD_HELP}>
        The summary is written by this model. The default follows whatever model the
        session is using.
      </div>
    </div>
  );
}

// -- Composer: context-window bar (owner ask 2026-07-30) ------------------------
// The chip's bar is context-window occupancy; the session total (unbounded) lives in
// the popover. Some people would rather not watch a meter at all, hence the toggle.
function ContextBarCard() {
  const { t } = useTranslation();
  const [shown, setShown] = useState<boolean | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setShown(s.context_bar === true))
      .catch(() => setShown(false));
  }, []);

  const save = async (next: boolean) => {
    setShown(next);
    await setContextBar(next);
  };

  if (shown === null) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="context-bar-card">
      <div className={FIELD_LABEL}>{t("settings.composer_section")}</div>
      <label className="flex items-start gap-3 py-2">
        <input
          type="checkbox"
          className="mt-0.5"
          data-testid="context-bar-toggle"
          checked={shown}
          onChange={(e) => save(e.target.checked)}
        />
        <span>
          <span className="block text-[13px] text-ink">{t("settings.context_bar_title")}</span>
          <span className="block text-[12px] text-muted">{t("settings.context_bar_desc")}</span>
        </span>
      </label>
    </div>
  );
}

// Auto-Approve (spec §1.5): the experimental feature flag that adds the "Auto-Approve" mode
// to the composer's mode picker, plus its shadow-evaluation sibling. Both default off and are
// user-global (a cloned repo can't turn either on). Shadow is nested under the main flag — it
// only makes sense to measure the reviewer once you know what it is.
function AutoApproveCard() {
  const [on, setOn] = useState<boolean | null>(null);
  const [shadow, setShadow] = useState(false);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setOn(s.auto_approve === true);
        setShadow(s.auto_approve_shadow === true);
      })
      .catch(() => setOn(false));
  }, []);

  const saveOn = async (next: boolean) => {
    setOn(next);
    await setAutoApprove(next);
  };
  const saveShadow = async (next: boolean) => {
    setShadow(next);
    await setAutoApproveShadow(next);
  };

  if (on === null) return null;
  return (
    <div className={CARD + " p-4 mb-4"} data-testid="auto-approve-card">
      <div className={FIELD_LABEL}>Auto-approve (experimental)</div>
      <label className="flex items-start gap-3 py-2">
        <input
          type="checkbox"
          className="mt-0.5"
          data-testid="auto-approve-toggle"
          checked={on}
          onChange={(e) => saveOn(e.target.checked)}
        />
        <span>
          <span className="block text-[13px] text-ink">Enable Auto-approve mode</span>
          <span className="block text-[12px] text-muted">
            Adds an <em>Auto-approve</em> option to the mode picker. In that mode, your session
            model reviews each action that would normally need approval and clears the routine
            ones; anything doubtful still asks you. It can never allow something the rules
            block. One extra model call per check, billed to your usage.
          </span>
        </span>
      </label>
      <label className="flex items-start gap-3 py-2 pl-7">
        <input
          type="checkbox"
          className="mt-0.5"
          data-testid="auto-approve-shadow-toggle"
          checked={shadow}
          onChange={(e) => saveShadow(e.target.checked)}
        />
        <span>
          <span className="block text-[13px] text-ink">
            Shadow evaluation <span className="text-faint">(for measuring)</span>
          </span>
          <span className="block text-[12px] text-muted">
            On any mode, the reviewer records what it <em>would</em> have decided next to your
            own choice — without changing anything. Lets you see how it would behave before
            trusting it. Also costs one model call per approval.
          </span>
        </span>
      </label>
    </div>
  );
}

function SidebarCard() {
  const { t } = useTranslation();
  const [peek, setPeek] = useState<number | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setPeek(s.sessions_peek || 5))
      .catch(() => setPeek(5));
  }, []);

  const save = async (n: number) => {
    const clamped = Math.max(1, Math.min(n || 5, 50));
    setPeek(clamped);
    await setSessionsPeek(clamped);
  };

  if (peek === null) return null;
  return (
    <div className={CARD + " p-4 mb-4"}>
      <div className={FIELD_LABEL}>{t("settings.sidebar_card_title")}</div>
      <label className="flex items-center gap-3 mt-2.5">
        <span className="text-[13px] text-ink">{t("settings.sidebar_per_coworker")}</span>
        <input
          type="number"
          min={1}
          max={50}
          value={peek}
          className="w-16 px-2 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
          onChange={(e) => save(Number(e.target.value))}
        />
      </label>
      <div className={FIELD_HELP}>
        {t("settings.sidebar_card_help")}
      </div>
    </div>
  );
}

// -- Files (scratch location) — one card inside General (UX-021: a single option
// doesn't earn its own tab) -----------------------------------------------------
function FilesCard() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [scratchDraft, setScratchDraft] = useState("");
  const [scratchMsg, setScratchMsg] = useState<string | null>(null);
  const desktop = isTauri();

  const refresh = () =>
    getSettings()
      .then((s) => {
        setSettings(s);
        setScratchDraft((d) => d || s.scratch_base || "");
      })
      .catch(() => setSettings(null));
  useEffect(() => {
    refresh();
  }, []);

  const saveScratch = async () => {
    setScratchMsg(null);
    const res = await setScratchBase(scratchDraft.trim());
    if (res.ok) {
      setScratchMsg(t("settings.files_saved"));
      refresh();
    } else {
      setScratchMsg(res.error || t("settings.files_save_error"));
    }
  };
  const browseScratch = async () => {
    const picked = await pickFolder();
    if (picked) setScratchDraft(picked);
  };

  if (!settings) return null;

  return (
    <div className={CARD + " p-4 mb-4"}>
      <div className={FIELD_LABEL}>{t("settings.files_title")}</div>
        <div className="flex items-center gap-2 mt-2.5">
          <input
            className={INPUT}
            type="text"
            placeholder={t("settings.scratch_placeholder")}
            value={scratchDraft}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setScratchDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveScratch()}
          />
          {desktop && (
            <button className={BTN_BORDERED} onClick={browseScratch} title={t("settings.files_pick_folder")}>
              {t("settings.files_browse")}
            </button>
          )}
          <button className={BTN_ACCENT} onClick={saveScratch} disabled={!scratchDraft.trim()}>
            {t("settings.files_save")}
          </button>
        </div>
      <div className={FIELD_HELP}>
        {t("settings.files_help")}
      </div>
      {scratchMsg && <div className="text-[13px] text-muted mt-2.5">{scratchMsg}</div>}
    </div>
  );
}

function WorkspaceBackupSection() {
  const { t } = useTranslation();
  const [includeAttachments, setIncludeAttachments] = useState(true);
  const [includeTranscripts, setIncludeTranscripts] = useState(true);
  const [cloneWorkspace, setCloneWorkspace] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [snapshotting, setSnapshotting] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const handleExport = async () => {
    setExporting(true);
    setStatusMsg(null);
    try {
      const blob = await exportTeamWorkspace({
        include_attachments: includeAttachments,
        include_transcripts: includeTranscripts,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      link.download = `astra-workspace-backup-${timestamp}.owb`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      setStatusMsg({ type: "success", text: t("settings.backup_export_success") });
    } catch (err: any) {
      setStatusMsg({ type: "error", text: err.message || t("settings.backup_export_failed") });
    } finally {
      setExporting(false);
    }
  };

  const handleImport = async () => {
    if (!selectedFile) return;
    setImporting(true);
    setStatusMsg(null);
    try {
      const res = await importTeamWorkspace(selectedFile, { clone: cloneWorkspace });
      if (res.ok) {
        setStatusMsg({
          type: "success",
          text: t("settings.backup_import_success", {
            records: res.total_records || 0,
            attachments: res.attachments_imported || 0,
            workspace: res.workspace_id || "",
          }),
        });
        setSelectedFile(null);
      } else {
        setStatusMsg({ type: "error", text: res.error || t("settings.backup_import_failed") });
      }
    } catch (err: any) {
      setStatusMsg({ type: "error", text: err.message || t("settings.backup_import_failed") });
    } finally {
      setImporting(false);
    }
  };

  const handleSnapshot = async () => {
    setSnapshotting(true);
    setStatusMsg(null);
    try {
      const res = await createTeamDatabaseBackup();
      if (res.ok) {
        setStatusMsg({
          type: "success",
          text: t("settings.backup_snapshot_success", { path: res.path || "" }),
        });
      } else {
        setStatusMsg({ type: "error", text: res.error || t("settings.backup_snapshot_failed") });
      }
    } catch (err: any) {
      setStatusMsg({ type: "error", text: err.message || t("settings.backup_snapshot_failed") });
    } finally {
      setSnapshotting(false);
    }
  };

  return (
    <section>
      <PanelHead
        title={t("settings.backup_title")}
        sub={t("settings.backup_sub")}
      />

      {statusMsg && (
        <div className={`mb-4 rounded-xl p-3 text-[13px] ${statusMsg.type === "success" ? "bg-successSoft text-success border border-success/30" : "bg-dangerSoft text-danger border border-danger/30"}`}>
          {statusMsg.text}
        </div>
      )}

      {/* Export Workspace Card */}
      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.backup_export_title")}</div>
        <div className={FIELD_HELP}>{t("settings.backup_export_help")}</div>

        <div className="mt-3 space-y-2">
          <label className="flex items-center gap-2.5 text-[13px] text-ink cursor-pointer">
            <input
              type="checkbox"
              checked={includeAttachments}
              onChange={(e) => setIncludeAttachments(e.target.checked)}
              className="rounded border-line bg-paper"
            />
            <span>{t("settings.backup_include_attachments")}</span>
          </label>
          <label className="flex items-center gap-2.5 text-[13px] text-ink cursor-pointer">
            <input
              type="checkbox"
              checked={includeTranscripts}
              onChange={(e) => setIncludeTranscripts(e.target.checked)}
              className="rounded border-line bg-paper"
            />
            <span>{t("settings.backup_include_transcripts")}</span>
          </label>
        </div>

        <div className="mt-4">
          <button
            className={BTN_ACCENT}
            disabled={exporting}
            onClick={handleExport}
          >
            {exporting ? t("settings.backup_exporting") : t("settings.backup_export_btn")}
          </button>
        </div>
      </div>

      {/* Import / Restore Workspace Card */}
      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.backup_import_title")}</div>
        <div className={FIELD_HELP}>{t("settings.backup_import_help")}</div>

        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-3">
            <input
              type="file"
              accept=".owb,.tar.gz,application/gzip"
              onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
              className="text-[12px] text-muted file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border file:border-line file:bg-paper file:text-[12px] file:text-ink hover:file:border-lineStrong"
            />
          </div>

          <label className="flex items-center gap-2.5 text-[13px] text-ink cursor-pointer">
            <input
              type="checkbox"
              checked={cloneWorkspace}
              onChange={(e) => setCloneWorkspace(e.target.checked)}
              className="rounded border-line bg-paper"
            />
            <span>{t("settings.backup_clone_as_new")}</span>
          </label>

          <div>
            <button
              className={BTN_BORDERED}
              disabled={importing || !selectedFile}
              onClick={handleImport}
            >
              {importing ? t("settings.backup_importing") : t("settings.backup_import_btn")}
            </button>
          </div>
        </div>
      </div>

      {/* Snapshot SQLite Card */}
      <div className={CARD + " p-4 mb-4"}>
        <div className={FIELD_LABEL}>{t("settings.backup_snapshot_title")}</div>
        <div className={FIELD_HELP}>{t("settings.backup_snapshot_help")}</div>

        <div className="mt-3">
          <button
            className={BTN_BORDERED}
            disabled={snapshotting}
            onClick={handleSnapshot}
          >
            {snapshotting ? t("settings.backup_snapshotting") : t("settings.backup_snapshot_btn")}
          </button>
        </div>
      </div>

      <ClearHistoryCard />
    </section>
  );
}

export function ClearHistoryCard() {
  const { t } = useTranslation();
  const [clearing, setClearing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [resultMsg, setResultMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const handleClear = async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setClearing(true);
    setResultMsg(null);
    try {
      const res = await deleteAllSessions();
      if (res.ok) {
        setResultMsg({
          type: "success",
          text: t("settings.clear_history_success", { count: res.deleted }),
        });
        window.dispatchEvent(new CustomEvent("coworker:sessions-changed"));
      } else {
        setResultMsg({
          type: "error",
          text: res.error || t("settings.clear_history_error"),
        });
      }
    } catch (err: any) {
      setResultMsg({
        type: "error",
        text: err?.message || t("settings.clear_history_error"),
      });
    } finally {
      setClearing(false);
      setConfirming(false);
    }
  };

  return (
    <div className={CARD + " p-4 mb-4 border border-red-500/30 bg-red-500/5"} data-testid="settings-clear-history-card">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold text-red-500 uppercase tracking-wider mb-1">
            {t("settings.danger_zone")}
          </div>
          <div className={FIELD_LABEL}>{t("settings.clear_history_title")}</div>
          <div className={FIELD_HELP}>
            {t("settings.clear_history_desc")}
          </div>
          {confirming && !clearing && (
            <div className="mt-2 text-[12px] font-medium text-red-500 animate-pulse">
              ⚠️ {t("settings.clear_history_confirm_warn")}
            </div>
          )}
          {resultMsg && (
            <div className={`mt-2 text-[12px] font-medium ${resultMsg.type === "success" ? "text-emerald-500" : "text-red-500"}`}>
              {resultMsg.text}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {confirming && !clearing && (
            <button
              type="button"
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium border border-line bg-paper text-muted hover:text-ink transition-colors"
              onClick={() => setConfirming(false)}
            >
              {t("common.cancel", "Cancel")}
            </button>
          )}
          <button
            type="button"
            className={`px-3 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
              confirming
                ? "bg-red-600 hover:bg-red-700 text-white shadow-sm"
                : "border border-red-500/40 text-red-500 hover:bg-red-500/10"
            } disabled:opacity-50`}
            disabled={clearing}
            onClick={handleClear}
          >
            {clearing
              ? t("settings.clear_history_busy")
              : confirming
                ? t("settings.clear_history_confirm_btn")
                : t("settings.clear_history_btn")}
          </button>
        </div>
      </div>
    </div>
  );
}
