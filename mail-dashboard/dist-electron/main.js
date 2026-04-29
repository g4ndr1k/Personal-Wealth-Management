"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
const path = __importStar(require("path"));
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
// ── Constants ─────────────────────────────────────────────────────────────────
const AGENT_URL = "http://127.0.0.1:8080";
const DEV_URL = "http://localhost:5174";
const PLIST_ID = "com.mailagent.dashboard";
const PLIST_PATH = path.join(os.homedir(), "Library/LaunchAgents", `${PLIST_ID}.plist`);
const isDev = !electron_1.app.isPackaged;
// ── State ─────────────────────────────────────────────────────────────────────
let win = null;
let tray = null;
// ── launchd plist ─────────────────────────────────────────────────────────────
function installLaunchdPlist() {
    if (process.platform !== "darwin")
        return;
    if (fs.existsSync(PLIST_PATH))
        return;
    const execPath = process.execPath;
    const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_ID}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${execPath}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>${os.homedir()}/Library/Logs/MailDashboard/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${os.homedir()}/Library/Logs/MailDashboard/stderr.log</string>
</dict>
</plist>
`;
    try {
        const laDir = path.dirname(PLIST_PATH);
        if (!fs.existsSync(laDir))
            fs.mkdirSync(laDir, { recursive: true });
        const logDir = path.join(os.homedir(), "Library/Logs/MailDashboard");
        if (!fs.existsSync(logDir))
            fs.mkdirSync(logDir, { recursive: true });
        fs.writeFileSync(PLIST_PATH, plist, { mode: 0o644 });
        console.log("[main] launchd plist installed:", PLIST_PATH);
    }
    catch (err) {
        console.error("[main] Failed to install launchd plist:", err);
    }
}
// ── Window ────────────────────────────────────────────────────────────────────
function createWindow() {
    const w = new electron_1.BrowserWindow({
        width: 1280,
        height: 800,
        minWidth: 900,
        minHeight: 600,
        titleBarStyle: "hiddenInset",
        vibrancy: "under-window",
        visualEffectState: "active",
        backgroundColor: "#1a1b1e",
        show: false,
        webPreferences: {
            preload: path.join(__dirname, "preload.js"),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
        },
    });
    if (isDev) {
        w.loadURL(DEV_URL);
        w.webContents.openDevTools({ mode: "detach" });
    }
    else {
        w.loadFile(path.join(__dirname, "../dist/index.html"));
    }
    w.once("ready-to-show", () => w.show());
    w.on("close", (e) => {
        // Hide to tray instead of closing
        e.preventDefault();
        w.hide();
    });
    return w;
}
// ── Tray ──────────────────────────────────────────────────────────────────────
function buildTrayMenu(paused) {
    return electron_1.Menu.buildFromTemplate([
        {
            label: "Open Dashboard",
            accelerator: "CmdOrCtrl+Shift+M",
            click: () => showWindow(),
        },
        { type: "separator" },
        {
            label: "Run Now",
            click: () => triggerRun(),
        },
        {
            label: paused ? "Resume" : "Pause",
            click: () => togglePause(!paused),
        },
        { type: "separator" },
        {
            label: "Open Log Folder",
            click: () => electron_1.shell.openPath(path.join(os.homedir(), "Library/Logs/MailDashboard")),
        },
        { type: "separator" },
        {
            label: "Quit",
            accelerator: "CmdOrCtrl+Q",
            click: () => {
                electron_1.app.exit(0);
            },
        },
    ]);
}
let _paused = false;
function togglePause(paused) {
    _paused = paused;
    tray?.setContextMenu(buildTrayMenu(_paused));
}
function createTray() {
    const iconPath = path.join(__dirname, "../assets/iconTemplate.png");
    let icon;
    if (fs.existsSync(iconPath)) {
        icon = electron_1.nativeImage.createFromPath(iconPath);
        icon.setTemplateImage(true);
    }
    else {
        // Fallback: 16×16 white square encoded as base64 PNG
        icon = electron_1.nativeImage.createFromDataURL("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHklEQVQ4T2NkoBAwUqifYdSAUQNGDRg1gCIDAAAIEAABhk2pRAAAAABJRU5ErkJggg==");
        icon.setTemplateImage(true);
    }
    const t = new electron_1.Tray(icon);
    t.setToolTip("Email Intelligence Hub");
    t.setContextMenu(buildTrayMenu(_paused));
    t.on("click", () => {
        if (win?.isVisible()) {
            win.hide();
        }
        else {
            showWindow();
        }
    });
    return t;
}
// ── Helpers ───────────────────────────────────────────────────────────────────
function showWindow() {
    if (!win || win.isDestroyed())
        win = createWindow();
    win.show();
    win.focus();
}
async function triggerRun() {
    const apiKey = process.env["FINANCE_API_KEY"] ?? "";
    try {
        const resp = await fetch(`${AGENT_URL}/api/mail/run`, {
            method: "POST",
            headers: apiKey ? { "X-Api-Key": apiKey } : {},
        });
        const body = await resp.json();
        console.log("[main] triggerRun →", body);
        // Notify renderer so it can refresh
        win?.webContents.send("agent:run-triggered", body);
    }
    catch (err) {
        console.error("[main] triggerRun failed:", err);
    }
}
// ── IPC handlers ──────────────────────────────────────────────────────────────
electron_1.ipcMain.handle("window:minimize", () => win?.minimize());
electron_1.ipcMain.handle("window:maximize", () => win?.isMaximized() ? win.unmaximize() : win?.maximize());
electron_1.ipcMain.handle("window:hide", () => win?.hide());
electron_1.ipcMain.handle("agent:run", async () => {
    await triggerRun();
    return { ok: true };
});
electron_1.ipcMain.handle("agent:get-url", () => AGENT_URL);
// ── App lifecycle ─────────────────────────────────────────────────────────────
electron_1.app.whenReady().then(() => {
    // macOS: stay in tray only, no Dock icon
    if (process.platform === "darwin") {
        electron_1.app.dock?.hide();
    }
    installLaunchdPlist();
    tray = createTray();
    win = createWindow();
    electron_1.app.on("activate", () => showWindow());
});
// Prevent default quit behaviour — only explicit "Quit" menu item exits
electron_1.app.on("window-all-closed", () => {
    // Do nothing; keep running in tray
});
electron_1.app.on("before-quit", () => {
    // Allow the window to actually close on quit
    if (win) {
        win.removeAllListeners("close");
        win.close();
    }
});
