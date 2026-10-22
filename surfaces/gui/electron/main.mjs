import path from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, ipcMain, Menu, shell, dialog } from "electron";
import { spawn } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import crypto from "node:crypto";
import os from "node:os";
import { createBrowserPanel } from "./browser-panel.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let mainWindow = null;
let sidecarProcess = null;
const SIDECAR_PORT = 8765;
const SIDECAR_HOST = "127.0.0.1";

function getOrCreateApiToken() {
  if (process.env.COWORKER_API_TOKEN) {
    return process.env.COWORKER_API_TOKEN;
  }
  const tokenPath = path.join(
    process.env.APPDATA || (process.platform === "darwin" ? path.join(os.homedir(), "Library/Application Support/coworker") : path.join(os.homedir(), ".config/coworker")),
    "sidecar-8765.token"
  );
  try {
    if (fs.existsSync(tokenPath)) {
      const existing = fs.readFileSync(tokenPath, "utf8").trim();
      if (existing) return existing;
    }
  } catch {}
  const newToken = crypto.randomBytes(32).toString("hex");
  try {
    fs.mkdirSync(path.dirname(tokenPath), { recursive: true });
    fs.writeFileSync(tokenPath, newToken, "utf8");
  } catch {}
  return newToken;
}

const apiToken = getOrCreateApiToken();

function isServerHealthy(host, port) {
  return new Promise((resolve) => {
    const req = http.get({ host, port, path: "/v1/health", timeout: 1000 }, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function ensureBackendServer() {
  const healthy = await isServerHealthy(SIDECAR_HOST, SIDECAR_PORT);
  if (healthy) {
    console.log(`[astra-electron] Sidecar already active on ${SIDECAR_HOST}:${SIDECAR_PORT}`);
    return;
  }

  const rootDir = path.resolve(__dirname, "../../..");
  const sidecarBinary = path.resolve(__dirname, "../src-tauri/binaries/sidecar/astra-server");
  const pythonBin = path.resolve(rootDir, ".venv/bin/python");

  if (fs.existsSync(pythonBin)) {
    console.log(`[astra-electron] Spawning python sidecar: ${pythonBin}`);
    sidecarProcess = spawn(pythonBin, ["-m", "coworker.server.run", "--host", SIDECAR_HOST, "--port", String(SIDECAR_PORT)], {
      cwd: rootDir,
      stdio: "inherit",
      env: { ...process.env, COWORKER_API_TOKEN: apiToken, COWORKER_EXIT_WITH_PARENT: "1" },
    });
  } else if (fs.existsSync(sidecarBinary)) {
    console.log(`[astra-electron] Spawning packaged sidecar: ${sidecarBinary}`);
    sidecarProcess = spawn(sidecarBinary, ["--host", SIDECAR_HOST, "--port", String(SIDECAR_PORT)], {
      cwd: rootDir,
      stdio: "inherit",
      env: { ...process.env, COWORKER_API_TOKEN: apiToken, COWORKER_EXIT_WITH_PARENT: "1" },
    });
  }

  // Poll for up to 10s until health passes
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await isServerHealthy(SIDECAR_HOST, SIDECAR_PORT)) {
      console.log("[astra-electron] Sidecar health check passed!");
      return;
    }
  }
}

async function createWindow() {
  await ensureBackendServer();

  mainWindow = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 900,
    minHeight: 600,
    title: "Astra",
    icon: path.join(__dirname, "../assets/icon.png"),
    ...(process.platform === "darwin" ? { titleBarStyle: "hiddenInset" } : { frame: false }),
    autoHideMenuBar: true,
    backgroundColor: "#ffffff",
    webPreferences: {
      preload: path.join(__dirname, "preload.mjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
      additionalArguments: [`--api-token=${apiToken}`],
    },
  });
  mainWindow.setMenuBarVisibility(false);

  if (app.isPackaged) {
    mainWindow.webContents.on("devtools-opened", () => {
      mainWindow.webContents.closeDevTools();
    });
  }

  const browserPanel = createBrowserPanel({
    getWindow: () => mainWindow,
    remoteDebugPort: 9222,
  });
  browserPanel.registerIpc(ipcMain);

  ipcMain.handle("openwork:window:setMenuBarVisibility", (_event, visible) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.setMenuBarVisibility(Boolean(visible));
    mainWindow.setAutoHideMenuBar(!visible);
  });
  ipcMain.handle("openwork:window:getMenuBarVisibility", () => {
    return mainWindow && !mainWindow.isDestroyed() ? mainWindow.isMenuBarVisible() : false;
  });

  ipcMain.handle("openwork:window:showMenu", (_event, { menuType, x, y }) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const roundedX = Math.round(x);
    const roundedY = Math.round(y);
    let template = [];
    if (menuType === "file") {
      template = [
        { label: "Đoạn chat mới", accelerator: "CmdOrCtrl+N", click: () => mainWindow?.webContents.send("openwork:menu:new-session") },
        { type: "separator" },
        { label: "Cài đặt", accelerator: "CmdOrCtrl+,", click: () => mainWindow?.webContents.send("openwork:menu:settings") },
        { type: "separator" },
        { label: "Đóng cửa sổ", accelerator: "CmdOrCtrl+W", click: () => mainWindow?.close() },
        { label: "Thoát Astra", accelerator: "CmdOrCtrl+Q", click: () => app.quit() },
      ];
    } else if (menuType === "edit") {
      template = [
        { role: "undo", label: "Hoàn tác" },
        { role: "redo", label: "Làm lại" },
        { type: "separator" },
        { role: "cut", label: "Cắt" },
        { role: "copy", label: "Sao chép" },
        { role: "paste", label: "Dán" },
        { role: "selectAll", label: "Chọn tất cả" },
      ];
    } else if (menuType === "view") {
      template = [
        { role: "reload", label: "Tải lại" },
        { role: "forceReload", label: "Tải lại bắt buộc" },
        { role: "toggleDevTools", label: "Công cụ nhà phát triển" },
        { type: "separator" },
        { role: "resetZoom", label: "Kích thước thực tế" },
        { role: "zoomIn", label: "Phóng to" },
        { role: "zoomOut", label: "Thu nhỏ" },
        { type: "separator" },
        { role: "togglefullscreen", label: "Toàn màn hình" },
      ];
    } else if (menuType === "window") {
      template = [
        { role: "minimize", label: "Thu nhỏ cửa sổ" },
        { role: "zoom", label: "Phóng to cửa sổ" },
        { type: "separator" },
        { role: "close", label: "Đóng cửa sổ" },
      ];
    } else if (menuType === "help") {
      template = [
        { label: "Tài liệu Astra", click: () => shell.openExternal("https://github.com/PhongTran-code/Astra_AI") },
        { type: "separator" },
        { label: "Giới thiệu về Astra", click: () => {
          if (!mainWindow) return;
          dialog.showMessageBox(mainWindow, {
            type: "info",
            title: "Astra",
            message: "Astra Coworker Desktop",
            detail: "Trợ lý AI Coworker nền tảng đa tác vụ.",
          });
        }},
      ];
    }
    const menu = Menu.buildFromTemplate(template);
    menu.popup({
      window: mainWindow,
      x: roundedX,
      y: roundedY,
    });
  });

  ipcMain.handle("openwork:window:minimize", () => mainWindow?.minimize());
  ipcMain.handle("openwork:window:maximize", () => {
    if (!mainWindow) return;
    mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
  });
  ipcMain.handle("openwork:window:isMaximized", () => {
    return mainWindow && !mainWindow.isDestroyed() ? mainWindow.isMaximized() : false;
  });
  ipcMain.handle("openwork:window:close", () => mainWindow?.close());
  ipcMain.handle("openwork:window:toggleDevTools", () => mainWindow?.webContents.toggleDevTools());

  mainWindow.on("closed", () => {
    browserPanel.destroy();
    mainWindow = null;
  });

  // Check if Vite dev server is running on 1420 or 5173
  const isVite1420 = await new Promise((resolve) => {
    http.get("http://localhost:1420", () => resolve(true)).on("error", () => resolve(false));
  });

  if (isVite1420) {
    console.log("[astra-electron] Loading from Vite dev server http://localhost:1420");
    await mainWindow.loadURL("http://localhost:1420");
  } else {
    const distIndex = path.resolve(__dirname, "../dist/index.html");
    if (fs.existsSync(distIndex)) {
      console.log(`[astra-electron] Loading built files from ${distIndex}`);
      await mainWindow.loadFile(distIndex);
    } else {
      console.log("[astra-electron] Loading fallback http://localhost:1420");
      await mainWindow.loadURL("http://localhost:1420");
    }
  }
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  if (sidecarProcess) {
    try {
      sidecarProcess.kill();
    } catch {
      // already terminated
    }
  }
});
