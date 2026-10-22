import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import { useTranslation } from "react-i18next";
import remarkGfm from "remark-gfm";
import { Icon } from "./Icon";

// §34 (UX-016): the agent ends a deliverable turn with plain markdown —
// [Title](artifact:relative/path) — and the renderer turns it into a chip that opens the
// artifact viewer in place. Plumbing is a window event (the viewer lives in RightRail;
// this component renders deep inside the transcript): RightRail resolves the path against
// the session's artifact list, App un-hides the rail.
export const OPEN_ARTIFACT_EVENT = "ocw-open-artifact";

// Seventeenth pass: the lead mentions the board ONCE — [Board · 5 items](board:) — and the
// chip opens the drawer on its Board section. Same event plumbing as artifact chips: App
// un-hides the rail and bumps the key that expands the section.
export const OPEN_BOARD_EVENT = "ocw-open-board";

function BoardChip({ label }: { label: string }) {
  return (
    <button
      className="boardlink-chip"
      data-testid="board-chip"
      title="Open the board"
      onClick={() => window.dispatchEvent(new CustomEvent(OPEN_BOARD_EVENT))}
    >
      <Icon name="table" size={12} />
      <span>{label || "Board"}</span>
    </button>
  );
}

function ArtifactChip({ path, title }: { path: string; title: string }) {
  const { t } = useTranslation();
  const file = path.split("/").pop() || path;
  const ext = (file.split(".").pop() || "").toUpperCase();

  return (
    <span className="my-2 inline-flex items-center">
      <button
        type="button"
        data-testid="artifact-chip"
        title={path}
        onClick={() =>
          window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path } }))
        }
        className="group/art flex max-w-full items-center gap-3 rounded-xl border border-dls-border bg-dls-surface px-3 py-2 text-left shadow-sm transition-all hover:border-dls-secondary/40 hover:bg-dls-hover active:scale-[0.99]"
      >
        {/* File icon container */}
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-dls-border bg-dls-surface-muted text-dls-secondary group-hover/art:text-dls-text transition-colors">
          <Icon name="file" size={16} />
        </div>

        {/* Metadata */}
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[13px] font-semibold text-dls-text leading-tight">
            {title || file}
          </span>
          <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[11px] text-dls-secondary">
            <span className="truncate">{file}</span>
            {ext && (
              <span className="shrink-0 rounded bg-dls-hover px-1 py-0.2 text-[9.5px] uppercase tracking-wider text-dls-secondary">
                {ext}
              </span>
            )}
          </div>
        </div>

        {/* Trailing action */}
        <span className="ml-2 flex shrink-0 items-center gap-1 text-xs font-medium text-dls-secondary group-hover/art:text-dls-text transition-colors">
          <span>{t("rail.open") || "Open"}</span>
          <span className="text-[14px] leading-none transition-transform group-hover/art:translate-x-0.5">›</span>
        </span>
      </button>
    </span>
  );
}

// Assistant messages rendered as GitHub-flavored markdown (headings, lists, tables, code,
// links). Links open externally — never navigate the app shell — except artifact: links,
// which open the session's artifact viewer.
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // artifact:/board: are ours — keep them through the sanitizer (everything else gets
        // the default http/https/mailto policy).
        urlTransform={(url) =>
          url.startsWith("artifact:") || url.startsWith("board:") ? url : defaultUrlTransform(url)
        }
        components={{
          a: ({ node: _n, href, children, ...props }) => {
            if (href?.startsWith("artifact:")) {
              const title = Array.isArray(children) ? children.join("") : String(children ?? "");
              return <ArtifactChip path={href.slice("artifact:".length)} title={title} />;
            }
            if (href?.startsWith("board:")) {
              const label = Array.isArray(children) ? children.join("") : String(children ?? "");
              return <BoardChip label={label} />;
            }
            return (
              <a href={href} {...props} target="_blank" rel="noreferrer" className="text-accent underline-offset-2 hover:underline">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
