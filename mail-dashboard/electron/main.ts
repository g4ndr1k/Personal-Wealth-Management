import {
  app,
  BrowserWindow,
  Menu,
  Tray,
  ipcMain,
  nativeImage,
  shell,
} from "electron";
import * as path from "path";
import * as fs from "fs";
import * as os from "os";

// ── Constants ─────────────────────────────────────────────────────────────────

const AGENT_URL   = "http://127.0.0.1:8080";
const DEV_URL     = "http://localhost:5174";
const PLIST_ID    = "com.mailagent.dashboard";
const PLIST_PATH  = path.join(
  os.homedir(),
  "Library/LaunchAgents",
  `${PLIST_ID}.plist`,
);

const isDev = !app.isPackaged;

// ── State ─────────────────────────────────────────────────────────────────────

let win:  BrowserWindow | null = null;
let tray: Tray          | null = null;

// ── launchd plist ─────────────────────────────────────────────────────────────

function installLaunchdPlist(): void {
  if (process.platform !== "darwin") return;
  if (fs.existsSync(PLIST_PATH)) return;

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
    if (!fs.existsSync(laDir)) fs.mkdirSync(laDir, { recursive: true });

    const logDir = path.join(os.homedir(), "Library/Logs/MailDashboard");
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });

    fs.writeFileSync(PLIST_PATH, plist, { mode: 0o644 });
    console.log("[main] launchd plist installed:", PLIST_PATH);
  } catch (err) {
    console.error("[main] Failed to install launchd plist:", err);
  }
}

// ── Window ────────────────────────────────────────────────────────────────────

function createWindow(): BrowserWindow {
  const w = new BrowserWindow({
    width:           1280,
    height:          800,
    minWidth:        900,
    minHeight:       600,
    titleBarStyle:   "hiddenInset",
    vibrancy:        "under-window",
    visualEffectState: "active",
    backgroundColor: "#1a1b1e",
    show:            false,
    webPreferences: {
      preload:          path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration:  false,
      sandbox:          true,
    },
  });

  if (isDev) {
    w.loadURL(DEV_URL);
    w.webContents.openDevTools({ mode: "detach" });
  } else {
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

function buildTrayMenu(paused: boolean): Menu {
  return Menu.buildFromTemplate([
    {
      label:       "Open Dashboard",
      accelerator: "CmdOrCtrl+Shift+M",
      click:       () => showWindow(),
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
      click: () =>
        shell.openPath(
          path.join(os.homedir(), "Library/Logs/MailDashboard"),
        ),
    },
    { type: "separator" },
    {
      label: "Quit",
      accelerator: "CmdOrCtrl+Q",
      click: () => {
        app.exit(0);
      },
    },
  ]);
}

let _paused = false;

function togglePause(paused: boolean): void {
  _paused = paused;
  tray?.setContextMenu(buildTrayMenu(_paused));
}

function createTray(): Tray {
  const iconPath = path.join(__dirname, "../assets/iconTemplate.png");
  let icon: Electron.NativeImage;

  if (fs.existsSync(iconPath)) {
    icon = nativeImage.createFromPath(iconPath);
    icon.setTemplateImage(true);
  } else {
    // Fallback: 16×16 white square encoded as base64 PNG
    icon = nativeImage.createFromDataURL(
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAHklEQVQ4T2NkoBAwUqifYdSAUQNGDRg1gCIDAAAIEAABhk2pRAAAAABJRU5ErkJggg==",
    );
    icon.setTemplateImage(true);
  }

  const t = new Tray(icon);
  t.setToolTip("Email Intelligence Hub");
  t.setContextMenu(buildTrayMenu(_paused));

  t.on("click", () => {
    if (win?.isVisible()) {
      win.hide();
    } else {
      showWindow();
    }
  });

  return t;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function showWindow(): void {
  if (!win || win.isDestroyed()) win = createWindow();
  win.show();
  win.focus();
}

async function triggerRun(): Promise<void> {
  const apiKey = process.env["FINANCE_API_KEY"] ?? "";
  try {
    const resp = await fetch(`${AGENT_URL}/api/mail/run`, {
      method:  "POST",
      headers: apiKey ? { "X-Api-Key": apiKey } : {},
    });
    const body = await resp.json();
    console.log("[main] triggerRun →", body);
    // Notify renderer so it can refresh
    win?.webContents.send("agent:run-triggered", body);
  } catch (err) {
    console.error("[main] triggerRun failed:", err);
  }
}

// ── IPC handlers ──────────────────────────────────────────────────────────────

ipcMain.handle("window:minimize",  () => win?.minimize());
ipcMain.handle("window:maximize",  () =>
  win?.isMaximized() ? win.unmaximize() : win?.maximize()
);
ipcMain.handle("window:hide",      () => win?.hide());

ipcMain.handle("agent:run",        async () => {
  await triggerRun();
  return { ok: true };
});

ipcMain.handle("agent:get-url",    () => AGENT_URL);

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  // macOS: stay in tray only, no Dock icon
  if (process.platform === "darwin") {
    app.dock?.hide();
  }

  installLaunchdPlist();
  tray = createTray();
  win  = createWindow();

  app.on("activate", () => showWindow());
});

// Prevent default quit behaviour — only explicit "Quit" menu item exits
app.on("window-all-closed", () => {
  // Do nothing; keep running in tray
});

app.on("before-quit", () => {
  // Allow the window to actually close on quit
  if (win) {
    win.removeAllListeners("close");
    win.close();
  }
});
