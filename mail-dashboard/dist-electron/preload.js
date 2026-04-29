"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
/**
 * Context-isolated IPC bridge exposed to the renderer as `window.electron`.
 * The renderer never has direct access to Node.js APIs.
 */
electron_1.contextBridge.exposeInMainWorld("electron", {
    // ── Window controls ────────────────────────────────────────────────────────
    minimize: () => electron_1.ipcRenderer.invoke("window:minimize"),
    maximize: () => electron_1.ipcRenderer.invoke("window:maximize"),
    hide: () => electron_1.ipcRenderer.invoke("window:hide"),
    // ── Agent controls ─────────────────────────────────────────────────────────
    runNow: () => electron_1.ipcRenderer.invoke("agent:run"),
    getAgentUrl: () => electron_1.ipcRenderer.invoke("agent:get-url"),
    // ── Events from main → renderer ────────────────────────────────────────────
    onRunTriggered: (cb) => {
        electron_1.ipcRenderer.on("agent:run-triggered", (_event, data) => cb(data));
    },
    removeRunTriggeredListener: () => {
        electron_1.ipcRenderer.removeAllListeners("agent:run-triggered");
    },
});
