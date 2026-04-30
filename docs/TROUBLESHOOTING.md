# Troubleshooting

Symptoms, likely causes, diagnosis, fixes, and prevention. For normal operating commands, see [OPERATIONS.md](OPERATIONS.md).

## PDF Stuck As "New" Or "Never"

### Symptoms

- PDF stays `New` or `Never` after clicking Process.
- Progress shows 0 processed.
- No useful error is visible in the file row.

### Likely Cause

- Preflight failed before queueing.
- No files were selected and no ready files matched the current filters.
- Bridge is down or unreachable from the finance API.
- A stale service worker or API cache returned old workspace data.

### How To Diagnose

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/pdf/preflight | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/pdf/local-workspace | python3 -m json.tool
tail -80 logs/bridge.log
```

### Fix

- Fix any preflight errors first.
- Clear filters or explicitly select files in Settings -> Process Local PDFs.
- Restart the bridge if `/api/pdf/preflight` reports bridge connection errors.
- Use Settings -> Refresh, then browser hard refresh if the PWA UI is stale.

### Prevention

- Keep PDF endpoints network-only/freshness-forced.
- Do not convert bridge/preflight failures into successful API responses.
- Preserve the post-run warning when 0 of N files were processed.

## Bridge Unreachable

### Symptoms

- `/api/pdf/preflight` returns a bridge connection error.
- Settings shows preflight or bridge token errors.
- Finance API works but PDF processing cannot start.

### Likely Cause

- Bridge process is not running.
- Bridge token file is missing, empty, or a directory.
- Docker cannot reach the host bridge address configured for the finance API.

### How To Diagnose

```bash
curl http://127.0.0.1:9100/healthz
ls -la secrets/bridge.token
docker compose logs -f finance-api
tail -80 logs/bridge.log
```

### Fix

```bash
PYTHONPATH=$(pwd) python3 -m bridge.server
python3 scripts/export-secrets-for-docker.py
```

If `secrets/bridge.token` is a directory, remove the stale directory and re-export secrets from Keychain.

### Prevention

- Use `scripts/setup-app.sh` / LaunchAgent setup for stable startup.
- Keep bridge token validation strict: missing, empty, or directory token paths must fail with clear `503`.

## Mail Agent Container Will Not Start After Rebuild

### Symptoms

- `docker compose up --build -d` builds images but `mail-agent` fails to start.
- `docker compose ps` shows `finance-api` only.
- Docker reports a mount error for `/Volumes/Synology/mailagent` or `/host_mnt/Volumes/Synology/mailagent`.

### Likely Cause

- `/Volumes/Synology/mailagent` is not mounted on the Mac.
- Docker Desktop is not allowed to share `/Volumes`.
- The Synology share was renamed, disconnected, or mounted under a different path.

### How To Diagnose

```bash
ls -la /Volumes
ls -la /Volumes/Synology/mailagent
docker compose ps
docker compose logs --tail=80 mail-agent
```

### Fix

- Mount the Synology share so `/Volumes/Synology/mailagent` exists.
- In Docker Desktop, allow file sharing for `/Volumes` or the specific Synology mount path.

```bash
docker compose up --build -d mail-agent
python3 scripts/mailagent_status.py --no-run
```

For sentinel and attachment-routing details, see [MAIL_AGENT.md](MAIL_AGENT.md).

## Mail Dashboard Shows Connection Error

### Symptoms

- Electron dashboard opens, but shows a connection error.
- `Run Now` does nothing visible.
- `python3 scripts/mailagent_status.py --no-run` fails.

### Likely Cause

- `finance-api` did not mount `/api/mail/*`.
- `finance-api` is missing mail-account dependencies such as `tomlkit` or `keyring`.
- `mail-agent` is not running or port `8080` is not bound for worker health/debug.
- The NAS bind mount prevented the container from starting.
- API key mismatch when `FINANCE_API_KEY` is configured.

### How To Diagnose

```bash
docker compose ps
docker compose logs --tail=120 finance-api
docker compose logs --tail=120 mail-agent
python3 scripts/mailagent_status.py --no-run
```

### Fix

```bash
docker compose up --build -d finance-api mail-agent
export VITE_FINANCE_API_KEY="$FINANCE_API_KEY"
cd mail-dashboard
npm run dev
```

Dashboard APIs are under `127.0.0.1:8090/api/mail/*`; mail-agent health/debug is on `127.0.0.1:8080`. See [MAIL_AGENT.md](MAIL_AGENT.md) for the detailed API boundary.

## Gmail Account Tests Fail With ASCII Or Whitespace Errors

### Symptoms

- Test Connection fails with an error like:

```text
IMAP Connection failed: 'ascii' codec can't encode character '\xa0'
```

### Likely Cause

- The pasted Gmail App Password contains spaces or non-breaking spaces from Google’s formatted display.

### Fix

- Paste the App Password normally; current code strips whitespace automatically.
- If the running container/app is stale, rebuild `finance-api` and restart the dashboard so the normalization patch is active.

See [MAIL_AGENT.md](MAIL_AGENT.md) for Gmail credential handling.

## Account Saves But Does Not Appear In IMAP Accounts

### Symptoms

- Save succeeds, but the table stays empty.
- `config/settings.toml` contains malformed `[mail.imap].accounts` entries.

### Likely Cause

- An older build wrote the account as an invalid TOML fragment instead of an inline table.

### How To Diagnose

```bash
python3 -c "import tomllib; tomllib.load(open('config/settings.toml','rb')); print('OK')"
```

### Fix

- Repair `[mail.imap].accounts` so each entry is a valid inline table.
- Rebuild/restart `finance-api` after fixing the writer code.

See [MAIL_AGENT.md](MAIL_AGENT.md) for the expected account configuration shape.

## Saving Mail Settings Fails With `settings.tmp -> settings.toml` Busy

### Symptoms

- Save fails with an error like:

```text
[Errno 16] Device or resource busy: '/app/config/settings.tmp' -> '/app/config/settings.toml'
```

### Likely Cause

- `config/settings.toml` is bind-mounted into Docker, and atomic rename on that single-file mount is rejected by the container filesystem.

### Fix

- Use the current build, which falls back to an in-place fsynced write when atomic rename is blocked.
- Rebuild `finance-api` and retry the save.

See [MAIL_AGENT.md](MAIL_AGENT.md) for mail-dashboard settings details.

## NAS Deploy Or Sync Fails

### Symptoms

- `scripts/deploy_nas.sh` fails before upload, during image load, or while recreating the container.
- `finance.backup.sync_to_nas()` returns SSH or permission errors.
- Household NAS deploy works inconsistently after changing Synology users.

### Likely Cause

- NAS automation is still pointed at the old Synology account.
- `secrets/nas_sudo_password` does not match the current NAS sudo password.
- The NAS user can SSH, but does not own the sync target file.
- Backup sync is using the wrong SSH port.

### How To Diagnose

```bash
grep '^NAS_SYNC_TARGET=' .env
ssh -i secrets/nas_sync_key chfun@192.168.1.44 'whoami'
ssh -i secrets/nas_sync_key chfun@192.168.1.44 "printf '%s\n' \"$(cat secrets/nas_sudo_password)\" | sudo -S whoami"
ssh -i secrets/nas_sync_key chfun@192.168.1.44 'ls -ld /volume1/finance && ls -l /volume1/finance/finance_readonly.db'
```

### Fix

- Set NAS automation to the current SSH user, currently `chfun`.
- Update `secrets/nas_sudo_password` after changing the Synology sudo password.
- Ensure `NAS_SYNC_TARGET` points to `chfun@192.168.1.44:/volume1/finance/finance_readonly.db`.
- If the target file is still owned by the old NAS user, change ownership so `chfun` can overwrite it.
- Keep backup sync on SSH port `22` unless the NAS SSH listener is intentionally moved.

### Prevention

- When creating a new Synology admin/deploy user, install `secrets/nas_sync_key.pub` into that user’s `authorized_keys` before changing scripts.
- Keep `scripts/deploy_nas.sh` and `household-expense/deploy_household.sh` aligned on the same NAS username.
- Re-test both `bash scripts/deploy_nas.sh` and a real `finance.backup.sync_to_nas()` call after NAS account changes.

## Stale Python Path

### Symptoms

- Launch scripts fail before starting Python.
- `.app` wrapper or LaunchAgent points to a deleted Python executable.
- Preflight runtime shows an unexpected Python path.

### Likely Cause

Older documentation or wrapper configuration referenced a fixed Homebrew/miniconda Python path. The scripts now resolve Python dynamically where possible.

### How To Diagnose

```bash
bash -n scripts/run_bridge.sh scripts/setup-app.sh scripts/start_agent.sh scripts/export-secrets-for-docker.sh
rg -n "miniconda|python3\\.13|/opt/homebrew/bin/python" scripts launchd app-bundle docs README.md SYSTEM_DESIGN.md
```

### Fix

- Regenerate or update the app wrapper with `scripts/setup-app.sh`.
- Prefer `python3` in docs and dynamic script resolution in shell wrappers.
- If a fixed path is unavoidable for macOS permissions, verify it exists first.

### Prevention

- Do not document user-specific Python absolute paths as the default.
- Preflight should expose runtime Python so path regressions are visible.

## Invalid `provider_order`

### Symptoms

- Classifier startup fails with unknown provider.
- PDF preflight reports invalid provider.
- `provider_order = ["rule_based"]` is rejected.

### Likely Cause

Config validation and runtime provider registration drifted apart.

### How To Diagnose

```bash
python3 -c "from agent.app.providers import PROVIDERS; print(sorted(PROVIDERS))"
rg -n "provider_order|PROVIDERS" agent bridge config/settings.toml
```

### Fix

- Use provider names from `agent/app/providers/__init__.py`.
- Keep `rule_based` in the provider registry.
- Update `config/settings.toml` to use supported names only.

### Prevention

- Config validation and runtime should use the same provider registry.
- New providers must update `PROVIDERS`, tests, and docs together.

## Parser Not Selected

### Symptoms

- PDF job fails with unknown bank/type.
- Job row shows `error`.
- Parser works for another bank but not this statement.

### Likely Cause

- Filename/content does not match parser detection.
- Bank statement format changed.
- PDF is scanned/image-only or encrypted without a resolved password.

### How To Diagnose

```bash
python3 -c "from parsers.router import detect_bank_and_type; print(detect_bank_and_type('data/pdf_inbox/your.pdf'))"
tail -120 logs/bridge.log
```

### Fix

- Confirm the PDF is readable and not an unsupported scan.
- Add or update detection in `parsers/router.py` only after confirming content patterns.
- Keep extraction changes isolated to the relevant parser.

### Prevention

- Do not put parser extraction logic in bridge job/status code.
- Add focused parser tests when adding or changing detection.

## Matching Engine Or Matching Console Looks Wrong

### Symptoms

- `/api/matching/stats` reports zero mappings for a domain that should have mappings.
- Matching Console mapping confirm/delete returns unexpected errors.
- `category_shadow_diff` grows after enabling categorization shadow mode.
- Parser/dedup/categorization behavior changes after enabling an engine flag.

### Likely Cause

- The relevant domain flag is not set, so the legacy path is still being used.
- Matching tables were not created in the active SQLite database.
- A Tier-1 mapping disagrees with legacy behavior.
- A malformed domain or field name reached dynamic SQL code outside the storage/API allow-list.

### How To Diagnose

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/matching/stats | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/matching/invariant-log?limit=50" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/matching/shadow-diffs?limit=50" | python3 -m json.tool

sqlite3 data/finance.db ".tables matching_%"
python3 -m pytest tests/matching -q
```

Check active flags in the environment that launches the API/importer:

```bash
env | rg 'PARSER_USE_ENGINE|DEDUP_USE_ENGINE|CATEGORIZATION_USE_ENGINE|MATCHING_ENABLED_'
```

### Fix

- If behavior changed unexpectedly, disable the domain flag and rerun using the legacy path.
- If tables are missing, open the DB through `finance.db.open_db()` or restart the API so migrations run.
- For categorization rollout, use `CATEGORIZATION_USE_ENGINE=shadow` and inspect `/api/matching/shadow-diffs` before enabling `true`.
- If a matching API operation on a missing mapping returns 500 instead of `404 mapping_not_found`, fix the endpoint rather than handling it in the UI.

### Prevention

- Keep domain adoption flag-gated until snapshot/shadow tests pass.
- Add a fixture in `tests/matching/` for every matching behavior bug before fixing it.
- Route dynamic domain/table access through `finance.matching.storage` so identifier validation is applied.

## Transactions Not Appearing In Assets -> Cash & Liquid

### Symptoms

- Statement parses successfully.
- Transactions appear, but balances do not show in Assets / Cash & Liquid.
- Wealth snapshot does not reflect a newly parsed cash account.

### Likely Cause

- Secondary balance upsert failed and the job is `partial`.
- Account owner mapping is missing or wrong.
- The statement type does not include account balance data.
- PWA cache is showing stale asset data.

### How To Diagnose

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/pdf/local-workspace | python3 -m json.tool
sqlite3 data/finance.db "select snapshot_date,institution,account,owner,balance_idr from account_balances order by snapshot_date desc limit 20;"
tail -120 logs/bridge.log | grep -i "upsert warning\\|balance\\|holding"
```

### Fix

- Treat `partial` as requiring review, not full success.
- Fix owner mappings in config or SQLite owner mapping data.
- Reprocess the PDF after correcting parser/owner/password issues.
- Refresh PWA data from Settings.

### Prevention

- Keep secondary upsert warnings visible in per-file details.
- Do not mask `partial` as `done` or `ok`.

## Batch Processing Partial Success

### Symptoms

- Some selected PDFs process and others fail.
- UI reports success even though one file has warnings/errors.
- Balances/holdings are missing after otherwise successful parse.

### Likely Cause

- Secondary writes failed after main parse/export.
- Older UI code treated `partial` as `ok`.
- Batch loop returned early instead of attempting all selected files.

### How To Diagnose

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/pdf/local-workspace | python3 -m json.tool
tail -120 logs/bridge.log | grep -i "upsert warning\\|partial\\|failed"
```

### Fix

- Inspect each file row, not only the aggregate run status.
- Fix the underlying secondary write error.
- Re-run failed or partial files after the fix.

### Prevention

- Batch processing should attempt all selected files.
- Aggregate summaries must count `partial` separately.
- API and UI must preserve meaningful backend errors.

## Invalid XLSX Header

### Symptoms

- Import fails with `XLSX header mismatch`.
- Import stops before reading data rows.

### Likely Cause

`ALL_TRANSACTIONS.xlsx` columns no longer match the positional import contract.

### How To Diagnose

Open the first row of `output/xls/ALL_TRANSACTIONS.xlsx` and compare it with the expected header in `finance/importer.py`.

### Fix

- Regenerate XLS from the parser/exporter.
- Do not manually edit or reorder columns in `ALL_TRANSACTIONS.xlsx`.
- If the exporter contract intentionally changes, update `_EXPECTED_HEADERS`, importer mapping, and tests together.

### Prevention

- Keep importer fail-fast header validation.
- Treat `ALL_TRANSACTIONS.xlsx` as immutable parser output.

## CoreTax Upload Rejected For Year Mismatch

### Symptoms

- CoreTax prior-year upload returns HTTP 400.
- Error mentions `Template year mismatch`.
- A file such as `CoreTax 2025.xlsx` is selected but the PWA tax-year selector is set to `2025`.

### Likely Cause

CoreTax imports the prior submitted workbook to seed the next tax year. The XLSX headers must line up with the selected target year. A workbook with `E=2025` and `F=2026` seeds `target_tax_year=2026`, not `2025`.

### How To Diagnose

```bash
python3 - <<'PY'
from pathlib import Path
import openpyxl

path = Path('/Users/g4ndr1k/Library/CloudStorage/OneDrive-Personal/Finance/SPT/2025/CoreTax 2025.xlsx')
wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
ws = wb.worksheets[0]
print('E4=', ws['E4'].value, 'F4=', ws['F4'].value)
PY
```

### Fix

- Set the PWA tax year to the F-header year.
- Upload the prior submitted SPT workbook for that target year.
- If preparing tax year `2026`, upload the workbook whose headers are `E=2025` and `F=2026`.

### Prevention

- Do not rename files as a substitute for checking the E/F headers.
- Keep the parser's strict year-header validation enabled.

## CoreTax Reconcile Did Not Update A Row

### Symptoms

- Reconcile finishes but a row remains unchanged.
- Trace shows `locked_skipped`.
- Unmatched PWM rows appear even though the account/holding exists in PWM.

### Likely Cause

- The target field is locked from a manual edit.
- No learned mapping exists for the PWM row.
- A learned mapping points to a `target_stable_key` that does not exist for the selected tax year.
- A mapping exists but uses a stale or low-confidence fingerprint.
- The reconcile snapshot date does not contain the PWM source row you expected.

### How To Diagnose

```bash
sqlite3 data/finance.db "
SELECT id, kode_harta, stable_key, current_amount_idr, amount_locked,
       market_value_idr, market_value_locked, last_mapping_id
FROM coretax_rows
WHERE tax_year = 2026
ORDER BY kode_harta, id;
"

sqlite3 data/finance.db "
SELECT id, match_kind, match_value, fingerprint_raw, target_stable_key,
       confidence_level, confidence_score, source, hits, last_used_tax_year
FROM coretax_mappings
ORDER BY id;
"

sqlite3 data/finance.db "
SELECT target_stable_key, source_kind, match_kind, match_value,
       component_amount_idr, component_market_value_idr, is_current, reconcile_run_id
FROM coretax_row_components
WHERE tax_year = 2026
ORDER BY target_stable_key, reconcile_run_id DESC;
"
```

### Fix

- Unlock the field in the CoreTax SPT view if the auto-reconcile value should replace it.
- Use the Mapping tab to map the PWM item, create a row, accept a suggestion, or inspect stale/lifecycle mappings.
- Delete and recreate stale mappings that point to a row from the wrong tax year.
- Confirm or retarget weak mappings if the source fingerprint still represents the correct PWM item.
- If the source appears only in a different snapshot date, rerun reconcile with the matching month range/snapshot.

### Prevention

- Treat locks as intentional. Do not bypass them in API changes.
- Learned mappings should store and use `target_stable_key`.
- Prefer explicit mapping decisions over enabling `CORETAX_LEGACY_HEURISTICS`.
- Review `ORPHANED`, `STALE`, `UNUSED`, and `WEAK` lifecycle buckets before annual export.

## CoreTax Mapping Suggestion Or Assignment Conflict

### Symptoms

- The Mapping tab shows preview conflicts.
- `POST /api/coretax/{year}/mappings/assign` returns HTTP 409.
- Bulk accept of mapping suggestions is blocked.

### Likely Cause

- The same source fingerprint appears twice in the accepted suggestion set.
- A source fingerprint already maps to a different `target_stable_key`.
- The target row no longer exists for the selected tax year.
- A liability source is being mapped to an asset row, or an asset/cash source to a liability row.

### How To Diagnose

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"suggestions":[]}' \
  "http://127.0.0.1:8090/api/coretax/2026/mappings/suggest/preview" | python3 -m json.tool

sqlite3 data/finance.db "
SELECT id, match_kind, match_value, fingerprint_raw, target_stable_key, source
FROM coretax_mappings
ORDER BY id;
"
```

### Fix

- Remove duplicate source fingerprints from the accepted suggestion set.
- Retarget or delete the existing conflicting mapping before creating a new one.
- Recreate the missing CoreTax target row for the selected tax year, then assign again.
- Map liabilities only to liability rows and account/holding sources only to asset rows.

## Household Expense Settings Unavailable

### Symptoms

- Settings -> Household Expense shows unavailable or empty data.
- Category, recent expense, or cash pool updates fail.
- `GET /api/household/settings` returns an error.

### Likely Cause

- NAS household container is down.
- `[household].base_url` is wrong or unreachable from the Mac.
- `[household].api_key_file` is missing or contains the wrong key.
- The household API health check passes locally on NAS but not from the Mac network path.

### How To Diagnose

```bash
curl http://192.168.1.44:8088/api/household/health
ls -la household-expense/secrets/household_api.key
curl -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/household/settings
docker compose logs -f finance-api
```

On the NAS, check the household container:

```bash
cd /volume1/docker/household-expense
sudo docker compose ps
sudo docker compose logs -f household-api
```

### Fix

- Redeploy from the Mac with `bash household-expense/deploy_household.sh`.
- Confirm `config/settings.toml [household] base_url` points to the NAS household API.
- Recreate or sync `household-expense/secrets/household_api.key` if the API key changed.

### Prevention

- Keep the household health check in the deploy script.
- Keep household data in `household.db`; do not mix it directly into `finance.db`.
- Treat the finance Settings household section as an admin proxy, not the source of truth.

## Mail AI Enrichment Not Completing

### Symptoms

- Dashboard shows AI status as `pending`, `running`, or `failed`.
- `POST /api/mail/ai/test` returns 502 or validation errors.
- Recent messages still show deterministic classification but no AI summary.

### Likely Cause

- `[mail.ai].enabled` is false.
- Ollama is not reachable from Docker at `[mail.ai].base_url`.
- The configured model is not pulled locally.
- Ollama returned malformed JSON or schema-invalid values.

### How To Diagnose

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  http://127.0.0.1:8090/api/mail/ai/settings | python3 -m json.tool

sqlite3 data/agent.db "
SELECT id, status, attempts, last_error, created_at, updated_at
FROM mail_ai_queue
ORDER BY id DESC
LIMIT 20;
"

docker compose logs -f mail-agent
```

From the host, confirm Ollama and the model:

```bash
curl http://localhost:11434/api/tags
ollama pull gemma3:4b
```

### Fix

- Keep AI disabled if enrichment is not needed; existing rule/classifier/PDF routing continues to work.
- Set `base_url = "http://host.docker.internal:11434"` for Docker mail-agent access to host Ollama.
- Use the dashboard AI settings test before enabling queue processing.
- For malformed model output, inspect `last_error`; validation failures are intentionally failed and do not fire actions.

### Safety Boundary

Phase 4B is read-only. It never performs IMAP mutations, auto-reply, forwarding, deletion, unsubscribe, external webhooks, or AI-triggered iMessage actions.

Phase 4C.1 mutation primitives can still be blocked by design. Check `[agent].mode`, `[mail.imap_mutations].enabled`, dry-run status, UIDVALIDITY, and per-account IMAP capability output before treating a blocked mutation as a runtime failure.

Phase 4C.3A AI triggers are also preview-only by design. A matched AI trigger should produce `mail_processing_events.event_type='ai_trigger_matched'` with `outcome='dry_run'`; it should not move mail, mark flags, send iMessage, reply, forward, delete, unsubscribe, or call webhooks.

Phase 4D.1 adds Control Center approvals, but approval is still not execution. A matched AI trigger should create a pending `mail_action_approvals` row in `data/agent.db`; the operator must approve it and then explicitly run an execution attempt. If the result is `blocked`, inspect the execution status before treating it as a failure: common expected values are `mode_blocked`, `mutation_disabled`, `dry_run`, `unsupported`, and UIDVALIDITY/capability failures from the IMAP layer.

There is no bulk approval path. Unsupported actions such as `send_imessage`, reply, forward, delete, expunge, unsubscribe, and webhooks should remain blocked even after approval.
