import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  listSkills,
  revealSkill,
  stageSkillUpload,
  confirmSkillUpload,
  updateSkill,
  type SkillRow,
  type SkillUploadPreview,
} from "../../api";
import { Icon } from "../Icon";
import { SkillMeshAvatar } from "../SkillMeshAvatar";
import { Toggle } from "../Toggle";

export function CodexSkillsList({
  onCreateSkill: _onCreateSkill,
}: {
  onCreateSkill?: (description: string) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SkillRow[]>([]);
  const [filter, setFilter] = useState("");
  const [scopeTab, setScopeTab] = useState<"personal" | "system">("personal");
  const [upload, setUpload] = useState<SkillUploadPreview | null>(null);
  const [detail, setDetail] = useState<SkillRow | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = async () => {
    try {
      setRows(await listSkills());
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const q = filter.trim().toLowerCase();
  const matchedRows = useMemo(() => {
    return rows.filter((r) => !q || r.name.toLowerCase().includes(q) || r.description.toLowerCase().includes(q));
  }, [rows, q]);

  const enabledSkills = useMemo(() => matchedRows.filter((r) => r.enabled), [matchedRows]);
  const personalSkills = useMemo(
    () => matchedRows.filter((r) => r.scope === "global" || r.scope === "project" || r.source === "user"),
    [matchedRows],
  );
  const systemSkills = useMemo(
    () => matchedRows.filter((r) => r.source === "builtin" || r.source === "detected"),
    [matchedRows],
  );

  const displayedList = scopeTab === "personal" ? personalSkills : systemSkills;

  const toggle = (row: SkillRow, enabled: boolean) => {
    void updateSkill(row.name, { enabled }).then(() => {
      void refresh();
    });
  };

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
          placeholder={t("skills.search_placeholder", "Tìm kiếm kỹ năng")}
          className="w-full pl-10 pr-4 py-2.5 rounded-full border border-line bg-paper/60 text-[13px] text-ink placeholder:text-muted outline-none focus:border-accent focus:bg-paper transition-all"
        />
      </div>

      {/* Installed / Enabled Skills Strip (Đã cài đặt) */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <span className="text-[13px] font-semibold text-ink">
            {t("skills.installed", "Đã cài đặt")}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fileInput.current?.click()}
              className="text-[12px] text-muted hover:text-ink flex items-center gap-1 transition-colors"
              title={t("skills.door_import", "Nhập tệp")}
            >
              <Icon name="paperclip" size={13} />
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".zip,.tar,.gz,.tar.gz"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const buf = await file.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let bin = "";
                for (let i = 0; i < bytes.length; i += 0x8000) {
                  bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
                }
                const res = await stageSkillUpload(btoa(bin), file.name);
                if (res.ok !== false) {
                  setUpload(res);
                }
              }}
            />
          </div>
        </div>

        {enabledSkills.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {enabledSkills.map((row) => (
              <div
                key={row.name}
                onClick={() => setDetail(row)}
                className="flex items-center justify-between p-3.5 rounded-xl border border-line bg-panel hover:border-lineStrong hover:bg-paper transition-all cursor-pointer group"
              >
                <div className="flex items-center gap-3 min-w-0 mr-3">
                  <div className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center bg-paper/80 border border-line">
                    <SkillMeshAvatar seed={row.name} className="w-6 h-6" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-ink truncate group-hover:text-accent transition-colors">
                      {row.name}
                    </div>
                    <div className="text-[12px] text-muted truncate">
                      {row.description || t("skills.no_description", "Kỹ năng theo tác vụ")}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0" onClick={(e) => e.stopPropagation()}>
                  <Toggle
                    checked={row.enabled}
                    onChange={(checked) => toggle(row, checked)}
                    title={row.name}
                  />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[13px] text-muted py-2">
            {t("skills.empty", "Chưa có kỹ năng nào được bật.")}
          </div>
        )}
      </div>

      {/* Scope Switcher: Cá nhân | Hệ thống */}
      <div className="flex items-center justify-between border-b border-line pb-2">
        <div className="flex items-center gap-4 text-[13px]">
          <button
            className={`font-medium transition-colors ${
              scopeTab === "personal" ? "text-ink border-b-2 border-ink pb-2 -mb-2" : "text-muted hover:text-ink"
            }`}
            onClick={() => setScopeTab("personal")}
          >
            {t("skills.scope_personal", "Cá nhân")}
          </button>
          <button
            className={`font-medium transition-colors ${
              scopeTab === "system" ? "text-ink border-b-2 border-ink pb-2 -mb-2" : "text-muted hover:text-ink"
            }`}
            onClick={() => setScopeTab("system")}
          >
            {t("skills.scope_system", "Hệ thống")}
          </button>
        </div>
      </div>

      {/* Skills 2-column Grid */}
      <div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {displayedList.map((row) => (
            <div
              key={row.name}
              onClick={() => setDetail(row)}
              className="flex items-center justify-between p-3.5 rounded-xl border border-line bg-panel hover:border-lineStrong hover:bg-paper transition-all cursor-pointer group"
            >
              <div className="flex items-center gap-3 min-w-0 mr-3">
                <div className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center bg-paper/80 border border-line">
                  <SkillMeshAvatar seed={row.name} className="w-6 h-6" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-ink truncate group-hover:text-accent transition-colors">
                    {row.name}
                  </div>
                  <div className="text-[12px] text-muted truncate">
                    {row.description || t("skills.no_description", "Kỹ năng theo tác vụ")}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0" onClick={(e) => e.stopPropagation()}>
                <Toggle
                  checked={row.enabled}
                  onChange={(checked) => toggle(row, checked)}
                  title={row.name}
                />
              </div>
            </div>
          ))}
          {displayedList.length === 0 && (
            <div className="col-span-2 text-center text-[13px] text-muted py-6">
              {t("skills.no_match", "Không tìm thấy kỹ năng phù hợp.")}
            </div>
          )}
        </div>
      </div>

      {/* Upload Confirmation Modal */}
      {upload && typeof document !== "undefined" && createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-xs" onClick={() => setUpload(null)} />
          <div className="relative w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-2xl space-y-4">
            <h3 className="text-[16px] font-semibold text-ink">{t("skills.review_title", "Xem lại trước khi cài đặt")}</h3>
            <p className="text-[13px] text-muted">{upload.name || t("skills.fallback_name")}</p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                className="px-3 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink hover:bg-chromeHover"
                onClick={() => setUpload(null)}
              >
                {t("cancel", "Hủy")}
              </button>
              <button
                className="px-3 py-1.5 rounded-lg bg-accent text-white text-[13px] font-medium hover:opacity-90"
                onClick={async () => {
                  if (upload.token) {
                    await confirmSkillUpload(upload.token);
                    setUpload(null);
                    void refresh();
                  }
                }}
              >
                {t("skills.install_skill", "Cài đặt kỹ năng")}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Skill Detail Modal */}
      {detail && typeof document !== "undefined" && createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/40 backdrop-blur-xs" onClick={() => setDetail(null)} />
          <div className="relative w-full max-w-lg rounded-2xl border border-line bg-panel p-6 shadow-2xl space-y-4 max-h-[85vh] overflow-y-auto hairline-scroll">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <SkillMeshAvatar seed={detail.name} className="w-8 h-8" />
                <div>
                  <h3 className="text-[16px] font-semibold text-ink">{detail.name}</h3>
                  <div className="text-[12px] text-muted">{detail.scope || "global"}</div>
                </div>
              </div>
              <button
                onClick={() => setDetail(null)}
                className="p-1 rounded-lg text-muted hover:text-ink hover:bg-chromeHover"
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            <div className="space-y-2">
              <div className="text-[12px] uppercase font-semibold text-faint tracking-wider">Mô tả</div>
              <div className="text-[13px] text-ink bg-paper/60 p-3 rounded-lg border border-line">
                {detail.description || t("skills.no_description")}
              </div>
            </div>

            {detail.instructions && (
              <div className="space-y-2">
                <div className="text-[12px] uppercase font-semibold text-faint tracking-wider">Chỉ dẫn</div>
                <div className="text-[12px] text-muted bg-paper/60 p-3 rounded-lg border border-line whitespace-pre-wrap font-mono max-h-48 overflow-y-auto">
                  {detail.instructions}
                </div>
              </div>
            )}

            <div className="flex items-center justify-between pt-2 border-t border-line">
              <div className="flex items-center gap-2">
                {detail.files ? (
                  <button
                    className="inline-flex items-center gap-1 text-[12px] px-2 py-1 rounded-md border border-line bg-paper text-muted hover:text-ink"
                    onClick={() => void revealSkill(detail.name)}
                  >
                    <Icon name="folder" size={12} /> {t("skills.file_count", { count: detail.files })}
                  </button>
                ) : null}
              </div>

              <button
                className="px-3 py-1.5 rounded-lg bg-ink text-paper text-[13px] font-medium hover:opacity-90"
                onClick={() => setDetail(null)}
              >
                Đóng
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
