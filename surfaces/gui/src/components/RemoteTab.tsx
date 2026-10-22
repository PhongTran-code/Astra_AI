import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteSSHProfile,
  getRemoteStatus,
  listSSHProfiles,
  saveSSHProfile,
  setActiveSSHProfile,
  testSSHProfile,
  type RemoteStatus,
  type SSHProfile,
  type SSHProfileInput,
} from "../api";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[13px] font-medium text-ink";
const FIELD_HELP = "text-[12px] text-muted mt-1 leading-relaxed";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT =
  "text-[13px] px-3.5 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40 font-medium cursor-pointer transition hover:opacity-90";
const BTN_BORDERED =
  "text-[13px] px-3 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 text-ink cursor-pointer disabled:opacity-40 transition";

export function RemoteTab() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  const [profiles, setProfiles] = useState<SSHProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    id: string;
    ok: boolean;
    msg: string;
  } | null>(null);

  // Form modal state
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [sshCommandInput, setSshCommandInput] = useState("");
  const [formData, setFormData] = useState<SSHProfileInput>({
    name: "",
    host: "",
    port: 22,
    username: "root",
    password: "",
    key_path: "",
    private_key: "",
    remote_cwd: "/root",
    connect_timeout: 10,
    keepalive_interval: 30,
  });
  const [authType, setAuthType] = useState<"password" | "key">("password");
  const [formTesting, setFormTesting] = useState(false);
  const [formTestResult, setFormTestResult] = useState<{
    ok: boolean;
    msg: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const [st, profs] = await Promise.all([
        getRemoteStatus(),
        listSSHProfiles(),
      ]);
      setStatus(st);
      setProfiles(profs);
    } catch {
      /* fetch failed */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleSetActive = async (profileId: string) => {
    try {
      await setActiveSSHProfile(profileId);
      await refresh();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to switch runner");
    }
  };

  const handleDelete = async (profileId: string, name: string) => {
    if (
      !window.confirm(
        t("settings.remote_delete_confirm", {
          name,
          defaultValue: `Are you sure you want to delete VPS profile "${name}"?`,
        })
      )
    ) {
      return;
    }
    try {
      await deleteSSHProfile(profileId);
      await refresh();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete profile");
    }
  };

  const handleTestExisting = async (profile: SSHProfile) => {
    setTestingId(profile.id);
    setTestResult(null);
    try {
      const res = await testSSHProfile({
        id: profile.id,
        name: profile.name,
        host: profile.host,
        port: profile.port,
        username: profile.username,
        remote_cwd: profile.remote_cwd,
      });
      if (res.ok) {
        setTestResult({
          id: profile.id,
          ok: true,
          msg: `Connected (${res.latency_ms ?? 0}ms) — cwd: ${res.remote_cwd || "~"}`,
        });
      } else {
        setTestResult({
          id: profile.id,
          ok: false,
          msg: res.error || "Unable to connect to VPS",
        });
      }
    } catch (err: unknown) {
      setTestResult({
        id: profile.id,
        ok: false,
        msg: err instanceof Error ? err.message : "SSH test failed",
      });
    } finally {
      setTestingId(null);
    }
  };

  const openNewForm = () => {
    setEditingId(null);
    setSshCommandInput("");
    setFormData({
      name: "",
      host: "",
      port: 22,
      username: "root",
      password: "",
      key_path: "",
      private_key: "",
      remote_cwd: "/root",
      connect_timeout: 10,
      keepalive_interval: 30,
    });
    setAuthType("password");
    setFormTestResult(null);
    setFormError(null);
    setShowForm(true);
  };

  const openEditForm = (prof: SSHProfile) => {
    setEditingId(prof.id);
    setSshCommandInput("");
    setFormData({
      id: prof.id,
      name: prof.name,
      host: prof.host,
      port: prof.port,
      username: prof.username,
      password: "",
      key_path: prof.key_path || "",
      private_key: "",
      remote_cwd: prof.remote_cwd || "",
      connect_timeout: prof.connect_timeout || 10,
      keepalive_interval: prof.keepalive_interval || 30,
    });
    setAuthType(prof.has_key ? "key" : "password");
    setFormTestResult(null);
    setFormError(null);
    setShowForm(true);
  };

  // Quick SSH command auto-parser
  const handleParseSshCommand = (cmd: string) => {
    setSshCommandInput(cmd);
    const cleaned = cmd.trim();
    if (!cleaned) return;

    let port = 22;
    let user = "root";
    let host = "";
    let keyPath = "";

    const portMatch = cleaned.match(/-[pP]\s*(\d+)/);
    if (portMatch) {
      port = parseInt(portMatch[1], 10) || 22;
    }

    const keyMatch = cleaned.match(/-i\s+([^\s]+)/);
    if (keyMatch) {
      keyPath = keyMatch[1];
      setAuthType("key");
    }

    const tokens = cleaned
      .replace(/-[pP]\s*\d+/, "")
      .replace(/-i\s+[^\s]+/, "")
      .replace(/^ssh\s+/, "")
      .trim()
      .split(/\s+/);

    const targetToken = tokens[0] || "";
    if (targetToken.includes("@")) {
      const parts = targetToken.split("@");
      user = parts[0];
      host = parts[1];
    } else if (targetToken) {
      host = targetToken;
    }

    if (host) {
      setFormData((prev) => ({
        ...prev,
        host,
        port,
        username: user,
        key_path: keyPath || prev.key_path,
        name: prev.name || `VPS ${host}`,
      }));
    }
  };

  const handleFormTest = async () => {
    if (!formData.host || !formData.username) {
      setFormError("Host/IP and Username are required");
      return;
    }
    setFormTesting(true);
    setFormTestResult(null);
    setFormError(null);
    try {
      const res = await testSSHProfile(formData);
      if (res.ok) {
        setFormTestResult({
          ok: true,
          msg: `Connected! Latency: ${res.latency_ms ?? 0}ms`,
        });
      } else {
        setFormTestResult({
          ok: false,
          msg: res.error || "Connection failed. Please verify credentials.",
        });
      }
    } catch (err: unknown) {
      setFormTestResult({
        ok: false,
        msg: err instanceof Error ? err.message : "Error testing connection",
      });
    } finally {
      setFormTesting(false);
    }
  };

  const handleFormSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name.trim() || !formData.host.trim() || !formData.username.trim()) {
      setFormError("Profile Name, Host/IP, and Username are required");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const res = await saveSSHProfile(formData);
      if (!res.ok) {
        setFormError(res.error || "Failed to save VPS profile");
        return;
      }
      setShowForm(false);
      await refresh();
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "Error saving profile");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="text-[13px] text-muted py-6">{t("settings.trust_loading", "Loading…")}</div>;
  }

  const activeId = status?.active_ssh_profile?.id || "";

  return (
    <section className="space-y-6">
      <PanelHead
        title={t("settings.remote_title", "Remote VPS & SSH Execution")}
        sub={t(
          "settings.remote_sub",
          "Manage where agent tools and terminal commands run, and configure remote server execution."
        )}
      />

      {/* 🌟 TOP-LEVEL: Global Active Target Runner Selector */}
      <div className={CARD + " p-5 space-y-3.5 bg-accent/5 border-accent/30"}>
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <div className="text-[14px] font-semibold text-ink flex items-center gap-2">
              <Icon name="sliders" size={16} className="text-accent" />
              <span>
                {t(
                  "settings.remote_active_runner_title",
                  "Current Execution Environment (Active Runner)"
                )}
              </span>
            </div>
            <p className="text-[12px] text-muted">
              {t(
                "settings.remote_active_runner_desc",
                "Select where Astra executes shell commands, runs code, and reads/writes files during conversations."
              )}
            </p>
          </div>
          <span className="text-[11px] px-2.5 py-1 rounded-full bg-accent text-white font-medium shadow-xs">
            {t("settings.remote_active_badge", {
              name: status?.active_ssh_profile
                ? status.active_ssh_profile.name
                : t("settings.remote_local_machine", "Local Machine"),
              defaultValue: `Active: ${
                status?.active_ssh_profile
                  ? status.active_ssh_profile.name
                  : "Local Machine"
              }`,
            })}
          </span>
        </div>

        {/* 1-Click Interactive Selector Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5 pt-1">
          {/* Local Machine Option */}
          <div
            onClick={() => handleSetActive("")}
            className={
              "p-3 rounded-xl border transition cursor-pointer flex items-center justify-between " +
              (!activeId
                ? "bg-accent/15 border-accent text-accent shadow-xs"
                : "bg-paper border-line text-ink hover:border-lineStrong")
            }
          >
            <div className="flex items-center gap-2.5 min-w-0">
              <div
                className={
                  "p-1.5 rounded-lg " +
                  (!activeId ? "bg-accent text-white" : "bg-panel text-muted")
                }
              >
                <Icon name="sliders" size={15} />
              </div>
              <div className="min-w-0">
                <div className="text-[13px] font-semibold truncate">
                  {t("settings.remote_local_machine", "Local Machine")}
                </div>
                <div className="text-[11px] text-muted truncate">
                  {t("settings.remote_default_env", "Default environment")}
                </div>
              </div>
            </div>
            <div
              className={
                "w-4 h-4 rounded-full border flex items-center justify-center shrink-0 " +
                (!activeId
                  ? "border-accent bg-accent"
                  : "border-lineStrong")
              }
            >
              {!activeId && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
            </div>
          </div>

          {/* Configured Profiles Options */}
          {profiles.map((p) => {
            const isAct = activeId === p.id;
            return (
              <div
                key={p.id}
                onClick={() => handleSetActive(p.id)}
                className={
                  "p-3 rounded-xl border transition cursor-pointer flex items-center justify-between " +
                  (isAct
                    ? "bg-accent/15 border-accent text-accent shadow-xs"
                    : "bg-paper border-line text-ink hover:border-lineStrong")
                }
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <div
                    className={
                      "p-1.5 rounded-lg " +
                      (isAct ? "bg-accent text-white" : "bg-panel text-muted")
                    }
                  >
                    <Icon name="server" size={15} />
                  </div>
                  <div className="min-w-0">
                    <div className="text-[13px] font-semibold truncate">{p.name}</div>
                    <div className="text-[11px] text-muted font-mono truncate">
                      {p.username}@{p.host}
                    </div>
                  </div>
                </div>
                <div
                  className={
                    "w-4 h-4 rounded-full border flex items-center justify-center shrink-0 " +
                    (isAct ? "border-accent bg-accent" : "border-lineStrong")
                  }
                >
                  {isAct && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Option 1: SSH Remote Runner Management */}
      <div className={CARD + " p-5 space-y-4"}>
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-[15px] text-ink">
                {t(
                  "settings.remote_opt1_title",
                  "Option 1: Offload Commands & Heavy Tasks to VPS (SSH Runner)"
                )}
              </span>
            </div>
            <p className={FIELD_HELP}>
              {t(
                "settings.remote_opt1_desc",
                "Astra remains on your local machine for full UI convenience, while all terminal commands, code compilation, and file operations are securely executed on your remote VPS via encrypted SSH."
              )}
            </p>
          </div>
          <button
            type="button"
            className={BTN_ACCENT + " flex items-center gap-1.5 shrink-0"}
            onClick={openNewForm}
          >
            <Icon name="plus" size={14} />{" "}
            {t("settings.remote_add_vps_btn", "Add VPS Profile")}
          </button>
        </div>

        {/* Profile List */}
        <div className="space-y-3 pt-1">
          <div className="flex items-center justify-between">
            <span className="text-[12px] font-semibold text-muted uppercase tracking-wider">
              {t("settings.remote_saved_profiles_title", {
                count: profiles.length,
                defaultValue: `Saved VPS Profiles (${profiles.length})`,
              })}
            </span>
          </div>

          {profiles.length === 0 ? (
            <div className="p-8 text-center border border-dashed border-line rounded-xl bg-paper/30 space-y-3">
              <div className="w-10 h-10 rounded-full bg-panel border border-line flex items-center justify-center mx-auto text-muted">
                <Icon name="server" size={20} />
              </div>
              <div className="space-y-1">
                <div className="text-[14px] font-medium text-ink">
                  {t("settings.remote_empty_title", "No VPS profiles configured yet")}
                </div>
                <div className="text-[12px] text-muted max-w-md mx-auto">
                  {t(
                    "settings.remote_empty_desc",
                    "Add your VPS to let Astra build projects, run benchmarks, or train models directly in the cloud."
                  )}
                </div>
              </div>
              <button
                type="button"
                className={BTN_ACCENT + " inline-flex items-center gap-1.5 text-[12px]"}
                onClick={openNewForm}
              >
                <Icon name="plus" size={13} />{" "}
                {t("settings.remote_add_first_btn", "Add First VPS Profile")}
              </button>
            </div>
          ) : (
            <div className="divide-y divide-line rounded-xl border border-line bg-paper overflow-hidden">
              {profiles.map((prof) => {
                const isAct = activeId === prof.id;
                const isTesting = testingId === prof.id;
                const result = testResult?.id === prof.id ? testResult : null;

                return (
                  <div key={prof.id} className="p-3.5 flex flex-col gap-2">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="p-2 rounded-lg bg-panel border border-line text-ink">
                          <Icon name="server" size={16} />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-semibold text-ink truncate">
                              {prof.name}
                            </span>
                            {isAct && (
                              <span className="text-[10px] bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold px-2 py-0.5 rounded-full">
                                {t("settings.remote_active_runner_badge", "ACTIVE RUNNER")}
                              </span>
                            )}
                          </div>
                          <div className="text-[12px] text-muted font-mono flex items-center gap-2 mt-0.5">
                            <span>
                              {prof.username}@{prof.host}:{prof.port}
                            </span>
                            <span>•</span>
                            <span>cwd: {prof.remote_cwd || "~"}</span>
                            <span>•</span>
                            <span>
                              {prof.has_key
                                ? t("settings.remote_auth_key", "SSH Private Key")
                                : t("settings.remote_auth_password", "Password")}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          className={BTN_BORDERED + " text-[12px] flex items-center gap-1"}
                          onClick={() => handleTestExisting(prof)}
                          disabled={isTesting}
                        >
                          <Icon
                            name="refresh"
                            size={12}
                            className={isTesting ? "animate-spin" : ""}
                          />
                          {isTesting
                            ? t("settings.remote_testing_btn", "Testing…")
                            : t("settings.remote_test_btn", "Test")}
                        </button>

                        {!isAct && (
                          <button
                            type="button"
                            className={
                              BTN_BORDERED +
                              " text-[12px] text-accent border-accent/30 hover:bg-accent/5"
                            }
                            onClick={() => handleSetActive(prof.id)}
                          >
                            {t("settings.remote_set_active_btn", "Set Active")}
                          </button>
                        )}

                        <button
                          type="button"
                          className={BTN_BORDERED + " text-[12px] py-1.5 px-2"}
                          onClick={() => openEditForm(prof)}
                          title={t("settings.remote_edit_tooltip", "Edit Profile")}
                        >
                          <Icon name="pencil" size={13} />
                        </button>

                        <button
                          type="button"
                          className={
                            BTN_BORDERED +
                            " text-[12px] py-1.5 px-2 text-red-500 hover:bg-red-500/10"
                          }
                          onClick={() => handleDelete(prof.id, prof.name)}
                          title={t("settings.remote_delete_tooltip", "Delete Profile")}
                        >
                          <Icon name="trash" size={13} />
                        </button>
                      </div>
                    </div>

                    {result && (
                      <div
                        className={
                          "text-[12px] px-3 py-1.5 rounded-lg flex items-center gap-2 " +
                          (result.ok
                            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                            : "bg-red-500/10 text-red-600 dark:text-red-400")
                        }
                      >
                        <Icon name={result.ok ? "check" : "warning"} size={13} />
                        <span>{result.msg}</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Option 2: Remote Server Daemon & Web UI */}
      <div className={CARD + " p-5 space-y-4"}>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-[15px] text-ink">
              {t(
                "settings.remote_opt2_title",
                "Option 2: Host Astra 24/7 on VPS (Web UI & Server Daemon)"
              )}
            </span>
            {status?.is_remote_daemon ? (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-medium">
                {t("settings.remote_daemon_active_badge", "Daemon Mode Active")}
              </span>
            ) : (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-panel text-muted font-medium border border-line">
                {t("settings.remote_local_server_badge", "Local App Server")}
              </span>
            )}
          </div>
          <p className={FIELD_HELP}>
            {t(
              "settings.remote_opt2_desc",
              "Deploy Astra directly onto your Linux VPS to run continuously without keeping your computer on. Access the complete Web UI from any browser or let agents run automations 24/7."
            )}
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
          <div className="p-3.5 rounded-xl bg-paper border border-line space-y-1.5">
            <div className="text-[11px] font-semibold text-muted uppercase">
              {t("settings.remote_server_status_title", "Current Server Status")}
            </div>
            <div className="text-[13px] text-ink flex items-center gap-2">
              <span
                className={
                  "w-2 h-2 rounded-full " +
                  (status?.is_remote_daemon ? "bg-emerald-500" : "bg-blue-500")
                }
              />
              <span className="font-medium">
                {status?.is_remote_daemon
                  ? t("settings.remote_daemon_active_badge", "Remote Daemon Active")
                  : t("settings.remote_local_server_badge", "Local App Server")}
              </span>
            </div>
            <div className="text-[12px] text-muted font-mono">
              {t("settings.remote_listening_on", {
                address: `${status?.host || "127.0.0.1"}:${status?.port || 8765}`,
                defaultValue: `Listening on: ${status?.host || "127.0.0.1"}:${
                  status?.port || 8765
                }`,
              })}
            </div>
          </div>

          <div className="p-3.5 rounded-xl bg-paper border border-line space-y-1.5">
            <div className="text-[11px] font-semibold text-muted uppercase">
              {t("settings.remote_security_title", "Security & Token Authentication")}
            </div>
            <div className="text-[13px] text-ink flex items-center gap-2">
              <Icon
                name="shield"
                size={14}
                className={
                  status?.token_auth_enabled ? "text-emerald-500" : "text-amber-500"
                }
              />
              <span className="font-medium">
                {status?.token_auth_enabled
                  ? t("settings.remote_token_protected", "Protected by X-Astra-Token")
                  : t("settings.remote_token_none", "Local Access (No Token)")}
              </span>
            </div>
            <div className="text-[12px] text-muted">
              {status?.token_auth_enabled
                ? t(
                    "settings.remote_token_protected_desc",
                    "API and WebSockets require secret token authentication"
                  )
                : t(
                    "settings.remote_token_none_desc",
                    "Set ASTRA_TOKEN before exposing the server to a public IP address"
                  )}
            </div>
          </div>
        </div>

        {/* Quick Launch Tutorial */}
        <div className="p-4 rounded-xl bg-paper/60 border border-line space-y-3">
          <div className="text-[13px] font-semibold text-ink flex items-center gap-1.5">
            <Icon name="code" size={14} />
            <span>
              {t(
                "settings.remote_steps_title",
                "3 steps to start Astra Server Daemon on Linux VPS:"
              )}
            </span>
          </div>

          <div className="space-y-2 text-[12px] text-ink">
            <div className="flex items-start gap-2.5">
              <span className="w-5 h-5 rounded-full bg-accent/10 text-accent flex items-center justify-center font-bold text-[11px] shrink-0">
                1
              </span>
              <div className="flex-1 min-w-0">
                <div className="font-medium">
                  {t("settings.remote_step1_title", "Set security token (Recommended):")}
                </div>
                <pre className="mt-1 p-2 rounded-lg bg-panel font-mono text-[11px] overflow-x-auto border border-line">
                  export ASTRA_TOKEN="your_secure_secret_token_123"{"\n"}
                  export ASTRA_REMOTE_DAEMON="1"
                </pre>
              </div>
            </div>

            <div className="flex items-start gap-2.5">
              <span className="w-5 h-5 rounded-full bg-accent/10 text-accent flex items-center justify-center font-bold text-[11px] shrink-0">
                2
              </span>
              <div className="flex-1 min-w-0">
                <div className="font-medium">
                  {t("settings.remote_step2_title", "Start the background server:")}
                </div>
                <pre className="mt-1 p-2 rounded-lg bg-panel font-mono text-[11px] overflow-x-auto border border-line">
                  coworker serve --host 0.0.0.0 --port 8765
                </pre>
              </div>
            </div>

            <div className="flex items-start gap-2.5">
              <span className="w-5 h-5 rounded-full bg-accent/10 text-accent flex items-center justify-center font-bold text-[11px] shrink-0">
                3
              </span>
              <div className="flex-1 min-w-0">
                <div className="font-medium">
                  {t("settings.remote_step3_title", "Open in Browser:")}
                </div>
                <p className="mt-0.5 text-muted">
                  {t(
                    "settings.remote_step3_desc",
                    "Visit http://<VPS_IP>:8765/ in your browser. The complete Astra Web UI loads automatically!"
                  )}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Add / Edit Profile Modal with Quick Import */}
      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-xl bg-panel border border-line rounded-2xl shadow-2xl p-6 space-y-4 max-h-[90vh] overflow-y-auto hairline-scroll">
            <div className="flex items-center justify-between border-b border-line pb-3">
              <div className="flex items-center gap-2">
                <Icon name="server" size={18} className="text-accent" />
                <span className="text-[15px] font-semibold text-ink">
                  {editingId
                    ? t("settings.remote_modal_edit_title", "Edit VPS Profile")
                    : t("settings.remote_modal_add_title", "Add New VPS Profile")}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="text-muted hover:text-ink cursor-pointer p-1"
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            {/* Quick SSH command parse box */}
            <div className="p-3.5 rounded-xl bg-paper border border-line space-y-2">
              <label className="text-[12px] font-medium text-ink flex items-center gap-1.5">
                <Icon name="sparkle" size={13} className="text-accent" />
                <span>
                  {t(
                    "settings.remote_quick_import_label",
                    "Quick Import via SSH Command (Optional):"
                  )}
                </span>
              </label>
              <input
                type="text"
                placeholder={t(
                  "settings.remote_quick_import_placeholder",
                  "e.g. ssh root@123.45.67.89 -p 22 or ssh -i ~/.ssh/id_rsa ubuntu@vps.com"
                )}
                className={INPUT + " w-full font-mono text-[12px]"}
                value={sshCommandInput}
                onChange={(e) => handleParseSshCommand(e.target.value)}
              />
              <div className="text-[11px] text-muted">
                {t(
                  "settings.remote_quick_import_help",
                  "Paste your SSH command here to automatically fill in the Host, Port, and Username fields below."
                )}
              </div>
            </div>

            <form onSubmit={handleFormSave} className="space-y-4">
              {formError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-[12px] flex items-center gap-2">
                  <Icon name="warning" size={14} />
                  <span>{formError}</span>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className={FIELD_LABEL}>
                    {t("settings.remote_field_name", "Profile Name *")}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder={t(
                      "settings.remote_field_name_placeholder",
                      "e.g. Production Ubuntu VPS, GPU Server..."
                    )}
                    className={INPUT + " w-full mt-1"}
                    value={formData.name}
                    onChange={(e) =>
                      setFormData({ ...formData, name: e.target.value })
                    }
                  />
                </div>

                <div>
                  <label className={FIELD_LABEL}>
                    {t("settings.remote_field_host", "Host / IP Address *")}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="123.45.67.89 or domain.com"
                    className={INPUT + " w-full mt-1 font-mono text-[12px]"}
                    value={formData.host}
                    onChange={(e) =>
                      setFormData({ ...formData, host: e.target.value })
                    }
                  />
                </div>

                <div>
                  <label className={FIELD_LABEL}>
                    {t("settings.remote_field_port", "SSH Port")}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    placeholder="22"
                    className={INPUT + " w-full mt-1 font-mono text-[12px]"}
                    value={formData.port ?? 22}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        port: Number(e.target.value) || 22,
                      })
                    }
                  />
                </div>

                <div>
                  <label className={FIELD_LABEL}>
                    {t("settings.remote_field_user", "Username *")}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="root, ubuntu, debian..."
                    className={INPUT + " w-full mt-1 font-mono text-[12px]"}
                    value={formData.username}
                    onChange={(e) =>
                      setFormData({ ...formData, username: e.target.value })
                    }
                  />
                </div>

                <div>
                  <label className={FIELD_LABEL}>
                    {t("settings.remote_field_cwd", "Remote Workspace CWD")}
                  </label>
                  <input
                    type="text"
                    placeholder="/root or /home/ubuntu"
                    className={INPUT + " w-full mt-1 font-mono text-[12px]"}
                    value={formData.remote_cwd || ""}
                    onChange={(e) =>
                      setFormData({ ...formData, remote_cwd: e.target.value })
                    }
                  />
                </div>
              </div>

              {/* Auth Type Switcher */}
              <div className="space-y-2 pt-1">
                <label className={FIELD_LABEL}>
                  {t("settings.remote_auth_method", "Authentication Method")}
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setAuthType("password")}
                    className={
                      "flex-1 py-2 text-[12px] font-medium rounded-lg border transition cursor-pointer " +
                      (authType === "password"
                        ? "bg-accent/10 border-accent text-accent"
                        : "bg-paper border-line text-muted hover:text-ink")
                    }
                  >
                    {t("settings.remote_auth_password", "Password")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setAuthType("key")}
                    className={
                      "flex-1 py-2 text-[12px] font-medium rounded-lg border transition cursor-pointer " +
                      (authType === "key"
                        ? "bg-accent/10 border-accent text-accent"
                        : "bg-paper border-line text-muted hover:text-ink")
                    }
                  >
                    {t("settings.remote_auth_key", "SSH Private Key")}
                  </button>
                </div>
              </div>

              {authType === "password" ? (
                <div>
                  <label className={FIELD_LABEL}>
                    {t("settings.remote_password_label", "SSH Password")}
                  </label>
                  <input
                    type="password"
                    placeholder={
                      editingId
                        ? t(
                            "settings.remote_password_placeholder_edit",
                            "Leave blank to keep existing password"
                          )
                        : t(
                            "settings.remote_password_placeholder_new",
                            "Enter SSH password"
                          )
                    }
                    className={INPUT + " w-full mt-1"}
                    value={formData.password || ""}
                    onChange={(e) =>
                      setFormData({ ...formData, password: e.target.value })
                    }
                  />
                </div>
              ) : (
                <div className="space-y-3">
                  <div>
                    <label className={FIELD_LABEL}>
                      {t(
                        "settings.remote_key_path_label",
                        "Private Key File Path"
                      )}
                    </label>
                    <input
                      type="text"
                      placeholder="~/.ssh/id_rsa or C:\Users\user\.ssh\id_rsa"
                      className={INPUT + " w-full mt-1 font-mono text-[12px]"}
                      value={formData.key_path || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, key_path: e.target.value })
                      }
                    />
                  </div>

                  <div>
                    <label className={FIELD_LABEL}>
                      {t(
                        "settings.remote_key_content_label",
                        "Or Paste Private Key (PEM format)"
                      )}
                    </label>
                    <textarea
                      rows={3}
                      placeholder="-----BEGIN OPENSSH PRIVATE KEY-----..."
                      className={INPUT + " w-full mt-1 font-mono text-[11px]"}
                      value={formData.private_key || ""}
                      onChange={(e) =>
                        setFormData({
                          ...formData,
                          private_key: e.target.value,
                        })
                      }
                    />
                  </div>

                  <div>
                    <label className={FIELD_LABEL}>
                      {t(
                        "settings.remote_passphrase_label",
                        "Key Passphrase (if encrypted)"
                      )}
                    </label>
                    <input
                      type="password"
                      placeholder={t(
                        "settings.remote_passphrase_placeholder",
                        "Optional passphrase"
                      )}
                      className={INPUT + " w-full mt-1"}
                      value={formData.key_passphrase || ""}
                      onChange={(e) =>
                        setFormData({
                          ...formData,
                          key_passphrase: e.target.value,
                        })
                      }
                    />
                  </div>
                </div>
              )}

              {/* Form test result */}
              {formTestResult && (
                <div
                  className={
                    "text-[12px] p-2.5 rounded-lg flex items-center gap-2 " +
                    (formTestResult.ok
                      ? "bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                      : "bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400")
                  }
                >
                  <Icon name={formTestResult.ok ? "check" : "warning"} size={14} />
                  <span>{formTestResult.msg}</span>
                </div>
              )}

              <div className="flex items-center justify-between pt-3 border-t border-line">
                <button
                  type="button"
                  className={
                    BTN_BORDERED + " flex items-center gap-1.5 text-[12px] py-2"
                  }
                  onClick={handleFormTest}
                  disabled={formTesting || saving}
                >
                  <Icon
                    name="refresh"
                    size={13}
                    className={formTesting ? "animate-spin" : ""}
                  />
                  {formTesting
                    ? t("settings.remote_test_conn_testing", "Testing Connection…")
                    : t("settings.remote_test_conn_btn", "Test Connection")}
                </button>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className={BTN_BORDERED + " text-[12px] py-2"}
                    onClick={() => setShowForm(false)}
                    disabled={saving}
                  >
                    {t("settings.remote_cancel_btn", "Cancel")}
                  </button>
                  <button
                    type="submit"
                    className={BTN_ACCENT + " text-[12px] py-2"}
                    disabled={saving}
                  >
                    {saving
                      ? t("settings.remote_saving_btn", "Saving…")
                      : t("settings.remote_save_btn", "Save Profile")}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
