# codingholic-homepage Workflow (`ch-hp-worklow.md`)

## Overview

Final structure on the NAS:

```text
/volume1/docker/codingholic-homepage/
├── prod
└── stag
```

Final routing:

```text
codingholic.fun         → Cloudflare Tunnel → 127.0.0.1:3002 → prod
staging.codingholic.fun → Cloudflare Tunnel → 127.0.0.1:3003 → stag
```

Container names:

```text
codingholic-homepage-prod
codingholic-homepage-stag
```

---

## Golden rules

- Edit code on the **NAS stag folder** via Mac SMB mount
- NAS is the **source of truth** and **deploy target**
- `stag` is the default deploy target
- Test on `staging.codingholic.fun`
- Promote to `prod` only when satisfied

---

## NAS Mount Setup (one-time)

### 1. Connect via SMB

```text
Finder → Go → Connect to Server → smb://192.168.1.44
```

Mount the **docker** shared folder. The stag folder will appear at:

```text
/Volumes/docker/codingholic-homepage/stag
```

### 2. Auto-mount on login

```text
System Settings → General → Login Items → + → select the mounted volume
```

This ensures the mount is ready every time you start your Mac.

### 3. Open in VS Code

```bash
code /Volumes/docker/codingholic-homepage/stag
```

Files you save in VS Code write directly to the NAS. No sync step needed.

---

## NAS structure

```text
/volume1/docker/codingholic-homepage/prod
/volume1/docker/codingholic-homepage/stag
```

`prod/docker-compose.yml` should expose:

```yaml
services:
  codingholic-homepage:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: codingholic-homepage-prod
    restart: unless-stopped
    ports:
      - "127.0.0.1:3002:3000"
    environment:
      NEXT_PUBLIC_SITE_ENV: production
```

`stag/docker-compose.yml` should expose:

```yaml
services:
  codingholic-homepage:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: codingholic-homepage-stag
    restart: unless-stopped
    ports:
      - "127.0.0.1:3003:3000"
    environment:
      NEXT_PUBLIC_SITE_ENV: staging
```

---

## Cloudflare Tunnel config

Edit:

```bash
nano /var/services/homes/g4ndr1k/.cloudflared/config.yml
```

Use:

```yaml
tunnel: codingholic
credentials-file: /var/services/homes/g4ndr1k/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: codingholic.fun
    service: http://127.0.0.1:3002

  - hostname: staging.codingholic.fun
    service: http://127.0.0.1:3003

  - service: http_status:404
```

Then make sure staging DNS exists:

```bash
cloudflared tunnel route dns codingholic staging.codingholic.fun
```

If you change `config.yml`, restart `cloudflared` by killing the actual process and re-running the Synology Task Scheduler task.

---

## Add a STAGING badge automatically in UI

### Goal

Show a visible badge/banner only on the staging site.

### Best approach

Use an environment variable passed by Docker Compose.

### 1. Add a badge component

Create:

```text
app/components/environment-badge.tsx
```

```tsx
export function EnvironmentBadge() {
  const env = process.env.NEXT_PUBLIC_SITE_ENV;

  if (env !== "staging") return null;

  return (
    <div className="border-b border-amber-500/20 bg-amber-500/10">
      <div className="container-shell py-2 text-center text-sm font-medium text-amber-200">
        STAGING · Preview environment
      </div>
    </div>
  );
}
```

### 2. Render it in `app/layout.tsx`

Example:

```tsx
import "./globals.css";
import { SiteFooter } from "./components/site-footer";
import { SiteHeader } from "./components/site-header";
import { EnvironmentBadge } from "./components/environment-badge";

export const metadata = {
  title: "codingholic.fun",
  description: "Public site, private tools, and future experiments."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <EnvironmentBadge />
        <SiteHeader />
        <main>{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
```

This keeps production clean while making staging unmistakable.

---

## Mac deploy script: `~/deploy-codingholic.sh`

Create on the Mac:

```bash
nano ~/deploy-codingholic.sh
```

Paste:

```bash
#!/usr/bin/env bash
set -euo pipefail

NAS_USER="chfun"
NAS_HOST="192.168.1.44"
STAG_DIR="/volume1/docker/codingholic-homepage/stag"
MOUNT_PATH="/Volumes/docker/codingholic-homepage/stag"

echo "==> Safety check"
if [ ! -f "$MOUNT_PATH/package.json" ] || [ ! -d "$MOUNT_PATH/app" ]; then
  echo "ERROR: NAS not mounted or stag dir missing expected files"
  echo "Mount path expected: $MOUNT_PATH"
  exit 1
fi

echo "==> Deploying staging on NAS"
ssh -t "${NAS_USER}@${NAS_HOST}" \
  "cd ${STAG_DIR} && sudo docker compose up -d --build && sudo docker image prune -f"

echo "✅ Staging deploy complete"
echo "👉 Test at: https://staging.codingholic.fun"
```

Make it executable:

```bash
chmod +x ~/deploy-codingholic.sh
```

### Daily usage

```bash
~/deploy-codingholic.sh
```

---

## One-command promote script from the Mac

### Goal

After staging is approved, copy the exact same code to `prod` and deploy it.

Create:

```bash
nano ~/promote-codingholic.sh
```

Paste:

```bash
#!/usr/bin/env bash
set -euo pipefail

NAS_USER="chfun"
NAS_HOST="192.168.1.44"
BASE_DIR="/volume1/docker/codingholic-homepage"
STAG_DIR="${BASE_DIR}/stag"
PROD_DIR="${BASE_DIR}/prod"

echo "==> Preview promote: staging -> prod"
ssh -t "${NAS_USER}@${NAS_HOST}" "rsync -av --dry-run --delete \
  --exclude node_modules \
  --exclude .next \
  --exclude .git \
  --exclude .gitignore \
  --exclude .DS_Store \
  ${STAG_DIR}/ ${PROD_DIR}/"

echo
read -r -p "Promote staging to production? [y/N] " reply
if [[ ! "$reply" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Syncing staging to prod"
ssh -t "${NAS_USER}@${NAS_HOST}" "rsync -av --delete \
  --exclude node_modules \
  --exclude .next \
  --exclude .git \
  --exclude .gitignore \
  --exclude .DS_Store \
  ${STAG_DIR}/ ${PROD_DIR}/"

echo "==> Deploying prod on NAS"
ssh -t "${NAS_USER}@${NAS_HOST}" "cd ${PROD_DIR} && sudo docker compose up -d --build && sudo docker image prune -f"

echo "✅ Production deploy complete"
echo "👉 Live at: https://codingholic.fun"
```

Make it executable:

```bash
chmod +x ~/promote-codingholic.sh
```

### Promotion usage

```bash
~/promote-codingholic.sh
```

---

## Recommended day-to-day workflow

### 1. Open stag in VS Code

```bash
code /Volumes/docker/codingholic-homepage/stag
```

Edit and save. Changes write directly to the NAS.

### 2. Deploy to staging

```bash
~/deploy-codingholic.sh
```

### 3. Review

Open:

```text
https://staging.codingholic.fun
```

### 4. Promote when approved

```bash
~/promote-codingholic.sh
```

### 5. Verify production

Open:

```text
https://codingholic.fun
```

---

## Version control (optional)

If you want git history, init a repo directly in the stag folder:

```bash
cd /Volumes/docker/codingholic-homepage/stag
git init
git add .
git commit -m "baseline"
git remote add origin <your-repo-url>
git push -u origin main
```

Daily loop:

```bash
git add .
git commit -m "improve homepage cards"
git push
~/deploy-codingholic.sh
```

The NAS is the source of truth either way — git is optional backup and history only.

---

## Safety checklist before every deploy

On the Mac, confirm the mount is live and the key files are present:

```bash
ls /Volumes/docker/codingholic-homepage/stag/app \
   /Volumes/docker/codingholic-homepage/stag/Dockerfile \
   /Volumes/docker/codingholic-homepage/stag/package.json
```

If the mount is missing or files are absent, do **not** deploy.

---

## Rollback options

### Fast rollback for production

If the last promotion was bad:
1. SSH to NAS
2. Restore previous code into `prod` (from a git checkout on the mount, or manual edit)
3. Redeploy `prod`

If you use git:

```bash
cd /Volumes/docker/codingholic-homepage/stag
git checkout <previous-good-commit>
~/deploy-codingholic.sh
~/promote-codingholic.sh
```

### Fast rollback for tunnel config

Usually not needed, because `prod` and `stag` have fixed domains and fixed ports.

---

## Final clean model

```text
Mac
├── /Volumes/docker → smb://192.168.1.44/docker (NAS mount, auto on login)
├── ~/deploy-codingholic.sh   (SSH → docker compose up --build on stag)
└── ~/promote-codingholic.sh  (SSH → rsync stag→prod + docker compose up --build)

NAS
└── /volume1/docker/codingholic-homepage
    ├── stag  → 3003 → staging.codingholic.fun  ← edit here via VS Code
    └── prod  → 3002 → codingholic.fun
```

Edit on the mount. Deploy with one command. Promote with one command.
