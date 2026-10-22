// Appearance: Light / Dark / Auto-as-system. The preference lives in localStorage only —
// it's per-device (like macOS appearance itself) and must apply before the sidecar is even
// reachable. index.html sets data-theme inline pre-paint with the same key, so the first
// frame is already the right color; this module keeps it current from then on.
import { useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "auto";
export type SidebarTint =
  | "default"
  | "oat"
  | "sand"
  | "amber"
  | "peach"
  | "coral"
  | "rose"
  | "lavender"
  | "plum"
  | "indigo"
  | "cobalt"
  | "sky"
  | "cyan"
  | "teal"
  | "mint"
  | "sage"
  | "olive"
  | "moss"
  | "clay"
  | "slate";

export interface SidebarTintOption {
  id: SidebarTint;
  nameKey: string;
  lightBg: string;
  darkBg: string;
  borderLight: string;
  borderDark: string;
}

export const SIDEBAR_TINT_OPTIONS: SidebarTintOption[] = [
  {
    id: "default",
    nameKey: "settings.sidebar_tint_default",
    lightBg: "#f7f8f9",
    darkBg: "#16171b",
    borderLight: "#e8eaed",
    borderDark: "#2a2d33",
  },
  {
    id: "oat",
    nameKey: "settings.sidebar_tint_oat",
    lightBg: "#f7f5f0",
    darkBg: "#1c1a17",
    borderLight: "#e7e2d7",
    borderDark: "#302b24",
  },
  {
    id: "sand",
    nameKey: "settings.sidebar_tint_sand",
    lightBg: "#f9f6ee",
    darkBg: "#1f1b14",
    borderLight: "#eae2cf",
    borderDark: "#352d1f",
  },
  {
    id: "amber",
    nameKey: "settings.sidebar_tint_amber",
    lightBg: "#fcf6ea",
    darkBg: "#221a0f",
    borderLight: "#f0dfbc",
    borderDark: "#3d2d18",
  },
  {
    id: "peach",
    nameKey: "settings.sidebar_tint_peach",
    lightBg: "#fdf4ed",
    darkBg: "#231812",
    borderLight: "#f4dac9",
    borderDark: "#40271c",
  },
  {
    id: "coral",
    nameKey: "settings.sidebar_tint_coral",
    lightBg: "#fdf2f0",
    darkBg: "#241615",
    borderLight: "#f5d3cf",
    borderDark: "#432321",
  },
  {
    id: "rose",
    nameKey: "settings.sidebar_tint_rose",
    lightBg: "#fcf0f4",
    darkBg: "#24151b",
    borderLight: "#f3d0dd",
    borderDark: "#42212e",
  },
  {
    id: "lavender",
    nameKey: "settings.sidebar_tint_lavender",
    lightBg: "#f5f0f9",
    darkBg: "#1e1726",
    borderLight: "#dfd2ed",
    borderDark: "#362647",
  },
  {
    id: "plum",
    nameKey: "settings.sidebar_tint_plum",
    lightBg: "#f4edf7",
    darkBg: "#201524",
    borderLight: "#decce5",
    borderDark: "#3a2243",
  },
  {
    id: "indigo",
    nameKey: "settings.sidebar_tint_indigo",
    lightBg: "#f0f2fb",
    darkBg: "#151828",
    borderLight: "#d2d7f4",
    borderDark: "#232b4b",
  },
  {
    id: "cobalt",
    nameKey: "settings.sidebar_tint_cobalt",
    lightBg: "#eef4fd",
    darkBg: "#121b2b",
    borderLight: "#cddff9",
    borderDark: "#1c3050",
  },
  {
    id: "sky",
    nameKey: "settings.sidebar_tint_sky",
    lightBg: "#eef7fc",
    darkBg: "#111d27",
    borderLight: "#cbe5f5",
    borderDark: "#1a3449",
  },
  {
    id: "cyan",
    nameKey: "settings.sidebar_tint_cyan",
    lightBg: "#eaf8f8",
    darkBg: "#0f2022",
    borderLight: "#c2e9ea",
    borderDark: "#173b3e",
  },
  {
    id: "teal",
    nameKey: "settings.sidebar_tint_teal",
    lightBg: "#ebf7f4",
    darkBg: "#10211d",
    borderLight: "#c5e9df",
    borderDark: "#193c33",
  },
  {
    id: "mint",
    nameKey: "settings.sidebar_tint_mint",
    lightBg: "#ecf8f0",
    darkBg: "#102216",
    borderLight: "#c7ebd2",
    borderDark: "#1a3d24",
  },
  {
    id: "sage",
    nameKey: "settings.sidebar_tint_sage",
    lightBg: "#eef4f0",
    darkBg: "#152019",
    borderLight: "#d4e4d7",
    borderDark: "#25372a",
  },
  {
    id: "olive",
    nameKey: "settings.sidebar_tint_olive",
    lightBg: "#f3f5eb",
    darkBg: "#1c2014",
    borderLight: "#dee4cb",
    borderDark: "#323a20",
  },
  {
    id: "moss",
    nameKey: "settings.sidebar_tint_moss",
    lightBg: "#f0f4ea",
    darkBg: "#182013",
    borderLight: "#d6e2c9",
    borderDark: "#2a3a1f",
  },
  {
    id: "clay",
    nameKey: "settings.sidebar_tint_clay",
    lightBg: "#f8f1eb",
    darkBg: "#221915",
    borderLight: "#e9d9cc",
    borderDark: "#3e2920",
  },
  {
    id: "slate",
    nameKey: "settings.sidebar_tint_slate",
    lightBg: "#eff2f6",
    darkBg: "#16191f",
    borderLight: "#d9e0ea",
    borderDark: "#262b35",
  },
];

const KEY = "openwork-theme";
const TINT_KEY = "openwork-sidebar-tint";
const PREF_EVENT = "openwork:theme-pref";
const TINT_EVENT = "openwork:sidebar-tint-pref";

const media = window.matchMedia?.("(prefers-color-scheme: dark)");

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function getSidebarTint(): SidebarTint {
  try {
    const v = localStorage.getItem(TINT_KEY) as SidebarTint | null;
    return v && SIDEBAR_TINT_OPTIONS.some((o) => o.id === v) ? v : "default";
  } catch {
    return "default";
  }
}

function applyTint(tint: SidebarTint) {
  if (tint === "default") {
    delete document.documentElement.dataset.sidebarTint;
  } else {
    document.documentElement.dataset.sidebarTint = tint;
  }
}

function apply(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "auto" && !!media?.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function setThemePref(pref: ThemePref) {
  try {
    if (pref === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* private mode etc. — still applies for this session */
  }
  apply(pref);
  window.dispatchEvent(new CustomEvent(PREF_EVENT));
}
export function setSidebarTint(tint: SidebarTint) {
  try {
    if (tint === "default") localStorage.removeItem(TINT_KEY);
    else localStorage.setItem(TINT_KEY, tint);
  } catch {
    /* private mode etc. */
  }
  applyTint(tint);
  window.dispatchEvent(new CustomEvent(TINT_EVENT));
}

/** Call once at startup: applies the stored theme/tint and follows macOS appearance while in auto. */
export function initTheme() {
  apply(getThemePref());
  applyTint(getSidebarTint());
  media?.addEventListener("change", () => {
    if (getThemePref() === "auto") apply("auto");
  });
}

/** The settings control's hook — stays in sync if the pref changes elsewhere. */
export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(getThemePref);
  useEffect(() => {
    const sync = () => setPref(getThemePref());
    window.addEventListener(PREF_EVENT, sync);
    return () => window.removeEventListener(PREF_EVENT, sync);
  }, []);
  return [pref, setThemePref];
}

/** Hook for sidebar tint selection. */
export function useSidebarTint(): [SidebarTint, (t: SidebarTint) => void] {
  const [tint, setTint] = useState<SidebarTint>(getSidebarTint);
  useEffect(() => {
    const sync = () => setTint(getSidebarTint());
    window.addEventListener(TINT_EVENT, sync);
    return () => window.removeEventListener(TINT_EVENT, sync);
  }, []);
  return [tint, setSidebarTint];
}
