import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";
import type { Option } from "./Dropdown";

export const MODEL_FAVORITES_KEY = "astra.model-favorites";
const MODEL_LIST_MAX_HEIGHT = 320;

function readFavorites(): string[] {
  try {
    const raw = window.localStorage.getItem(MODEL_FAVORITES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === "string");
  } catch {
    return [];
  }
}

function writeFavorites(favorites: string[]) {
  try {
    window.localStorage.setItem(MODEL_FAVORITES_KEY, JSON.stringify(favorites));
  } catch {
    // localStorage can be unavailable in a restricted browser context.
  }
}

interface Props {
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  align?: "left" | "right";
}

export function ModelPicker({ value, options, onChange, align = "left" }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [favoriteIds, setFavoriteIds] = useState<string[]>(readFavorites);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const current = options.find((option) => option.value === value);
  const label = current?.label || value;

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => searchRef.current?.focus());
  }, [open]);

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  const toggleFavorite = (id: string) => {
    setFavoriteIds((currentFavorites) => {
      const next = currentFavorites.includes(id)
        ? currentFavorites.filter((favorite) => favorite !== id)
        : [id, ...currentFavorites];
      writeFavorites(next);
      return next;
    });
  };

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matches = options.filter((option) => {
    if (!normalizedQuery) return true;
    return `${option.label} ${option.value}`.toLocaleLowerCase().includes(normalizedQuery);
  });
  const favoriteSet = new Set(favoriteIds);
  const favoriteOptions = favoriteIds
    .map((id) => matches.find((option) => option.value === id))
    .filter((option): option is Option => option !== undefined);
  const favoriteValues = new Set(favoriteOptions.map((option) => option.value));
  const otherOptions = matches.filter((option) => !favoriteValues.has(option.value));

  const renderOption = (option: Option) => {
    const favorite = favoriteSet.has(option.value);
    return (
      <div className="model-picker-option" key={option.value}>
        <button
          className={"model-picker-option-main" + (option.value === value ? " selected" : "")}
          onClick={() => {
            onChange(option.value);
            close();
          }}
          title={option.value}
          role="option"
          aria-selected={option.value === value}
        >
          <span className="model-picker-option-label">{option.label}</span>
          {option.value === value && <Icon name="check" size={14} className="model-picker-check" />}
        </button>
        <button
          className={"model-picker-favorite" + (favorite ? " active" : "")}
          onClick={(event) => {
            event.stopPropagation();
            toggleFavorite(option.value);
          }}
          title={favorite ? t("composer.model.unfavorite") : t("composer.model.favorite")}
          aria-label={favorite ? t("composer.model.unfavorite") : t("composer.model.favorite")}
          aria-pressed={favorite}
        >
          <Icon name="star" size={15} className={favorite ? "fill-current" : ""} />
        </button>
      </div>
    );
  };

  return (
    <div className="dd model-picker">
      <button
        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[12px] font-medium text-muted hover:text-ink hover:bg-paper/70 transition-colors cursor-pointer"
        onClick={() => {
          if (open) close();
          else setOpen(true);
        }}
        title={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={t("composer.model.choose")}
      >
        <span className="pill-label font-medium text-ink">{label}</span>
        <Icon name="chevronDown" size={11} className="caret text-faint ml-0.5 shrink-0" />
      </button>
      {open && (
        <>
          <div className="dd-backdrop" onClick={close} />
          <div className={`dd-menu model-picker-menu ${align}`}>
            <label className="model-picker-search">
              <Icon name="search" size={15} className="text-faint shrink-0" />
              <input
                ref={searchRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") close();
                }}
                placeholder={t("composer.model.search")}
                aria-label={t("composer.model.search")}
              />
            </label>
            <div
              className="model-picker-list"
              style={{ maxHeight: MODEL_LIST_MAX_HEIGHT }}
              role="listbox"
              aria-label={t("composer.model.choose")}
            >
              {favoriteOptions.length > 0 && (
                <div className="model-picker-section">
                  <div className="model-picker-section-label">{t("composer.model.favorites")}</div>
                  {favoriteOptions.map(renderOption)}
                </div>
              )}
              {otherOptions.length > 0 && (
                <div className="model-picker-section">
                  {favoriteOptions.length > 0 && (
                    <div className="model-picker-section-label">{t("composer.model.all")}</div>
                  )}
                  {otherOptions.map(renderOption)}
                </div>
              )}
              {matches.length === 0 && (
                <div className="model-picker-empty">{t("composer.model.no_matches")}</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
