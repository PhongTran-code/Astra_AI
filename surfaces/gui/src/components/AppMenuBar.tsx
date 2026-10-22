import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type MenuKey = "file" | "edit" | "view" | "window" | "help";

interface MenuItem {
  label: string;
  shortcut?: string;
  action?: () => void;
  separator?: boolean;
}

export function AppMenuBar({
  className = "",
  onNewSession,
  onOpenSettings,
}: {
  className?: string;
  onNewSession?: () => void;
  onOpenSettings?: () => void;
}) {
  const { t } = useTranslation();
  const [openMenu, setOpenMenu] = useState<MenuKey | null>(null);
  const barRef = useRef<HTMLDivElement>(null);

  // Close when clicking outside or pressing Escape
  useEffect(() => {
    if (!openMenu) return;

    const handlePointerDown = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) {
        setOpenMenu(null);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpenMenu(null);
      }
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [openMenu]);

  const desktop = typeof window !== "undefined" ? (window as any).desktop : null;

  const menus: Record<MenuKey, { title: string; items: MenuItem[] }> = {
    file: {
      title: t("menubar.file", "Tệp"),
      items: [
        {
          label: t("sidebar.new_session", "Đoạn chat mới"),
          shortcut: "Ctrl+N",
          action: () => {
            onNewSession?.();
            window.dispatchEvent(new CustomEvent("coworker:new-session"));
          },
        },
        { separator: true, label: "" },
        {
          label: t("nav.settings", "Cài đặt"),
          shortcut: "Ctrl+,",
          action: () => {
            onOpenSettings?.();
            window.dispatchEvent(new CustomEvent("coworker:open-settings"));
          },
        },
        { separator: true, label: "" },
        {
          label: t("menubar.close_window", "Đóng cửa sổ"),
          shortcut: "Ctrl+W",
          action: () => desktop?.close?.(),
        },
        {
          label: t("menubar.quit", "Thoát Astra"),
          shortcut: "Ctrl+Q",
          action: () => desktop?.close?.(),
        },
      ],
    },
    edit: {
      title: t("menubar.edit", "Chỉnh sửa"),
      items: [
        {
          label: t("menubar.undo", "Hoàn tác"),
          shortcut: "Ctrl+Z",
          action: () => document.execCommand("undo"),
        },
        {
          label: t("menubar.redo", "Làm lại"),
          shortcut: "Ctrl+Y",
          action: () => document.execCommand("redo"),
        },
        { separator: true, label: "" },
        {
          label: t("menubar.cut", "Cắt"),
          shortcut: "Ctrl+X",
          action: () => document.execCommand("cut"),
        },
        {
          label: t("menubar.copy", "Sao chép"),
          shortcut: "Ctrl+C",
          action: () => document.execCommand("copy"),
        },
        {
          label: t("menubar.paste", "Dán"),
          shortcut: "Ctrl+V",
          action: () => document.execCommand("paste"),
        },
        { separator: true, label: "" },
        {
          label: t("menubar.select_all", "Chọn tất cả"),
          shortcut: "Ctrl+A",
          action: () => document.execCommand("selectAll"),
        },
      ],
    },
    view: {
      title: t("menubar.view", "Xem"),
      items: [
        {
          label: t("menubar.reload", "Tải lại trang"),
          shortcut: "Ctrl+R",
          action: () => window.location.reload(),
        },
        {
          label: t("menubar.toggle_devtools", "Công cụ phát triển"),
          shortcut: "F12",
          action: () => desktop?.toggleDevTools?.(),
        },
        { separator: true, label: "" },
        {
          label: t("menubar.zoom_in", "Phóng to"),
          shortcut: "Ctrl++",
          action: () => {
            const cur = parseFloat(document.body.style.zoom || "1");
            document.body.style.zoom = String(Math.min(2, cur + 0.1));
          },
        },
        {
          label: t("menubar.zoom_out", "Thu nhỏ"),
          shortcut: "Ctrl+-",
          action: () => {
            const cur = parseFloat(document.body.style.zoom || "1");
            document.body.style.zoom = String(Math.max(0.5, cur - 0.1));
          },
        },
        {
          label: t("menubar.reset_zoom", "Kích thước chuẩn"),
          shortcut: "Ctrl+0",
          action: () => {
            document.body.style.zoom = "1";
          },
        },
      ],
    },
    window: {
      title: t("menubar.window", "Cửa sổ"),
      items: [
        {
          label: t("menubar.minimize", "Thu nhỏ"),
          action: () => desktop?.minimize?.(),
        },
        {
          label: t("menubar.maximize", "Phóng to / Khôi phục"),
          action: () => desktop?.maximize?.(),
        },
        { separator: true, label: "" },
        {
          label: t("menubar.close", "Đóng"),
          shortcut: "Alt+F4",
          action: () => desktop?.close?.(),
        },
      ],
    },
    help: {
      title: t("menubar.help", "Trợ giúp"),
      items: [
        {
          label: t("menubar.docs", "Tài liệu Astra"),
          action: () => window.open("https://github.com/PhongTran-code/Astra_AI", "_blank"),
        },
        {
          label: t("menubar.issues", "Báo lỗi / Góp ý"),
          action: () => window.open("https://github.com/PhongTran-code/Astra_AI/issues", "_blank"),
        },
        { separator: true, label: "" },
        {
          label: t("menubar.about", "Giới thiệu Astra"),
          action: () => {
            alert("Astra Desktop Coworker v0.1.0\nNền tảng trợ lý AI đa tác vụ.");
          },
        },
      ],
    },
  };

  const handleTriggerClick = (key: MenuKey) => {
    setOpenMenu((prev) => (prev === key ? null : key));
  };

  const handleTriggerHover = (key: MenuKey) => {
    // If ANY menu is already open, hovering over another button switches to it instantly!
    if (openMenu !== null && openMenu !== key) {
      setOpenMenu(key);
    }
  };

  const menuKeys: MenuKey[] = ["file", "edit", "view", "window", "help"];

  return (
    <div
      ref={barRef}
      className={"relative flex items-center gap-0.5 text-[12.5px] font-normal text-muted select-none whitespace-nowrap " + className}
      style={{ WebkitAppRegion: "no-drag" } as any}
    >
      {menuKeys.map((key) => {
        const isOpen = openMenu === key;
        const menu = menus[key];
        return (
          <div key={key} className="relative">
            <button
              type="button"
              onClick={() => handleTriggerClick(key)}
              onMouseEnter={() => handleTriggerHover(key)}
              className={
                "px-2.5 py-1 rounded-md transition-colors cursor-pointer whitespace-nowrap " +
                (isOpen
                  ? "bg-chromeHover text-ink font-medium"
                  : "hover:bg-chromeHover/70 hover:text-ink")
              }
            >
              {menu.title}
            </button>

            {isOpen && (
              <div
                className="absolute top-full left-0 mt-1 min-w-[210px] rounded-xl border border-line bg-panel shadow-xl py-1 z-[100] animate-in fade-in zoom-in-95 duration-75 text-ink"
                onPointerDown={(e) => e.stopPropagation()}
              >
                {menu.items.map((item, idx) => {
                  if (item.separator) {
                    return <div key={idx} className="my-1 border-t border-line" />;
                  }
                  return (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => {
                        setOpenMenu(null);
                        item.action?.();
                      }}
                      className="w-full px-3 py-1.5 flex items-center justify-between text-left text-[12.5px] text-ink hover:bg-chromeHover transition-colors cursor-pointer"
                    >
                      <span className="whitespace-nowrap">{item.label}</span>
                      {item.shortcut && (
                        <span className="ml-4 text-[11px] font-mono text-faint tracking-wider shrink-0">
                          {item.shortcut}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
