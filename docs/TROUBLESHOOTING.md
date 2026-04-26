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
SELECT id, match_kind, match_value, target_stable_key, hits, last_used_tax_year
FROM coretax_mappings
ORDER BY id;
"
```

### Fix

- Unlock the field in the CoreTax SPT view if the auto-reconcile value should replace it.
- Use Review & Manual Mapping or Create from Unmatched to create a learned mapping.
- Delete and recreate stale mappings that point to a row from the wrong tax year.

### Prevention

- Treat locks as intentional. Do not bypass them in API changes.
- Learned mappings should store and use `target_stable_key`.

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
