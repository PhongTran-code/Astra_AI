import { useMemo, useRef, useState } from "react";
import { useEffect } from "react";
import { Trans, useTranslation } from "react-i18next";
import {
  createSkill,
  deleteSkill,
  listSkills,
  revealSkill,
  stageSkillUpload,
  confirmSkillUpload,
  updateSkill,
  type SkillRow,
  type SkillUploadPreview,
} from "../api";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";
import { SkillMeshAvatar } from "./SkillMeshAvatar";
import { Toggle } from "./Toggle";

// Settings ▸ Skills (SKILLS-SPEC §5/§6) — the management home: the LIST is the page; every
// add-surface appears only when summoned from the single "Add skill" menu (the three doors:
// write form / import / start-a-conversation). Everything a user creates here is GLOBAL —
// "skills are things your worker knows everywhere". Creation-by-AI is a CONVERSATION (the
// menu's third door starts one; the worker proposes via save_skill) — there is no
// in-Settings drafting and no description box: the composer is where you describe it.
// Persona-bundled skills arrive with personas (§10), managed on the persona page, not here.

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[13px] font-medium text-ink";
const INPUT =
  "w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT =
  "inline-flex items-center gap-1.5 text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "inline-flex items-center gap-1.5 text-[13px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const BADGE =
  "text-[11px] px-2 py-0.5 rounded-full border border-line bg-paper text-muted shrink-0";
const CONNECTED_BADGE =
  "text-[10px] font-medium px-1.5 py-0.5 rounded-md border border-okLine bg-okSoft text-ok shrink-0";
type Editor = {
  mode: "new" | "edit";
  name: string;
  description: string;
  instructions: string;
};

const emptyEditor = (): Editor => ({
  mode: "new",
  name: "",
  description: "",
  instructions: "",
});

async function fileToB64(file: File): Promise<string> {
  // FileReader fallback: File.arrayBuffer is missing in some webviews (and jsdom).
  const buf =
    typeof file.arrayBuffer === "function"
      ? await file.arrayBuffer()
      : await new Promise<ArrayBuffer>((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result as ArrayBuffer);
          r.onerror = () => reject(r.error);
          r.readAsArrayBuffer(file);
        });
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

export function SkillsTab({
  onCreateSkill,
}: {
  // The doorway (SKILLS-SPEC §5.2): starts a new conversation with the description
  // prefilled in the composer — the worker builds the skill and proposes it via save_skill.
  onCreateSkill?: (description: string) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SkillRow[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [upload, setUpload] = useState<SkillUploadPreview | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "ready">("all");
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [detail, setDetail] = useState<SkillRow | null>(null);
  const [error, setError] = useState("");
  // The state-change callout (SKILLS-SPEC §4.1 #2): name-first so the user knows WHICH
  // skill, and visually distinct so it can't be skimmed past (tester ask 2026-07-27).
  const [notice, setNotice] = useState<{ name: string; text: string; tone: "ok" | "warn" } | null>(
    null,
  );
  const fileInput = useRef<HTMLInputElement>(null);

  // Confirmation copy (SKILLS-SPEC §4.1 #2): name-first, outcome + remedy only, in words a
  // person already owns — now / everywhere / off / start a new one. Never mechanism ("the
  // model will be told…") or engineering timing ("from the next message") — owner-driver
  // review rounds, 2026-07-27. The engine countermands disabled-but-loaded skills silently;
  // the copy promises only the guaranteed part.
  const CONFIRMATION = t("skills.confirmation");
  const OFF_NOTE = t("skills.off_note");
  const DELETE_NOTE = t("skills.delete_note");

  const refresh = async () => {
    setRefreshing(true);
    try {
      setRows(await listSkills());
    } finally {
      setRefreshing(false);
    }
  };
  useEffect(() => {
    refresh();
  }, []);

  const readyCount = rows.filter((row) => row.enabled).length;
  const visibleRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (filter === "ready" && !row.enabled) return false;
      if (!query) return true;
      return `${row.name} ${row.description}`.toLowerCase().includes(query);
    });
  }, [filter, rows, search]);

  const fail = (res: { ok?: boolean; error?: string }) => {
    setNotice(null);
    if (res.ok === false) {
      setError(res.error || t("skills.error_generic"));
      return true;
    }
    setError("");
    return false;
  };

  const save = async () => {
    if (!editor) return;
    const res =
      editor.mode === "new"
        ? await createSkill({
            name: editor.name.trim(),
            description: editor.description.trim(),
            instructions: editor.instructions,
          })
        : await updateSkill(editor.name, {
            description: editor.description.trim(),
            instructions: editor.instructions,
          });
    if (fail(res)) return;
    setEditor(null);
    if (editor.mode === "new")
      setNotice({ name: editor.name.trim(), text: CONFIRMATION, tone: "ok" });
    refresh();
  };

  const onPickFile = async (file: File | undefined) => {
    if (!file) return;
    const res = await stageSkillUpload(await fileToB64(file), file.name);
    if (fail(res)) return;
    setUpload(res);
  };

  const confirmUpload = async () => {
    if (!upload?.token) return;
    const res = await confirmSkillUpload(upload.token);
    if (fail(res)) return;
    setUpload(null);
    setNotice({ name: upload.name || t("skills.fallback_name"), text: CONFIRMATION, tone: "ok" });
    refresh();
  };

  const remove = async (row: SkillRow) => {
    if (armedDelete !== row.name) {
      setArmedDelete(row.name);
      return;
    }
    setArmedDelete(null);
    const res = await deleteSkill(row.name);
    if (fail(res)) return;
    setNotice({ name: row.name, text: DELETE_NOTE, tone: "warn" });
    refresh();
  };

  const edit = (row: SkillRow) => {
    setDetail(null);
    setEditor({
      mode: "edit",
      name: row.name,
      description: row.description,
      instructions: row.instructions,
    });
  };

  const toggle = (row: SkillRow, enabled: boolean) => {
    void updateSkill(row.name, { enabled }).then((res) => {
      if (!fail(res)) {
        setNotice({
          name: row.name,
          text: enabled ? CONFIRMATION : OFF_NOTE,
          tone: enabled ? "ok" : "warn",
        });
      }
      void refresh();
    });
  };

  const renderActions = (row: SkillRow) => (
    <div className="flex items-center gap-2 flex-wrap">
      <button
        className="text-[12px] font-medium text-ink hover:text-accent transition-colors"
        onClick={() => setDetail(row)}
        aria-label={t("skills.view_details_aria", { name: row.name })}
      >
        {t("skills.view_details")}
      </button>
      {row.files ? (
        <button
          className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded-md border border-line bg-paper text-muted hover:text-ink hover:border-lineStrong shrink-0"
          title={t("skills.show_folder")}
          onClick={() => void revealSkill(row.name)}
        >
          <Icon name="folder" size={11} /> {t("skills.file_count", { count: row.files })}
        </button>
      ) : null}
      {row.source === "detected" ? null : (
        <>
          <button
            className="sr-only"
            title={t("skills.edit_tip")}
            onClick={() => edit(row)}
          >
            <Icon name="pencil" size={13} />
          </button>
          <button
            className="sr-only"
            aria-label={t("skills.delete_aria", { name: row.name })}
            onClick={() => void remove(row)}
            onBlur={() => setArmedDelete(null)}
          >
            {armedDelete === row.name ? t("skills.confirm_delete") : <Icon name="trash" size={13} />}
          </button>
        </>
      )}
      <div className="inline-flex items-center ml-auto">
        <Toggle
          checked={row.enabled}
          onChange={(enabled) => toggle(row, enabled)}
          tone="success"
          ariaLabel={t("skills.enabled_aria", { name: row.name })}
          title={t("skills.enabled_aria", { name: row.name })}
        />
      </div>
    </div>
  );

  return (
    <section>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 className="text-[20px] font-semibold">{t("skills.library_title")}</h2>
          <p className="text-[13px] text-muted mt-1 leading-relaxed max-w-xl">{t("skills.library_subtitle")}</p>
        </div>
        {/* One add-action, three doors behind it (SKILLS-SPEC §5): the list is the page. */}
        <div className="relative shrink-0 flex items-center gap-2">
          <button
            className={BTN_BORDERED}
            aria-label={t("skills.refresh")}
            title={t("skills.refresh")}
            disabled={refreshing}
            onClick={() => void refresh()}
          >
            <Icon name="refresh" size={13} />
          </button>
          <button
            className="text-[13px] px-3 py-2 rounded-lg bg-ink text-paper shrink-0 disabled:opacity-40"
            aria-haspopup="menu"
            aria-expanded={addOpen}
            onClick={() => setAddOpen((v) => !v)}
          >
            <span className="inline-flex items-center gap-1.5">
              <Icon name="plus" size={13} /> {t("skills.add_skill")}
            </span>
          </button>
          {addOpen ? (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setAddOpen(false)} />
              <div
                role="menu"
                className="absolute right-0 top-full mt-1.5 w-80 rounded-xl2 border border-line bg-panel shadow-xl z-20 p-1.5"
                onKeyDown={(e) => e.key === "Escape" && setAddOpen(false)}
              >
                <button
                  role="menuitem"
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-paper"
                  onClick={() => {
                    setAddOpen(false);
                    setEditor(emptyEditor());
                  }}
                >
                  <div className="text-[13px] font-medium">{t("skills.door_write")}</div>
                  <div className="text-[12px] text-muted">{t("skills.door_write_sub")}</div>
                </button>
                <button
                  role="menuitem"
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-paper"
                  onClick={() => {
                    setAddOpen(false);
                    fileInput.current?.click();
                  }}
                >
                  <div className="text-[13px] font-medium">{t("skills.door_import")}</div>
                  <div className="text-[12px] text-muted">{t("skills.door_import_sub")}</div>
                </button>
                <button
                  role="menuitem"
                  className="w-full text-left px-3 py-2 rounded-lg hover:bg-paper disabled:opacity-40"
                  disabled={!onCreateSkill}
                  onClick={() => {
                    setAddOpen(false);
                    onCreateSkill?.("");
                  }}
                >
                  <div className="text-[13px] font-medium">{t("skills.door_ai")}</div>
                  <div className="text-[12px] text-muted">{t("skills.door_ai_sub")}</div>
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>
      <input
        ref={fileInput}
        type="file"
        accept=".zip,.md"
        className="hidden"
        aria-label={t("skills.upload_aria")}
        onChange={(e) => {
          onPickFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      {error ? (
        <div className="text-[13px] text-red-500 mb-3" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div
          role="status"
          className={
            "mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[13px] " +
            (notice.tone === "ok"
              ? "bg-tealSoft/70 text-tealInk border-tealInk/20"
              : "bg-warnSoft/70 text-warnInk border-warnInk/20")
          }
        >
          <span className="min-w-0">
            <b>{notice.name}</b> {notice.text}
          </span>
          <button
            className="ml-auto shrink-0 opacity-60 hover:opacity-100"
            aria-label={t("common.dismiss")}
            onClick={() => setNotice(null)}
          >
            ✕
          </button>
        </div>
      ) : null}

      {upload ? (
        <div className={`${CARD} p-4 mb-4`}>
          <div className="text-[13px] font-medium mb-1">{t("skills.review_title")}</div>
          <p className="text-[13px] text-muted mb-3">{t("skills.review_sub")}</p>
          <div className="text-[13px] mb-1">
            <span className="font-medium">{upload.name}</span>
            <span className="text-muted"> — {upload.description || t("skills.no_description")}</span>
          </div>
          <pre className="text-[12px] bg-paper border border-line rounded-lg p-3 whitespace-pre-wrap max-h-64 overflow-y-auto mb-2">
            {upload.instructions}
          </pre>
          {upload.files?.length ? (
            <div className="text-[12px] text-muted mb-2">
              {t("skills.bundled_files", { files: upload.files.join(", ") })}
            </div>
          ) : null}
          <div className="flex gap-2 mt-3">
            <button className={BTN_ACCENT} onClick={confirmUpload}>
              {t("skills.install_skill")}
            </button>
            <button className={BTN_BORDERED} onClick={() => setUpload(null)}>
              {t("folder_gate.cancel")}
            </button>
          </div>
        </div>
      ) : null}

      {editor ? (
        <div className={`${CARD} p-4 mb-4`}>
          <div className="text-[13px] font-medium mb-3">
            {editor.mode === "new"
              ? t("skills.editor_new")
              : t("skills.editor_edit", { name: editor.name })}
          </div>
          <label className={FIELD_LABEL} htmlFor="skill-name">
            {t("skills.name_label")}
          </label>
          <input
            id="skill-name"
            className={`${INPUT} mt-1 mb-3`}
            value={editor.name}
            disabled={editor.mode === "edit"}
            placeholder={t("skills.name_placeholder")}
            onChange={(e) => setEditor({ ...editor, name: e.target.value })}
          />
          <label className={FIELD_LABEL} htmlFor="skill-desc">
            {t("skills.desc_label")}
          </label>
          <input
            id="skill-desc"
            className={`${INPUT} mt-1 mb-3`}
            value={editor.description}
            placeholder={t("skills.desc_placeholder")}
            onChange={(e) => setEditor({ ...editor, description: e.target.value })}
          />
          <label className={FIELD_LABEL} htmlFor="skill-instructions">
            {t("skills.instructions_label")}
          </label>
          <textarea
            id="skill-instructions"
            className={`${INPUT} mt-1 mb-3 min-h-[140px] font-mono`}
            value={editor.instructions}
            placeholder={t("skills.instructions_placeholder")}
            onChange={(e) => setEditor({ ...editor, instructions: e.target.value })}
          />
          <div className="flex gap-2 mt-3">
            <button
              className={BTN_ACCENT}
              disabled={!editor.name.trim() || !editor.instructions.trim()}
              onClick={save}
            >
              {t("skills.save_skill")}
            </button>
            <button className={BTN_BORDERED} onClick={() => setEditor(null)}>
              {t("folder_gate.cancel")}
            </button>
          </div>
        </div>
      ) : null}

      {rows.length === 0 && !editor ? (
        <div className={`${CARD} p-5 text-[13px] text-muted`}>
          <Trans i18nKey="skills.empty" components={{ b: <b /> }} />
        </div>
      ) : rows.length > 0 ? (
        <>
          <div className="mb-4">
            <div className="inline-flex items-center gap-1 rounded-xl border border-line bg-panel p-1" role="tablist">
              <button
                role="tab"
                aria-selected={filter === "all"}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] ${filter === "all" ? "bg-paper text-ink font-medium shadow-xs" : "text-muted hover:text-ink"}`}
                onClick={() => setFilter("all")}
              >
                {t("skills.all")}
                <span className="text-[11px] text-muted">{rows.length}</span>
              </button>
              <button
                role="tab"
                aria-selected={filter === "ready"}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] ${filter === "ready" ? "bg-paper text-ink font-medium shadow-xs" : "text-muted hover:text-ink"}`}
                onClick={() => setFilter("ready")}
              >
                {t("skills.ready_to_use")} <span className="text-[11px] text-muted">{readyCount}</span>
              </button>
            </div>
            <div className="flex items-center gap-2 mt-3 flex-wrap">
              <div className="relative flex-1 min-w-[180px]">
                <Icon name="search" size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  className={`${INPUT} pl-9`}
                  aria-label={t("skills.search")}
                  placeholder={t("skills.search")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <span className="inline-flex items-center h-9 rounded-full border border-ink bg-ink text-paper px-3 text-[12px] font-medium">
                {t("skills.filter_skills")}
              </span>
              <div className="flex items-center gap-1" role="group" aria-label={t("skills.layout")}>
                <button
                  className={`${BTN_BORDERED} ${layout === "grid" ? "text-accent border-accent/40" : ""}`}
                  aria-label={t("skills.layout_grid")}
                  aria-pressed={layout === "grid"}
                  title={t("skills.layout_grid")}
                  onClick={() => setLayout("grid")}
                >
                  <Icon name="grid" size={13} />
                </button>
                <button
                  className={`${BTN_BORDERED} ${layout === "list" ? "text-accent border-accent/40" : ""}`}
                  aria-label={t("skills.layout_list")}
                  aria-pressed={layout === "list"}
                  title={t("skills.layout_list")}
                  onClick={() => setLayout("list")}
                >
                  <Icon name="list" size={13} />
                </button>
              </div>
            </div>
          </div>
          {visibleRows.length === 0 ? (
            <div className={`${CARD} p-5 text-[13px] text-muted`}>{t("skills.no_matches")}</div>
          ) : layout === "grid" ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {visibleRows.map((row) => (
                <article key={row.name} className={`${CARD} p-4 min-h-[184px] flex flex-col ${row.enabled ? "border-okLine bg-okSoft/60" : "opacity-70"}`}>
                  <div className="flex items-start gap-3 min-w-0">
                    <div className={`relative flex w-10 h-10 items-center justify-center rounded-lg border ${row.enabled ? "border-okLine bg-okSoft" : "border-line bg-paper"} shrink-0`}>
                      <SkillMeshAvatar seed={`skill:${row.name}`} className="w-7 h-7 rounded-md shadow-inner" square />
                      {row.enabled ? (
                        <span className="absolute -bottom-0.5 -right-0.5 flex w-4 h-4 items-center justify-center rounded-full border-2 border-panel bg-ok">
                          <Icon name="check" size={9} className="text-white" />
                        </span>
                      ) : null}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className={`text-[14px] font-semibold ${row.enabled ? "" : "text-muted"}`}>{row.name}</h3>
                        <span className={CONNECTED_BADGE}>{t("skills.connected")}</span>
                        {row.source !== "local" && row.source !== "detected" ? (
                          <span className={BADGE}>{row.source}</span>
                        ) : null}
                      </div>
                      <p className="text-[12px] text-muted leading-relaxed mt-1 max-h-10 overflow-hidden">{row.description || t("skills.no_description")}</p>
                    </div>
                  </div>
                  <div className="mt-4">{renderActions(row)}</div>
                </article>
              ))}
            </div>
          ) : (
            <div className={`${CARD} divide-y divide-line`}>
              {visibleRows.map((row) => (
                <div
                  key={row.name}
                  className="flex items-center gap-3.5 px-4 py-3 hover:bg-paper/50 transition-colors group"
                >
                  <div
                    className="shrink-0 flex items-center justify-center cursor-pointer"
                    onClick={() => setDetail(row)}
                    role="button"
                    tabIndex={0}
                    aria-label={t("skills.view_details_aria", { name: row.name })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setDetail(row);
                      }
                    }}
                  >
                    <div className="relative flex w-9 h-9 items-center justify-center rounded-lg border border-line/70 bg-paper shadow-2xs overflow-hidden">
                      <SkillMeshAvatar seed={`skill:${row.name}`} className="w-9 h-9" square />
                    </div>
                  </div>
                  <div className="flex-1 min-w-0 flex items-center gap-3">
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        className={`text-[13px] font-medium transition-colors hover:text-accent truncate text-left ${row.enabled ? "text-ink" : "text-muted"}`}
                        onClick={() => setDetail(row)}
                      >
                        {row.name}
                      </button>
                      <span
                        className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${row.enabled ? "bg-ok" : "bg-faint"}`}
                        title={row.enabled ? t("skills.connected") : t("skills.off_note")}
                      />
                    </div>
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-paper border border-line/60 text-muted shrink-0 font-normal">
                      Skill
                    </span>
                    {row.source !== "local" && row.source !== "detected" ? (
                      <span className={BADGE}>{row.source}</span>
                    ) : null}
                    <p
                      className="text-[12px] text-muted truncate flex-1 min-w-0"
                      title={row.description || t("skills.no_description")}
                    >
                      {row.description || t("skills.no_description")}
                    </p>
                  </div>
                  <div className="shrink-0">
                    {renderActions(row)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : null}

      {detail ? (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4"
          role="presentation"
          onClick={() => setDetail(null)}
        >
          <div
            className="flex max-h-[88vh] w-full max-w-3xl min-h-0 flex-col overflow-hidden rounded-xl2 border border-line bg-panel shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby="skill-detail-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="shrink-0 border-b border-line px-5 py-4">
              <div className="flex items-start gap-3">
                <div className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-okLine bg-okSoft">
                  <SkillMeshAvatar seed={`skill:${detail.name}`} className="h-9 w-9 rounded-lg shadow-inner" square />
                  {detail.enabled ? (
                    <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full border-2 border-panel bg-ok">
                      <Icon name="check" size={10} className="text-white" />
                    </span>
                  ) : null}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 id="skill-detail-title" className="text-[16px] font-semibold tracking-tight">{detail.name}</h3>
                    <span className={CONNECTED_BADGE}>{t("skills.connected")}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {detail.source === "detected" ? (
                      <span className={BADGE}>{t("skills.detected_on_device")}</span>
                    ) : null}
                    {detail.source !== "local" && detail.source !== "detected" ? (
                      <span className={BADGE}>{detail.source}</span>
                    ) : null}
                    {detail.files ? (
                      <span className={`${BADGE} inline-flex items-center gap-1`}>
                        <Icon name="files" size={11} /> {t("skills.file_count", { count: detail.files })}
                      </span>
                    ) : null}
                  </div>
                </div>
                <button
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-paper text-muted hover:border-lineStrong hover:text-ink"
                  aria-label={t("common.dismiss")}
                  title={t("common.dismiss")}
                  onClick={() => setDetail(null)}
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
              <p className="mt-3 max-w-2xl text-[13px] leading-relaxed text-muted">
                {detail.description || t("skills.no_description")}
              </p>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <div className="space-y-4">
                <div className="rounded-lg border border-line bg-paper px-3 py-2.5">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
                    <Icon name="folder" size={12} /> {t("skills.path")}
                  </div>
                  <div className="mt-1 truncate font-mono text-[12px] text-ink" title={detail.path}>
                    {detail.path}
                  </div>
                </div>

                <section aria-labelledby="skill-instructions-title">
                  <div className="mb-2">
                    <h4 id="skill-instructions-title" className="text-[13px] font-semibold text-ink">
                      {t("skills.instructions_label")}
                    </h4>
                  </div>
                  <div className="max-h-80 overflow-y-auto rounded-xl border border-line bg-paper px-4 py-3.5">
                    <Markdown text={detail.instructions} />
                  </div>
                </section>
              </div>
            </div>

            <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-line bg-paper/50 px-5 py-3">
              <button className={BTN_BORDERED} onClick={() => void revealSkill(detail.name)}>
                <Icon name="folder" size={13} /> {t("skills.show_folder")}
              </button>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <button className={BTN_BORDERED} onClick={() => setDetail(null)}>
                  {t("common.close")}
                </button>
                {detail.source !== "detected" ? (
                  <button className={BTN_ACCENT} onClick={() => edit(detail)}>
                    <Icon name="pencil" size={13} /> {t("skills.edit_tip")}
                  </button>
                ) : null}
              </div>
            </footer>
          </div>
        </div>
      ) : null}

    </section>
  );
}
