import { contextBridge, ipcRenderer } from "electron";

const tokenArg = process.argv.find((arg) => arg.startsWith("--api-token="));
const apiToken = tokenArg ? tokenArg.split("=")[1] : "";

contextBridge.exposeInMainWorld("__COWORKER_HTTP__", "http://127.0.0.1:8765");
contextBridge.exposeInMainWorld("__COWORKER_WS__", "ws://127.0.0.1:8765");
contextBridge.exposeInMainWorld("__COWORKER_API_TOKEN__", apiToken);

contextBridge.exposeInMainWorld("desktop", {
  isElectron: true,
  setMenuBarVisibility: (visible) => ipcRenderer.invoke("openwork:window:setMenuBarVisibility", visible),
  getMenuBarVisibility: () => ipcRenderer.invoke("openwork:window:getMenuBarVisibility"),
  showMenu: (menuType, coords) => ipcRenderer.invoke("openwork:window:showMenu", { menuType, ...coords }),
  minimize: () => ipcRenderer.invoke("openwork:window:minimize"),
  maximize: () => ipcRenderer.invoke("openwork:window:maximize"),
  isMaximized: () => ipcRenderer.invoke("openwork:window:isMaximized"),
  close: () => ipcRenderer.invoke("openwork:window:close"),
  toggleDevTools: () => ipcRenderer.invoke("openwork:window:toggleDevTools"),
  browser: {
    show: (bounds) => ipcRenderer.invoke("openwork:browser:show", bounds),
    hide: () => ipcRenderer.invoke("openwork:browser:hide"),
    openUrl: (url, provider) => ipcRenderer.invoke("openwork:browser:openUrl", url, provider),
    openInChrome: (url) => ipcRenderer.invoke("openwork:browser:openInChrome", url),
    navigate: (url) => ipcRenderer.invoke("openwork:browser:navigate", url),
    back: () => ipcRenderer.invoke("openwork:browser:back"),
    forward: () => ipcRenderer.invoke("openwork:browser:forward"),
    reload: () => ipcRenderer.invoke("openwork:browser:reload"),
    setBounds: (bounds) => ipcRenderer.invoke("openwork:browser:bounds", bounds),
    getState: () => ipcRenderer.invoke("openwork:browser:state"),
    createTab: (url) => ipcRenderer.invoke("openwork:browser:createTab", url),
    closeTab: (tabId) => ipcRenderer.invoke("openwork:browser:closeTab", tabId),
    closeAllTabs: () => ipcRenderer.invoke("openwork:browser:closeAllTabs"),
    selectTab: (tabId) => ipcRenderer.invoke("openwork:browser:selectTab", tabId),
    reorderTabs: (tabIds) => ipcRenderer.invoke("openwork:browser:reorderTabs", tabIds),
    listTabs: () => ipcRenderer.invoke("openwork:browser:listTabs"),
    setProxy: (proxy) => ipcRenderer.invoke("openwork:browser:setProxy", proxy),
    getProxy: () => ipcRenderer.invoke("openwork:browser:getProxy"),
    showTabContextMenu: (tabId, point) => ipcRenderer.invoke("openwork:browser:tabContextMenu", tabId, point),
    destroy: () => ipcRenderer.invoke("openwork:browser:destroy"),
    onState: (handler) => {
      const listener = (_event, state) => handler(state);
      ipcRenderer.on("openwork:browser:state", listener);
      return () => ipcRenderer.removeListener("openwork:browser:state", listener);
    },
    onPanelOpened: (handler) => {
      const listener = () => handler();
      ipcRenderer.on("openwork:browser:panel-opened", listener);
      return () => ipcRenderer.removeListener("openwork:browser:panel-opened", listener);
    },
    onPanelClosed: (handler) => {
      const listener = () => handler();
      ipcRenderer.on("openwork:browser:panel-closed", listener);
      return () => ipcRenderer.removeListener("openwork:browser:panel-closed", listener);
    },
  },
});
