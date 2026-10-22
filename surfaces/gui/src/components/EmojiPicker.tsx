import { useState, useMemo, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";

export interface EmojiCategory {
  id: string;
  name: string;
  icon: string;
  emojis: string[];
}

export const EMOJI_CATEGORIES: EmojiCategory[] = [
  {
    id: "smileys",
    name: "Smileys & Emotion",
    icon: "😀",
    emojis: [
      "😀", "😃", "😄", "😁", "😆", "😅", "😂", "🤣", "🥹", "😊", "😇", "🙂", "🙃", "😉", "😌", "😍",
      "🥰", "😘", "😗", "😙", "😚", "😋", "😛", "😝", "😜", "🤪", "🤨", "🧐", "🤓", "😎", "🥸", "🤩",
      "🥳", "😏", "😒", "😞", "😔", "😟", "😕", "🙁", "☹️", "😣", "😖", "😫", "😩", "🥺", "😢", "😭",
      "😮‍💨", "😤", "😠", "😡", "🤬", "🤯", "😳", "🥵", "🥶", "😱", "😨", "😰", "😥", "😓", "🤗", "🤔",
      "🫣", "🤭", "🫢", "🫡", "🤫", "🫠", "🤥", "😶", "😶‍🌫️", "😐", "😑", "😬", "🫨", "😯", "😦", "😧",
      "😮", "😲", "🥱", "😴", "🤤", "😪", "😵", "😵‍💫", "🫥", "🤐", "🥴", "🤢", "🤮", "🤧", "😷", "🤒",
    ],
  },
  {
    id: "gestures",
    name: "People & Hands",
    icon: "👋",
    emojis: [
      "👋", "🤚", "🖐️", "✋", "🖖", "🫱", "🫲", "🫳", "🫴", "🫷", "🫸", "👌", "🤌", "🤏", "✌️", "🤞",
      "🫰", "🤟", "🤘", "🤙", "👈", "👉", "👆", "🖕", "👇", "☝️", "🫵", "👍", "👎", "✊", "👊", "🤛",
      "🤜", "👏", "🙌", "🫶", "👐", "🤲", "🤝", "🙏", "✍️", "💅", "🤳", "💪", "🦾", "🦿", "🦵", "🦶",
      "👂", "🦻", "👃", "🫀", "🫁", "🧠", "🫀", "🫁", "👀", "👁️", "👅", "👄", "🫦", "👶", "👧", "🧒",
      "👦", "👩", "🧑", "👨", "👩‍🦱", "🧑‍🦱", "👨‍🦱", "👩‍🦰", "🧑‍🦰", "👨‍🦰", "👱‍♀️", "👱", "👱‍♂️", "👩‍🦳", "🧑‍🦳", "👨‍🦳",
    ],
  },
  {
    id: "animals",
    name: "Animals & Nature",
    icon: "🐶",
    emojis: [
      "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐻‍❄️", "🐨", "🐯", "🦁", "🐮", "🐷", "🐽", "🐸",
      "🐵", "🙈", "🙉", "🙊", "🐒", "🐔", "🐧", "🐦", "🐤", "🐣", "🐥", "🦆", "🦅", "🦉", "🦇", "🐺",
      "🐗", "🐴", "🦄", "🐝", "🪱", "🐛", "🦋", "🐌", "🐞", "🐜", "🪰", "🪲", "🪳", "🪴", "🌲", "🌳",
      "🌴", "🌵", "🌾", "🌿", "☘️", "🍀", "🍁", "🍂", "🍃", "🍄", "🪨", "🌺", "🌸", "🌼", "🌻", "🌞",
    ],
  },
  {
    id: "food",
    name: "Food & Drink",
    icon: "🍔",
    emojis: [
      "🍏", "🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓", "🫐", "🍈", "🍒", "🍑", "🥭", "🍍", "🥥",
      "🥝", "🍅", "🍆", "🥑", "🥦", "🥬", "🥒", "🌶️", "🫑", "🌽", "🥕", "🫒", "🧄", "🧅", "🥔", "🍠",
      "🥐", "🥯", "🍞", "🥖", "🥨", "🧀", "🥚", "🍳", "🧈", "🥞", "🧇", "🥓", "🥩", "🍗", "🍖", "🦴",
      "🌭", "🍔", "🍟", "🍕", "🫓", "🥪", "🥙", "🧆", "🌮", "🌯", "🫔", "🥗", "🥘", "🫕", "🍲", "🫙",
      "🍜", "🍝", "🍣", "🍱", "🥟", "🦪", "🍤", "🍙", "🍚", "🍘", "🍥", "🍢", "🥮", "🍧", "🍨", "🍦",
      "☕", "🫖", "🍵", "🧃", "🥤", "🧋", "🍶", "🍺", "🍻", "🥂", "🍷", "🥃", "🍸", "🍹", "🧉", "🍾",
    ],
  },
  {
    id: "activities",
    name: "Activities & Objects",
    icon: "⚽",
    emojis: [
      "⚽", "🏀", "🏈", "⚾", "🥎", "🎾", "🏐", "🏉", "🥏", "🎱", "🪀", "🏓", "🏸", "🏒", "🏑", "🥍",
      "🏏", "🪃", "🥅", "⛳", "🪁", "🏹", "🎣", "🤿", "🥊", "🥋", "🎽", "🛹", "🛼", "🛷", "⛸️", "🥌",
      "🎯", "🎮", "🕹️", "🎲", "🧩", "♟️", "🎭", "🎨", "🧵", "🪡", "🧶", "🎹", "🥁", "🪘", "🎷", "🎺",
      "🎸", "🪕", "🎻", "📱", "💻", "⌨️", "🖥️", "🖨️", "🖱️", "💽", "💾", "💿", "📀", "📷", "📸", "📹",
      "💡", "🔦", "🕯️", "🪔", "📦", "📫", "📬", "📮", "📝", "📁", "📂", "📅", "📊", "📈", "📉", "📌",
    ],
  },
  {
    id: "symbols",
    name: "Symbols & Flags",
    icon: "💡",
    emojis: [
      "❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎", "💔", "❤️‍🔥", "❤️‍🩹", "❣️", "💕", "💞", "💓",
      "💗", "💖", "💘", "💝", "💟", "☮️", "✝️", "☪️", "🕉️", "☸️", "✡️", "🔯", "🕎", "☯️", "☦️", "🛐",
      "⛎", "♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓", "🆔", "⚛️", "🉑",
      "🔥", "✨", "🌟", "💫", "⭐", "🎉", "🎊", "🎈", "🎁", "🏆", "🥇", "🥈", "🥉", "🏅", "🎖️", "🚀",
      "💯", "✅", "❌", "⚠️", "⛔", "🚫", "💤", "💬", "💭", "🗯️", "🔔", "🔕", "📢", "📣", "⚡", "🌈",
    ],
  },
];

interface EmojiPickerProps {
  onSelect: (emoji: string) => void;
  onClose: () => void;
}

export function EmojiPicker({ onSelect, onClose }: EmojiPickerProps) {
  const { t } = useTranslation();
  const [activeCategory, setActiveCategory] = useState<string>("smileys");
  const [search, setSearch] = useState<string>("");
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    searchInputRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [onClose]);

  const filteredCategories = useMemo(() => {
    if (!search.trim()) return EMOJI_CATEGORIES;
    const q = search.toLowerCase();
    return EMOJI_CATEGORIES.map((cat) => ({
      ...cat,
      emojis: cat.emojis.filter((emoji) => {
        // Match category name or common emoji searches
        return cat.name.toLowerCase().includes(q) || cat.id.includes(q) || emoji.includes(q);
      }),
    })).filter((cat) => cat.emojis.length > 0);
  }, [search]);

  return (
    <div
      ref={popoverRef}
      className="absolute bottom-full left-0 mb-2 z-50 w-[320px] max-h-[380px] rounded-xl border border-line bg-panel shadow-2xl flex flex-col overflow-hidden text-ink animate-in fade-in zoom-in-95 duration-100"
      role="dialog"
      aria-label="Emoji picker"
    >
      {/* Header with Search */}
      <div className="p-2.5 pb-2 border-b border-line">
        <div className="relative flex items-center">
          <input
            ref={searchInputRef}
            type="text"
            className="w-full h-8 pl-8 pr-3 rounded-lg border border-line bg-paper text-[13px] text-ink placeholder:text-muted/60 outline-none transition-colors focus:border-accent"
            placeholder={t("team_surface.search_emoji", "Search emoji…")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <span className="absolute left-2.5 text-muted pointer-events-none">
            <Icon name="search" size={13} />
          </span>
          {search && (
            <button
              type="button"
              className="absolute right-2 text-muted hover:text-ink"
              onClick={() => setSearch("")}
            >
              <Icon name="x" size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Emoji Scroll Area */}
      <div className="flex-1 overflow-y-auto p-2.5 space-y-3.5 max-h-[260px] hairline-scroll">
        {filteredCategories.length === 0 ? (
          <div className="py-8 text-center text-[12px] text-muted">
            {t("team_surface.no_emoji_found", "No emoji found")}
          </div>
        ) : (
          filteredCategories.map((cat) => (
            <div key={cat.id} id={`emoji-cat-${cat.id}`}>
              <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5 px-1">
                {cat.name}
              </div>
              <div className="grid grid-cols-8 gap-1">
                {cat.emojis.map((emoji, index) => (
                  <button
                    key={`${emoji}-${index}`}
                    type="button"
                    className="h-8 w-8 rounded-lg hover:bg-paper flex items-center justify-center text-[18px] transition-transform hover:scale-125 focus:outline-none focus:bg-paper"
                    onClick={() => {
                      onSelect(emoji);
                      onClose();
                    }}
                    title={emoji}
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Category Tabs Footer */}
      {!search && (
        <div className="flex items-center justify-between border-t border-line px-2 py-1.5 bg-paper/50">
          {EMOJI_CATEGORIES.map((cat) => {
            const isActive = activeCategory === cat.id;
            return (
              <button
                key={cat.id}
                type="button"
                className={
                  "h-7 w-7 rounded-md flex items-center justify-center text-[14px] transition-colors " +
                  (isActive ? "bg-accent/15 text-accent" : "hover:bg-paper text-muted hover:text-ink")
                }
                onClick={() => {
                  setActiveCategory(cat.id);
                  const el = document.getElementById(`emoji-cat-${cat.id}`);
                  el?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
                title={cat.name}
              >
                <span>{cat.icon}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
