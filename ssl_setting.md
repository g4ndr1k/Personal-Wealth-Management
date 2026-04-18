## 🔐 Private Domain Access via Tailscale + Synology Reverse Proxy

### 🎯 Goal
Allow secure access to the NAS-hosted **read-only Finance Dashboard** using a custom domain:

https://codingholic.fun

Requirements:
- Only accessible when Tailscale VPN is ON
- Not exposed to the public internet
- Uses valid HTTPS (no browser warning)
- Automatically renews certificates

---

## 🧱 Architecture Overview

```
iPhone / Laptop (Tailscale ON)
        ↓
codingholic.fun
        ↓ (AdGuard DNS rewrite)
100.x.x.x (Tailscale IP)
        ↓
Synology Reverse Proxy (DSM)
        ↓
127.0.0.1:8090
        ↓
finance-api (read-only, Docker)
```

---

## 🌐 DNS Setup

### Cloudflare (Public DNS)

```
Type: A
Name: codingholic.fun
Content: 100.x.x.x (Tailscale IP)
Proxy: DNS only (IMPORTANT)
```

⚠️ Do NOT enable proxy (orange cloud)

---

### AdGuard (Private DNS Rewrite)

```
codingholic.fun → 100.x.x.x
*.codingholic.fun → 100.x.x.x
```

---

## 🔁 Synology Reverse Proxy

### HTTP Rule
```
Source:
  Protocol: HTTP
  Hostname: codingholic.fun
  Port: 80

Destination:
  Protocol: HTTP
  Hostname: 127.0.0.1
  Port: 8090
```

### HTTPS Rule
```
Source:
  Protocol: HTTPS
  Hostname: codingholic.fun
  Port: 443

Destination:
  Protocol: HTTP
  Hostname: 127.0.0.1
  Port: 8090
```

---

## 🔒 HTTPS via acme.sh (DNS Challenge)

### Install acme.sh

```bash
cd ~
curl -L https://github.com/acmesh-official/acme.sh/archive/master.tar.gz -o acme.tar.gz
tar -xzf acme.tar.gz
cd acme.sh-master
./acme.sh --install --force
```

---

### Set Let's Encrypt

```bash
~/.acme.sh/acme.sh --set-default-ca --server letsencrypt
```

---

### Issue Certificate

```bash
export CF_Token="YOUR_CLOUDFLARE_API_TOKEN"

~/.acme.sh/acme.sh --issue \
  --dns dns_cf \
  -d codingholic.fun \
  -d '*.codingholic.fun'
```

---

### Deploy to Synology DSM

```bash
export SYNO_SCHEME="http"
export SYNO_HOSTNAME="localhost"
export SYNO_PORT="5000"
export SYNO_USERNAME="YOUR_DSM_ADMIN_USERNAME"
export SYNO_PASSWORD="YOUR_DSM_ADMIN_PASSWORD"
export SYNO_CREATE=1
export SYNO_CERTIFICATE="codingholic.fun"

~/.acme.sh/acme.sh --deploy -d codingholic.fun --ecc --deploy-hook synology_dsm
```

---

## 🔁 Auto Renewal (DSM Task Scheduler)

Create task:

- Control Panel → Task Scheduler → Create → User-defined script
- User: g4ndr1k
- Schedule: Daily

Script:

```bash
. /var/services/homes/g4ndr1k/.config/acme/env.sh

ACME_HOME="/var/services/homes/g4ndr1k/.acme.sh"

"$ACME_HOME/acme.sh" --cron --home "$ACME_HOME"
"$ACME_HOME/acme.sh" --deploy -d codingholic.fun --ecc --deploy-hook synology_dsm
```

---

## 🔐 Secrets Management

Create:

```
/var/services/homes/g4ndr1k/.config/acme/env.sh
```

Content:

```bash
export CF_Token="..."
export SYNO_SCHEME="http"
export SYNO_HOSTNAME="localhost"
export SYNO_PORT="5000"
export SYNO_USERNAME="..."
export SYNO_PASSWORD="..."
export SYNO_CREATE=1
export SYNO_CERTIFICATE="codingholic.fun"
```

Permissions:

```bash
chmod 600 /var/services/homes/g4ndr1k/.config/acme/env.sh
```

---

## ⚠️ Security Notes

- Domain resolves to Tailscale IP (100.x.x.x) → not publicly reachable
- No port forwarding required
- Only accessible via Tailscale VPN
- Cloudflare proxy must remain OFF
- Rotate API tokens and passwords if exposed

---

## ✅ Result

- Private access: https://codingholic.fun
- Valid HTTPS (no warnings)
- Auto-renewed certificates
- Fully local-first architecture