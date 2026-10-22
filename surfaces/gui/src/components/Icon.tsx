import { AstraGrokLogo } from "./AstraGrokLogo";

export type IconName =
  | "sparkle"
  | "star"
  | "logo"
  | "sidebar"
  | "sidebarRight"
  | "signOut"
  | "chat"
  | "diamond"
  | "book"
  | "search"
  | "folder"
  | "folderPlus"
  | "plus"
  | "clock"
  | "sliders"
  | "gear"
  | "inbox"
  | "users"
  | "code"
  | "wrench"
  | "pencil"
  | "branch"
  | "arrowLeft"
  | "copy"
  | "refresh"
  | "panelClose"
  | "panelOpen"
  | "expandPanel"
  | "collapsePanel"
  | "plug"
  | "audit"
  | "chevronRight"
  | "chevronDown"
  | "moreHorizontal"
  | "pin"
  | "archive"
  | "trash"
  | "shield"
  | "file"
  | "fileCode"
  | "image"
  | "table"
  | "mic"
  | "stop"
  | "warning"
  | "quote"
  | "paperclip"
  | "smile"
  | "check"
  | "send"
  | "translate"
  | "messageCircle"
  | "listChecks"
  | "activity"
  | "bot"
  | "server"
  | "files"
  | "grid"
  | "list"
  | "monitor"
  | "globe"
  | "moreVertical"
  | "externalLink"
  | "x";

export function Icon({
  name,
  size = 16,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  const s = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    "aria-hidden": true,
  };

  switch (name) {
    case "book":
      // A playbook — Skills are the worker's recipe book (Settings ▸ Skills).
      // Hardcover with a full spine + two text lines: "written instructions inside".
      // (Owner-picked from a 15px-preview comparison, 2026-07-27.)
      return (
        <svg {...s}>
          <path d="M6 2h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" />
          <path d="M8.5 2v20" />
          <path d="M11.5 8.5h5" />
          <path d="M11.5 12h3.5" />
        </svg>
      );
    case "sparkle":
      // Filled 4-point twinkle — crisp at small sizes.
      return (
        <svg {...s} fill="currentColor" stroke="none">
          <path d="M12 2.4c.5 4.7 2.5 6.7 7.2 7.2-4.7.5-6.7 2.5-7.2 7.2-.5-4.7-2.5-6.7-7.2-7.2 4.7-.5 6.7-2.5 7.2-7.2z" />
        </svg>
      );
    case "star":
      return (
        <svg {...s}>
          <path d="m12 3 2.75 5.57 6.15.89-4.45 4.34 1.05 6.13L12 17.04 6.5 19.93l1.05-6.13L3.1 9.46l6.15-.89L12 3z" />
        </svg>
      );
    case "logo":
      return <AstraGrokLogo size={size} className={className} />;
    case "sidebar":
      // Clean "sidebar panel" toggle — rounded rect + divider, no chevron (one glyph both states).
      return (
        <svg {...s}>
          <rect x="3.5" y="4.5" width="17" height="15" rx="4" />
          <path d="M9 4.5v15" />
        </svg>
      );
    case "sidebarRight":
      // Right-panel counterpart of "sidebar" — same rounded rect + divider, mirrored.
      return (
        <svg {...s}>
          <rect x="3.5" y="4.5" width="17" height="15" rx="4" />
          <path d="M15 4.5v15" />
        </svg>
      );
    case "signOut":
      return (
        <svg {...s}>
          <path d="M9 4H5.5A1.5 1.5 0 0 0 4 5.5v13A1.5 1.5 0 0 0 5.5 20H9" />
          <path d="M15 8l4 4-4 4M19 12H9" />
        </svg>
      );
    case "folder":
      // Clean single-tab folder (Lucide-style) — no internal divider line.
      return (
        <svg {...s}>
          <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9l-.81-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
        </svg>
      );
    case "shield":
      return (
        <svg {...s}>
          <path d="M12 3.2l7 2.8v5.1c0 4.3-2.9 7.4-7 9.7-4.1-2.3-7-5.4-7-9.7V6l7-2.8z" />
          <path d="M9.3 12.1l1.9 1.9 3.5-3.6" />
        </svg>
      );
    case "file":
      // Document with a folded corner + text lines.
      return (
        <svg {...s}>
          <path d="M6.5 3.5h7L18 8v11a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5z" />
          <path d="M13.5 3.5V8H18M8.5 12.5h7M8.5 16h5" />
        </svg>
      );
    case "files":
      return (
        <svg {...s}>
          <path d="M8 3.5h7l4 4v13H8a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2z" />
          <path d="M15 3.5v4h4M4 7.5v11a2 2 0 0 0 2 2h9M10 12h6M10 15.5h4" />
        </svg>
      );
    case "fileCode":
      // Document with </> marks (html/code).
      return (
        <svg {...s}>
          <path d="M6.5 3.5h7L18 8v11a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5z" />
          <path d="M13.5 3.5V8H18M10.2 12l-2 2.2 2 2.2M13.3 12l2 2.2-2 2.2" />
        </svg>
      );
    case "image":
      // Picture frame: sun + mountains.
      return (
        <svg {...s}>
          <rect x="4" y="5" width="16" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.6" />
          <path d="M4.5 16.5l4.5-4 3.5 3 3-2.5 4 3.5" />
        </svg>
      );
    case "table":
      // Spreadsheet grid (csv/xlsx).
      return (
        <svg {...s}>
          <rect x="4" y="5" width="16" height="14" rx="2" />
          <path d="M4 10h16M10 10v9M4 14.5h16" />
        </svg>
      );
    case "grid":
      return (
        <svg {...s}>
          <rect x="4" y="4" width="6" height="6" rx="1" />
          <rect x="14" y="4" width="6" height="6" rx="1" />
          <rect x="4" y="14" width="6" height="6" rx="1" />
          <rect x="14" y="14" width="6" height="6" rx="1" />
        </svg>
      );
    case "list":
      return (
        <svg {...s}>
          <path d="M9 6h11M9 12h11M9 18h11" />
          <circle cx="4.5" cy="6" r=".8" fill="currentColor" stroke="none" />
          <circle cx="4.5" cy="12" r=".8" fill="currentColor" stroke="none" />
          <circle cx="4.5" cy="18" r=".8" fill="currentColor" stroke="none" />
        </svg>
      );
    case "mic":
      return (
        <svg {...s}>
          <rect x="8.5" y="3.5" width="7" height="11" rx="3.5" />
          <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3.5M8.5 21.5h7" />
        </svg>
      );
    case "stop":
      return (
        <svg {...s} fill="currentColor" stroke="none">
          <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />
        </svg>
      );
    case "x":
      return (
        <svg {...s}>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      );
    case "warning":
      // Caution triangle (Lucide-style): rounded triangle + exclamation. Marks the
      // bypass-approvals mode in the picker — the one option that switches approvals off.
      return (
        <svg {...s}>
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </svg>
      );
    case "folderPlus":
      // Same clean folder body + a centered plus (Lucide-style).
      return (
        <svg {...s}>
          <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9l-.81-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h8.5" />
          <path d="M19 14v6M16 17h6" />
        </svg>
      );
    case "plus":
      return (
        <svg {...s}>
          <path d="M12 5.5v13M5.5 12h13" />
        </svg>
      );
    case "clock":
      return (
        <svg {...s}>
          <circle cx="12" cy="12" r="9" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      );
    case "activity":
      return (
        <svg {...s}>
          <path d="M3 12h4l2.2-6 4.1 12 2.2-6H21" />
        </svg>
      );
    case "sliders":
      // Two rails, knob on each, rails broken around the knobs (owner pick 2026-07-11:
      // the three-rail version read as heavier than the mocks' two-rail glyph).
      return (
        <svg {...s}>
          <path d="M4 8h10M18 8h2M4 16h2M10 16h10" />
          <circle cx="16" cy="8" r="2" />
          <circle cx="8" cy="16" r="2" />
        </svg>
      );
    case "gear":
      return (
        <svg {...s}>
          <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    case "inbox":
      return (
        <svg {...s}>
          <path d="M22 12h-6l-2 3h-4l-2-3H2" />
          <path d="M5.4 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.4-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.8 1.1z" />
        </svg>
      );
    case "users":
      return (
        <svg {...s}>
          <circle cx="9" cy="8" r="3" />
          <path d="M3.5 19a5.5 5.5 0 0 1 11 0M15.5 5.5a3 3 0 0 1 0 5.8M17 13.5a4.5 4.5 0 0 1 3.5 4" />
        </svg>
      );
    case "bot":
      return (
        <svg {...s}>
          <rect x="4" y="7" width="16" height="13" rx="3" />
          <path d="M12 3v4M8.5 12h.01M15.5 12h.01M8.5 16h7" />
        </svg>
      );
    case "server":
      return (
        <svg {...s}>
          <rect x="2" y="3" width="20" height="7" rx="2" />
          <rect x="2" y="14" width="20" height="7" rx="2" />
          <line x1="6" y1="6.5" x2="6.01" y2="6.5" />
          <line x1="6" y1="17.5" x2="6.01" y2="17.5" />
        </svg>
      );
    case "code":
      return (
        <svg {...s}>
          <path d="M8.5 8.5 4.5 12l4 3.5M15.5 8.5l4 3.5-4 3.5" />
        </svg>
      );
    case "chat":
      return (
        <svg {...s}>
          <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
        </svg>
      );
    case "messageCircle":
      return (
        <svg {...s}>
          <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 8.5 8.5 0 0 1-4.1-1L3 21l2-5.4A8.5 8.5 0 1 1 21 11.5z" />
        </svg>
      );
    case "wrench":
      return (
        <svg {...s}>
          <path d="M14.6 6.4a3.8 3.8 0 0 0-5 4.9l-6 6a1.6 1.6 0 0 0 2.3 2.3l6-6a3.8 3.8 0 0 0 4.9-5l-2.4 2.4-2-.5-.5-2 2.7-2.1z" />
        </svg>
      );
    case "search":
      return (
        <svg {...s}>
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
      );
    case "diamond":
      return (
        <svg {...s} fill="currentColor" stroke="none">
          <path d="M12 3.2 20.8 12 12 20.8 3.2 12z" />
        </svg>
      );
    case "pencil":
      return (
        <svg {...s}>
          <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
          <path d="m15 5 4 4" />
        </svg>
      );
    case "branch":
      return (
        <svg {...s}>
          <circle cx="7" cy="6" r="2.1" />
          <circle cx="7" cy="18" r="2.1" />
          <circle cx="17" cy="8" r="2.1" />
          <path d="M7 8.1v7.8M7 12.5c5.4 0 10-.4 10-2.4" />
        </svg>
      );
    case "arrowLeft":
      return (
        <svg {...s}>
          <path d="M19 12H5M11 6l-6 6 6 6" />
        </svg>
      );
    case "copy":
      return (
        <svg {...s}>
          <rect x="8" y="8" width="11" height="11" rx="2" />
          <path d="M5 15.5H4.8A1.8 1.8 0 0 1 3 13.7V4.8A1.8 1.8 0 0 1 4.8 3h8.9a1.8 1.8 0 0 1 1.8 1.8V5" />
        </svg>
      );
    case "refresh":
      return (
        <svg {...s}>
          <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M16 16h4v4" />
        </svg>
      );
    case "panelClose":
      return (
        <svg {...s}>
          <rect x="4" y="5" width="16" height="14" rx="2" />
          <path d="M14.5 5v14M8 10l3 2-3 2" />
        </svg>
      );
    case "panelOpen":
      return (
        <svg {...s}>
          <rect x="4" y="5" width="16" height="14" rx="2" />
          <path d="M9.5 5v14M16 10l-3 2 3 2" />
        </svg>
      );
    case "plug":
      return (
        <svg {...s}>
          <path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5" />
        </svg>
      );
    case "audit":
      return (
        <svg {...s}>
          <path d="M7 4h10a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />
          <path d="M8.5 9h7M8.5 13h7M8.5 17H13" />
        </svg>
      );
    case "chevronRight":
      return (
        <svg {...s}>
          <path d="m9 6 6 6-6 6" />
        </svg>
      );
    case "chevronDown":
      return (
        <svg {...s}>
          <path d="m6 9 6 6 6-6" />
        </svg>
      );
    case "moreHorizontal":
      return (
        <svg {...s}>
          <circle cx="6.5" cy="12" r="1.2" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
          <circle cx="17.5" cy="12" r="1.2" fill="currentColor" stroke="none" />
        </svg>
      );
    case "pin":
      return (
        <svg {...s}>
          <line x1="12" y1="17" x2="12" y2="22" />
          <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
        </svg>
      );
    case "archive":
      return (
        <svg {...s}>
          <path d="M4.5 7.5h15M6 7.5v10.2c0 1 .8 1.8 1.8 1.8h8.4c1 0 1.8-.8 1.8-1.8V7.5M5.6 4.5h12.8c.6 0 1.1.5 1.1 1.1v1.9h-15V5.6c0-.6.5-1.1 1.1-1.1z" />
          <path d="M9.5 11.5h5" />
        </svg>
      );
    case "trash":
      return (
        <svg {...s}>
          <path d="M4.5 7h15" />
          <path d="M10 11v6M14 11v6" />
          <path d="M6.5 7l.9 12c.1.9.8 1.5 1.7 1.5h7.8c.9 0 1.6-.6 1.7-1.5l.9-12" />
          <path d="M9.2 7V4.9c0-.5.4-.9.9-.9h3.8c.5 0 .9.4.9.9V7" />
        </svg>
      );
    case "quote":
      return (
        <svg {...s}>
          <path d="M3 21c3 0 7-1 7-8V5c0-1.25-.75-2-2-2H4c-1.25 0-2 .75-2 2v6c0 1.25.75 2 2 2 0 4 0 6-1 8zm14 0c3 0 7-1 7-8V5c0-1.25-.75-2-2-2h-4c-1.25 0-2 .75-2 2v6c0 1.25.75 2 2 2 0 4 0 6-1 8z" />
        </svg>
      );
    case "paperclip":
      return (
        <svg {...s}>
          <path d="m8.5 12.5 5.9-5.9a3.1 3.1 0 0 1 4.4 4.4l-7.4 7.4a4.5 4.5 0 0 1-6.4-6.4l7.1-7.1a2.7 2.7 0 0 1 3.8 3.8l-6.7 6.7a1.5 1.5 0 0 1-2.1-2.1l6.1-6.1" />
        </svg>
      );
    case "smile":
      return (
        <svg {...s}>
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="9" cy="10" r="0.8" fill="currentColor" stroke="none" />
          <circle cx="15" cy="10" r="0.8" fill="currentColor" stroke="none" />
          <path d="M8.5 14a4.5 4.5 0 0 0 7 0" />
        </svg>
      );
    case "check":
      return (
        <svg {...s}>
          <polyline points="20 6 9 17 4 12" />
        </svg>
      );
    case "listChecks":
      return (
        <svg {...s}>
          <path d="m4 6 1.5 1.5L8 5M11 6h9M4 12l1.5 1.5L8 11M11 12h9M4 18l1.5 1.5L8 17M11 18h9" />
        </svg>
      );
    case "send":
      return (
        <svg {...s}>
          <path d="M22 2L11 13" />
          <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
      );
    case "translate":
      return (
        <svg {...s}>
          <path d="M5 8l6 0" />
          <path d="M4 14l6-6 2-3" />
          <path d="M2 5h12" />
          <path d="M7 2h1" />
          <path d="M22 22l-5-10-5 10" />
          <path d="M14 18h6" />
        </svg>
      );
    case "monitor":
      return (
        <svg {...s}>
          <rect x="2" y="3" width="20" height="14" rx="2" />
          <line x1="8" y1="21" x2="16" y2="21" />
          <line x1="12" y1="17" x2="12" y2="21" />
        </svg>
      );
    case "globe":
      return (
        <svg {...s}>
          <circle cx="12" cy="12" r="10" />
          <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
          <path d="M2 12h20" />
        </svg>
      );
    case "moreVertical":
      return (
        <svg {...s}>
          <circle cx="12" cy="5" r="1" fill="currentColor" />
          <circle cx="12" cy="12" r="1" fill="currentColor" />
          <circle cx="12" cy="19" r="1" fill="currentColor" />
        </svg>
      );
    case "externalLink":
      return (
        <svg {...s}>
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
          <polyline points="15 3 21 3 21 9" />
          <line x1="10" y1="14" x2="21" y2="3" />
        </svg>
      );
    case "expandPanel":
      return (
        <svg {...s}>
          <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
        </svg>
      );
    case "collapsePanel":
      return (
        <svg {...s}>
          <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7" />
        </svg>
      );
  }
}
