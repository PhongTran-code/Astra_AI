import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// `base: "./"` makes built asset URLs relative, so the bundle loads from the `tauri://`
// origin in the desktop shell (absolute `/assets` 404s there); a server-hosted build is
// unaffected. Dev runs on a fixed port (1420) with strictPort so the Tauri webview always
// loads the vite instance Tauri itself spawns (a drifting port would make the window load a
// stale/other server). `tauri.conf.json` devUrl must match this.
export default defineConfig(({ command }) => {
  let devToken = "";
  if (command === "serve") {
    const state =
      process.env.COWORKER_STATE_DIR ||
      (process.platform === "win32"
        ? path.join(process.env.APPDATA || os.homedir(), "coworker")
        : path.join(os.homedir(), ".config", "coworker"));
    try {
      devToken = fs.readFileSync(path.join(state, "sidecar-8765.token"), "utf8").trim();
    } catch {
      // The Tauri dev shell injects its in-memory token at runtime. Plain browser dev
      // shows the normal startup retry until the standalone server/token file exists.
    }
  }
  return {
    root: __dirname,
    base: "./",
    plugins: [react()],
    server: {
      port: 1420,
      strictPort: true,
      watch: {
        ignored: [
          "**/src-tauri/**",
          "**/target/**",
          "**/.venv/**",
          "**/dist/**",
          path.resolve(__dirname, "../../.venv/**"),
          path.resolve(__dirname, "../src-tauri/**"),
        ],
      },
    },
    define: { __COWORKER_DEV_TOKEN__: JSON.stringify(devToken) },
    build: {
      sourcemap: false,
      minify: "esbuild",
    },
    esbuild: {
      drop: command === "build" ? ["console", "debugger"] : [],
      legalComments: "none",
    },
    // Tauri CLI looks for these; harmless for the browser build.
    clearScreen: false,
    envPrefix: ["VITE_", "TAURI_"],
  };
});
