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

- Edit code on the **Mac**
- NAS is the **deploy target**
- `stag` is the default deploy target
- Test on `staging.codingholic.fun`
- Promote to `prod` only when satisfied

---

## Recommended local Mac structure

Use one local source folder:

```text
~/codingholic-homepage
```

This is your source of truth.

Suggested tools:
- VS Code for main editing
- Claude Code / Hermes for refactors and UI generation
- terminal for deploy

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

## One-command deploy workflow from the Mac

### Philosophy

- Work only from the Mac
- Sync code to `stag`
- Deploy `stag`
- Test on `staging.codingholic.fun`
- Promote to `prod` with one explicit command

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

LOCAL_DIR="$HOME/codingholic-homepage"
NAS_USER="g4ndr1k"
NAS_HOST="192.168.1.44"
BASE_DIR="/volume1/docker/codingholic-homepage"
STAG_DIR="${BASE_DIR}/stag"

echo "==> Safety check"
if [ ! -f "$LOCAL_DIR/package.json" ] || [ ! -f "$LOCAL_DIR/docker-compose.yml" ] || [ ! -d "$LOCAL_DIR/app" ]; then
  echo "ERROR: LOCAL_DIR does not look like the homepage project"
  echo "LOCAL_DIR=$LOCAL_DIR"
  exit 1
fi

echo "==> Dry-run preview to staging"
rsync -av --dry-run --delete   --exclude node_modules   --exclude .next   --exclude .git   --exclude .gitignore   --exclude .DS_Store   "$LOCAL_DIR/" "${NAS_USER}@${NAS_HOST}:${STAG_DIR}/"

echo
read -r -p "Proceed with sync to staging? [y/N] " reply
if [[ ! "$reply" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Syncing to staging"
rsync -av --delete   --exclude node_modules   --exclude .next   --exclude .git   --exclude .gitignore   --exclude .DS_Store   "$LOCAL_DIR/" "${NAS_USER}@${NAS_HOST}:${STAG_DIR}/"

echo "==> Deploying staging on NAS"
ssh -t "${NAS_USER}@${NAS_HOST}" "cd ${STAG_DIR} && sudo docker compose up -d --build && sudo docker image prune -f"

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

NAS_USER="g4ndr1k"
NAS_HOST="192.168.1.44"
BASE_DIR="/volume1/docker/codingholic-homepage"
STAG_DIR="${BASE_DIR}/stag"
PROD_DIR="${BASE_DIR}/prod"

echo "==> Preview promote: staging -> prod"
ssh -t "${NAS_USER}@${NAS_HOST}" "rsync -av --dry-run --delete   --exclude node_modules   --exclude .next   --exclude .git   --exclude .gitignore   --exclude .DS_Store   ${STAG_DIR}/ ${PROD_DIR}/"

echo
read -r -p "Promote staging to production? [y/N] " reply
if [[ ! "$reply" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Syncing staging to prod"
ssh -t "${NAS_USER}@${NAS_HOST}" "rsync -av --delete   --exclude node_modules   --exclude .next   --exclude .git   --exclude .gitignore   --exclude .DS_Store   ${STAG_DIR}/ ${PROD_DIR}/"

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

### 1. Edit locally on the Mac

```bash
cd ~/codingholic-homepage
code .
```

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

## Optional GitHub workflow

Best practice:
- Git lives on the Mac only
- GitHub is backup and version history
- NAS remains deploy-only

Suggested local Git workflow:

```bash
cd ~/codingholic-homepage
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

---

## Safety checklist before every deploy

On the Mac, confirm:

```bash
cd ~/codingholic-homepage
ls app Dockerfile docker-compose.yml package.json
```

If any of those are missing, do **not** deploy.

Always read the `rsync --dry-run` output before confirming.

---

## Rollback options

### Fast rollback for production

If the last promotion was bad:
1. restore previous code into `prod`
2. redeploy `prod`

If you use Git on the Mac:

```bash
git checkout <previous-good-commit>
~/deploy-codingholic.sh
~/promote-codingholic.sh
```

### Fast rollback for tunnel config

Usually not needed anymore, because `prod` and `stag` have fixed domains and fixed ports.

---

## Final clean model

```text
Mac
├── ~/codingholic-homepage
├── ~/deploy-codingholic.sh
└── ~/promote-codingholic.sh

NAS
└── /volume1/docker/codingholic-homepage
    ├── prod  → 3002 → codingholic.fun
    └── stag  → 3003 → staging.codingholic.fun
```

This keeps your naming clean, your workflow predictable, and your production site stable.
