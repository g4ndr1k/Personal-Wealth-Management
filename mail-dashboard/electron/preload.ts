import { contextBridge, ipcRenderer } from "electron";

/**
 * Context-isolated IPC bridge exposed to the renderer as `window.electron`.
 * The renderer never has direct access to Node.js APIs.
 */
contextBridge.exposeInMainWorld("electron", {
  // ── Window controls ────────────────────────────────────────────────────────
  minimize: () => ipcRenderer.invoke("window:minimize"),
  maximize: () => ipcRenderer.invoke("window:maximize"),
  hide:     () => ipcRenderer.invoke("window:hide"),

  // ── Agent controls ─────────────────────────────────────────────────────────
  runNow:   () => ipcRenderer.invoke("agent:run"),
  getAgentUrl: (): Promise<string> => ipcRenderer.invoke("agent:get-url"),

  // ── Events from main → renderer ────────────────────────────────────────────
  onRunTriggered: (cb: (data: unknown) => void) => {
    ipcRenderer.on("agent:run-triggered", (_event, data) => cb(data));
  },
  removeRunTriggeredListener: () => {
    ipcRenderer.removeAllListeners("agent:run-triggered");
  },
});
