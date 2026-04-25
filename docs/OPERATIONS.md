# Operations

How to run, validate, and maintain the system. Architecture details live in [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md); symptom-driven fixes live in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Common Commands

### Bridge

```bash
PYTHONPATH=$(pwd) python3 -m bridge.server
```

The bridge runs on the Mac host, not inside Docker. It exposes `http://127.0.0.1:9100` or the configured host/port in `config/settings.toml`.

Health checks:

```bash
curl http://127.0.0.1:9100/healthz
TOKEN=$(cat secrets/bridge.token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/health | python3 -m json.tool
```

### Finance API

```bash
python3 -m finance.server
python3 -m finance.server --reload
```

Docker:

```bash
docker compose up --build -d
docker compose logs -f finance-api
```

Always rebuild the Docker image after Python changes. A container restart alone does not pick up changed source files baked into the image.

### PWA

```bash
cd pwa
npm install
npm run dev
npm run build
```

`pwa/dist/` must exist before building production Docker images that serve the PWA from FastAPI.

### Household Expense PWA

```bash
cd household-expense/pwa
npm install
npm run build

cd ~/agentic-ai/household-expense
bash deploy_household.sh
```

The household app runs on the NAS as `household-api` on port `8088`. The deploy script builds the Vue PWA, copies icons into `household-expense/dist`, rsyncs to `/volume1/docker/household-expense`, rebuilds the NAS Docker container, and verifies `/api/household/health`.

Health checks:

```bash
curl http://192.168.1.44:8088/api/household/health
curl -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/household/settings
```

### Import And Backup

```bash
python3 -m finance.importer --dry-run
python3 -m finance.importer
python3 -m finance.importer --overwrite
python3 -m finance.backup --kind manual
```

`finance.importer` reads `output/xls/ALL_TRANSACTIONS.xlsx` and writes to `data/finance.db`.

NAS automation defaults:

- NAS SSH user: `chfun`
- NAS SSH port: `22`
- Finance replica sync target: `chfun@192.168.1.44:/volume1/finance/finance_readonly.db`
- Local sudo password file for NAS automation: `secrets/nas_sudo_password`

## Process Local PDF Workflow

Use Settings -> Process Local PDFs in the PWA for manual processing.

1. Drop PDFs into `data/pdf_inbox/` or use already-unlocked files in `data/pdf_unlocked/`.
2. Open Settings -> Process Local PDFs.
3. Open PDF Workspace.
4. Select specific files, or leave none selected to process all ready files.
5. Click Process Selected / Process Ready PDFs.
6. The PWA runs preflight, queues all files, polls each job, and shows per-file status.

Status meanings:

| Status | Action |
|---|---|
| `new` | Ready to process. |
| `pending` | Queued. |
| `running` | Processing. |
| `done` | Fully processed. |
| `partial` | Main parse succeeded; secondary write failed. Inspect details. |
| `error` | Processing failed. Inspect details and bridge logs. |
| `missing_source` | Registry row points to a PDF that no longer exists. |

## Preflight Validation

The PWA calls `GET /api/pdf/preflight`, which proxies bridge `GET /pdf/preflight`, before queueing work.

Preflight checks:

- bridge initialized its PDF handler config
- provider order contains only supported providers
- PDF inbox and unlocked folders exist
- bridge job database is reachable
- runtime diagnostics are available

Manual checks:

```bash
TOKEN=$(cat secrets/bridge.token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/pdf/preflight | python3 -m json.tool

curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/pdf/preflight | python3 -m json.tool
```

## Config Validation

Main config file: `config/settings.toml`.

Important sections:

| Section | Used by |
|---|---|
| `[bridge]` | Bridge host, port, log level. |
| `[auth]` | Bridge token source and file/keychain settings. |
| `[mail]` | Mail source and Gmail secrets. |
| `[classifier]` | Provider order and rule loading. |
| `[pdf]` | PDF folders, password source, job DB, verification. |
| `[pipeline]` | Scheduled PDF/import/backup pipeline. |
| `[finance]` | SQLite DB path and XLSX input. |
| `[fastapi]` | Finance API host, port, CORS. |
| `[ollama_finance]` | Finance categorization AI settings. |
| `[household]` | Household satellite base URL and API key file. |

Validate TOML syntax:

```bash
python3 -c "import tomllib; tomllib.load(open('config/settings.toml','rb')); print('OK')"
```

Validate provider configuration:

```bash
python3 -c "from agent.app.providers import PROVIDERS; print(sorted(PROVIDERS))"
```

## Secrets

macOS Keychain is the source of truth. `secrets/` files are runtime/export artifacts for Docker and local scripts.

Common commands:

```bash
python3 -m bridge.secret_manager init
python3 scripts/export-secrets-for-docker.py
bash scripts/export-secrets-for-docker.sh
```

Expected secret files:

| File | Purpose |
|---|---|
| `secrets/bridge.token` | Bridge bearer token. |
| `secrets/gmail.toml` | Gmail app passwords if file fallback is used. |
| `secrets/banks.toml` | Bank PDF passwords if file fallback is used. |
| `secrets/nas_sync_key` | NAS SSH private key. |
| `secrets/nas_sudo_password` | Synology sudo password used by NAS deploy scripts. |
| `secrets/finance_api.key` | Exported finance API key used by NAS finance deploy script. |

`secrets/bridge.token` must be a file, not a directory, and should have mode `600`.

## Logs

| Log | Command |
|---|---|
| Bridge app log | `tail -f logs/bridge.log` |
| Docker finance API | `docker compose logs -f finance-api` |
| Docker mail agent | `docker compose logs -f mail-agent` |
| Batch processor | `tail -f logs/batch_process.log` |

LaunchAgent stdout/stderr locations depend on the installed plist. Check `launchd/` and the plist `StandardOutPath` / `StandardErrorPath` values.

## Maintenance

Backups:

- The finance DB is backed up with SQLite online backup helpers.
- Manual backups can be triggered from Settings or `python3 -m finance.backup --kind manual`.
- NAS sync uses SSH and the configured NAS target.
- Current default NAS sync target is `chfun@192.168.1.44:/volume1/finance/finance_readonly.db`.
- `finance.backup` now uses SSH port `22` by default; override with `NAS_SYNC_SSH_PORT` only if the NAS SSH port changes.

PDF registry:

- Processed files are tracked by SHA-256, so renaming a file does not make it new.
- `missing_source` means a registry entry points to a file no longer on disk.
- Failed jobs can be retried after correcting parser/config/password issues.

PWA cache:

- Mobile/offline GET caching is intentional.
- Settings includes a Refresh Mobile Data action.
- PDF endpoints are network-only or freshness-forced so local processing state does not come from stale cache.

Household maintenance:

- `household-expense/data/household.db` is separate from `data/finance.db`.
- Household transactions are soft-deleted with `is_deleted`.
- Settings -> Household Expense manages categories, recent expense category corrections, and cash pools through the finance API proxy.
- The household API key is loaded from `household-expense/secrets/household_api.key` via `[household].api_key_file`.
- Reconciliation import into the main finance model should be verified before documenting it as fully automated.

## Existing Specialized Docs

These docs remain as focused notes and were not folded into the core docs set:

- `docs/ch-hp-worklow.md`
