# Code Review: `agentic-ai` Codebase

**Reviewer:** Senior engineer pass via Claude Code  
**Date:** 2026-04-17  
**Scope:** Stage 1 (bridge), Stage 2 (finance pipeline + FastAPI), Stage 3 (wealth), PWA, tests  
**Coverage gaps:** See §9 — parsers/, agent/, most of finance/api.py

---

## Executive Summary

Three bugs will crash production on the first run. Fix these before anything else.

| # | Severity | File | Line | Issue |
|---|---|---|---|---|
| 1 | CRITICAL | `finance/importer.py` | 316 | `AttributeError` on every non-dry-run import |
| 2 | CRITICAL | `scripts/batch_process.py` | 191, 212 | Same `AttributeError` — every registry write crashes |
| 3 | CRITICAL | `tests/test_backup.py` | 12 | Mock signature mismatch → `TypeError` on every test run |
| 4 | HIGH | `finance/categorizer.py` | 527 | `id()` vs tuple-key mismatch — cash withdrawals can mis-categorise |
| 5 | HIGH | `finance/backup.py` | 233 | `StrictHostKeyChecking=no` — MITM risk on NAS sync |
| 6 | HIGH | `bridge/pipeline.py` | 117 | `stop()` race — one extra cycle fires after shutdown |

---

## §2 Critical Bugs

### 2.1 `datetime.timezone` AttributeError — `finance/importer.py:316`

**Impact:** Crashes every non-dry-run import with `AttributeError: type object 'datetime' has no attribute 'timezone'`.

**Root cause:** Line 38 does `from datetime import datetime`, so the name `datetime` in this module refers to the *class*, not the *module*. Calling `datetime.timezone.utc` on the class fails.

```python
# finance/importer.py — line 38 and 316

# BAD (current)
from datetime import datetime
# ...
now = datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# GOOD (fix)
from datetime import datetime, timezone
# ...
now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
```

### 2.2 Same `AttributeError` — `scripts/batch_process.py:191, 212`

Identical bug, two occurrences in `Registry.record` and `Registry.record_zip_member`. Breaks every file-registry insert.

```python
# scripts/batch_process.py — import block and lines 191, 212

# BAD (current — same import pattern)
from datetime import datetime
# ...
now = datetime.now(datetime.timezone.utc).isoformat()   # L191, L212

# GOOD (fix both lines the same way)
from datetime import datetime, timezone
# ...
now = datetime.now(timezone.utc).isoformat()
```

### 2.3 Mock signature mismatch — `tests/test_backup.py:12`

**Impact:** `test_manual_backup_prunes_to_retention_limit` and any test that monkeypatches `backup.datetime` will raise `TypeError: now() takes 1 positional argument but 2 were given` because `finance/backup.py:89` calls `datetime.now(timezone.utc)`.

```python
# tests/test_backup.py — class FixedDateTime

# BAD (current)
@classmethod
def now(cls):
    return cls._current

# GOOD — accept (and ignore) the tz argument
@classmethod
def now(cls, tz=None):
    return cls._current
```

---

## §3 High-Severity Issues

### 3.1 `id()` vs tuple-key in seen-set — `finance/categorizer.py:527`

The comment at line 477 explicitly says *"Use value-based keys instead of id()"*, but the Helen-BCA cash-withdrawal block 50 lines later uses `id(txn)` anyway:

```python
# categorizer.py — the inconsistency

# Lines 477-502: uses tuple keys correctly
seen: set[tuple] = set()
def _txn_key(t) -> tuple:
    return (t.owner, t.account, t.date, t.amount)
# ...
seen.add(_txn_key(txn))   # ← correct

# Lines 527-537: uses id() — WRONG
for txn in transactions:
    if id(txn) in seen:      # ← id() will never match tuple keys above
        continue
    # ...
    seen.add(id(txn))        # ← adds int, previous adds were tuples
```

**Effect:** A txn already marked Transfer (added to `seen` as a tuple) will not be skipped by the `id(txn) in seen` check, so it can be re-categorised as "Household". This silently corrupts category data.

**Fix:**

```python
# Replace lines 527–537 with:
for txn in transactions:
    if _txn_key(txn) in seen:
        continue
    if (txn.owner == h_owner and txn.account == h_account
            and txn.amount < 0
            and txn.category == "Cash Withdrawal"):
        desc_upper = (getattr(txn, "raw_description", "") or "").upper()
        if any(hint in desc_upper for hint in _ATM_HINTS):
            txn.category = "Household"
            txn.merchant = "Cash (Household)"
            matched += 1
            seen.add(_txn_key(txn))
```

### 3.2 `StrictHostKeyChecking=no` — `finance/backup.py:233`

```python
ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", "-p", "68"]
```

Tailscale provides network-level ACLs but not TLS certificate pinning. A compromised node on the Tailnet (or a misconfigured Tailscale key reuse) can MITM the SSH session and swap the database backup for a poisoned one.

**Fix:** Pin the NAS host key.

```bash
# Run once on the Mac to capture the NAS fingerprint
ssh-keyscan -p 68 ds920plus >> ~/.ssh/known_hosts
```

```python
# finance/backup.py
ssh_base = ["ssh", "-o", "StrictHostKeyChecking=yes", "-o",
            f"UserKnownHostsFile={Path.home() / '.ssh' / 'known_hosts'}",
            "-p", "68"]
```

Document this in `SYSTEM_DESIGN.md` under the NAS sync section.

### 3.3 Pipeline stop() race — `bridge/pipeline.py:117-118`

```python
# pipeline.py — inside _run_cycle (the finally block)
finally:
    self._running = False
    _lock.release()
    if self.is_enabled():         # ← checked AFTER stop() may have been called
        self._schedule(...)       # ← fires another cycle
```

If `stop()` sets `self._enabled = False` *after* `self.is_enabled()` is evaluated, one extra cycle fires. Low probability but non-zero on a loaded machine.

**Fix:** Check a separate `_stop_requested` flag set atomically before acquiring the lock:

```python
def stop(self):
    self._stop_requested = True
    self._enabled = False
    # ...

# In finally block:
finally:
    self._running = False
    _lock.release()
    if self.is_enabled() and not self._stop_requested:
        self._schedule(...)
```

### 3.4 Scripts-as-library anti-pattern — `bridge/pipeline.py`

```python
from scripts.batch_process import Registry   # ← production code importing from scripts/
```

`scripts/` is conventionally a directory of thin entrypoints, not an importable library. This makes `Registry` untestable in isolation and forces Docker containers to carry the full `scripts/` tree.

**Fix:** Move `Registry` (and related helpers) to `finance/batch.py` or `finance/registry.py`. Make `scripts/batch_process.py` a one-liner that imports and calls the library.

---

## §4 Security Findings (OWASP)

| OWASP ID | Risk | Location | Status |
|---|---|---|---|
| A01 Broken Access Control | API key baked into JS bundle | `pwa/src/api/client.js:7-11` | **Accepted risk** — Tailscale is the real auth boundary; documented in comments. Verify Tailscale ACLs annually. |
| A02 Cryptographic Failures | `StrictHostKeyChecking=no` | `finance/backup.py:233` | **Open** — see §3.2 |
| A03 Injection — SQL | Parameterized queries | `finance/*`, `bridge/*` | **Mitigated** — spot-checked throughout; recommend `bandit -r finance/ bridge/` to confirm coverage |
| A03 Injection — Shell | `shlex.quote` on remote path | `finance/backup.py:243` | **Mitigated** — `cat > {quoted_path}` is correctly escaped |
| A03 Injection — AppleScript | Recipient regex + argv[1] | `bridge/messages_source.py` | **Mitigated** — body passed as subprocess argv, not interpolated |
| A03 Injection — Path Traversal | `os.path.realpath` check | `bridge/pdf_handler.py:109-128` | **Mitigated** — correct containment check |
| A05 Security Misconfiguration | CORS, security headers | `finance/api.py` | **Good** — CORS rejects `*` + `allow_credentials`, security headers present; CSP scoped to `/app` only — verify `/api` responses don't need it |
| A07 Authentication Failures | Bearer token file perms | `bridge/auth.py` | **Good** — `hmac.compare_digest` + length check + chmod 600 enforcement |
| A08 Software Integrity | No retry cap on failing PDFs | `bridge/pipeline.py` | **Open** — same PDF can loop forever; see §5 |
| A09 Logging | `log_message` suppressed | `bridge/server.py:343` | Intentional for noise; ensure auth failures still reach the log (they do via `state.log_request`) |

### Additional security observations

**Mixed naive/aware datetimes in `finance/backup.py`:**

```python
# backup.py:89 — aware
ts = datetime.now(timezone.utc).strftime(...)

# ensure_auto_backups (later) — naive
last_backup_time = datetime.fromisoformat(last_ts)  # loses tzinfo
if datetime.now() - last_backup_time > timedelta(...):  # naive vs naive, ok by accident
```

This works today because both sides happen to be naive, but if `last_ts` ever carries a `+00:00` suffix (e.g. from a future schema change), the comparison raises `TypeError`. Unify to aware throughout:

```python
# Use aware datetimes everywhere in backup.py
from datetime import datetime, timezone, timedelta

last_backup_time = datetime.fromisoformat(last_ts)
if last_backup_time.tzinfo is None:
    last_backup_time = last_backup_time.replace(tzinfo=timezone.utc)
now_utc = datetime.now(timezone.utc)
if now_utc - last_backup_time > timedelta(...):
```

---

## §5 Medium — Maintainability & Performance

### 5.1 Duplicated SQLite connection opens in `bridge/pdf_handler.py`

`_upsert_closing_balance`, `_upsert_cc_liability`, `_upsert_bond_holdings`, `_upsert_fund_holdings`, and `_upsert_investment_holdings` each open their own connection inside the function. This causes 5 separate connection allocations per PDF parse. Extract a shared context manager:

```python
# Proposed helper (add near top of pdf_handler.py)
from contextlib import contextmanager

@contextmanager
def _db_conn(db_path: str):
    con = sqlite3.connect(db_path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

# Then each _upsert_* becomes:
def _upsert_closing_balance(db_path: str, ..., con=None):
    ctx = nullcontext(con) if con else _db_conn(db_path)
    with ctx as c:
        c.execute(...)
```

Pass the connection from the caller to batch all upserts into one transaction per PDF.

### 5.2 Dead `_ALLOWED_TABLES` check — `finance/importer.py:432`

```python
# importer.py (current — iterates the same tuple it validates against)
_ALLOWED_TABLES = ("transactions", "merchant_aliases", ...)
for t in _ALLOWED_TABLES:
    if t not in _ALLOWED_TABLES:   # ← always False; dead code
        raise ValueError(...)
```

Replace with a membership check at the call site, or delete entirely since callers already validate.

### 5.3 No retry cap on failing PDFs — `bridge/pipeline.py`

A corrupted or unrecognised PDF will fail every cycle and spam the logs. Track attempt count in the Registry and quarantine after N failures:

```python
# After processing, in _process_candidate:
if outcome["status"] == "error":
    attempts = registry.failure_count(sha256)
    if attempts >= MAX_FAILURES:  # e.g. 3
        shutil.move(pdf_path, FAILED_DIR / pdf_path.name)
        log.warning("Quarantined after %d failures: %s", attempts, pdf_path.name)
```

### 5.4 `"✓ Complete"` string match — `bridge/pipeline.py`

`_find_completed_months` matches on the string `"✓ Complete"` from Registry records. A whitespace or encoding change breaks it silently. Use a dedicated status enum or column instead.

### 5.5 Hardcoded 120-month gap-fill cap — `bridge/pdf_handler.py:1237`

```python
for _ in range(120):   # 10-year safety cap
```

Move to a config key (`pdf.gap_fill_max_months`, default 120) so it can be changed without a deploy.

### 5.6 Prompt size not bounded — `bridge/pdf_verify.py:312`

Up to 120 transactions are embedded verbatim in the LLM verification prompt. For a large statement this is ~6-10k tokens. Add a truncation guard:

```python
MAX_VERIFY_ROWS = 50
if len(transactions) > MAX_VERIFY_ROWS:
    log.warning("Truncating verify prompt: %d → %d rows", len(transactions), MAX_VERIFY_ROWS)
    transactions = transactions[:MAX_VERIFY_ROWS]
```

### 5.7 Silently capped timeout — `bridge/pdf_verify.py:264`

```python
# _ollama_generate caps caller's timeout to 60s without logging
timeout = min(timeout, 60)
```

The caller's config (`verify_timeout_seconds`) is silently overridden. Either remove the cap or log a warning so operators know their config value is being ignored.

### 5.8 Dead code — `bridge/pdf_unlock.py`

`_escape_applescript_string` is defined but never called. Delete it.

### 5.9 Hardcoded person name — `finance/importer.py`

```python
def sync_grogol_2_from_transactions(...):
    person_name = "TEGUH PRANOTO CHEN"   # ← config, not code
```

Move to `config/settings.toml` under `[finance]` or `[owners]`.

### 5.10 Monolithic `Settings.vue` (1,773 lines)

Every domain in the app is crammed into one component: categories, health check, mobile cache, import pipeline, PDF workspace, backup, NAS sync, AI refinement, About. Split into sub-components. Suggested breakdown:

```
pwa/src/views/settings/
  CategoryEditor.vue
  HealthCard.vue
  MobileCacheCard.vue
  ImportPipelineCard.vue
  BackupNasCard.vue
  AIRefinementCard.vue
  AboutCard.vue
Settings.vue   # thin orchestrator that imports and lays out the above
```

### 5.11 Deprecated `navigator.platform` — `pwa/src/views/Settings.vue`

```javascript
// Current — deprecated
const isMac = navigator.platform.toUpperCase().includes('MAC')

// Better
const isMac = navigator.userAgentData
  ? navigator.userAgentData.platform.includes('mac')
  : navigator.platform.toUpperCase().includes('MAC')   // fallback for Safari
```

### 5.12 Auto carry-forward side-effect on load — `pwa/src/views/Holdings.vue`

The view silently calls `carryForwardHoldings` on mount (skipped for read-only, which is good). But a page refresh triggers a write. Add an explicit "Carry Forward" button, or at minimum log that it happened so users can see it in audit trails.

---

## §6 Low — Readability & Style

### 6.1 Docstring drift — `finance/models.py:69`

```python
def make_hash(...) -> str:
    """
    16-hex-char dedup fingerprint.   ← wrong; actual result is 32 chars
    ...
    """
    return hashlib.sha256(key.encode()).hexdigest()[:32]   # 32 chars
```

Fix the docstring to say "32-hex-char".

### 6.2 Century heuristic silently expires in 2080 — `finance/models.py`

```python
yr_full = ("19" if yr_raw >= 80 else "20") + str(yr_raw)
```

Add a TODO so it doesn't silently misparse 2080+ dates:

```python
# TODO: revisit before 2080 — heuristic breaks for yr_raw >= 80 in 2080+
yr_full = ("19" if yr_raw >= 80 else "20") + str(yr_raw)
```

### 6.3 `is_encrypted` uses error-string matching — `bridge/pdf_handler.py:81`

```python
# Fragile — depends on pikepdf's English error message
except pikepdf.PasswordError as e:
    if "encrypted" in str(e).lower():
        return True
```

`pikepdf.PasswordError` is already specific enough — just catching it implies the file is encrypted. Remove the string match:

```python
except pikepdf.PasswordError:
    return True
```

### 6.4 Cache TTL is aggressive for a finance app — `pwa/src/api/client.js:4`

```javascript
const DEFAULT_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000  // 24 hours
```

This is intentional for mobile offline use but could serve stale balances all day. Add a comment explaining this, and consider a shorter TTL (2–4 hours) with a manual refresh option already wired in (the "Refresh" button in Settings triggers `refreshReferenceData`).

### 6.5 Validate-on-all-views pattern — `pwa/src/views/GroupDrilldown.vue:97-109`

GroupDrilldown has good JSON query-param validation (200 items, 200 chars each). Mirror this in other views that accept query params (e.g. `transactions` view filters).

---

## §7 Refactor Snippets

### 7.1 Timezone fix (applies to 3 files)

```python
# Before (all three files)
from datetime import datetime
now = datetime.now(datetime.timezone.utc)    # AttributeError

# After
from datetime import datetime, timezone
now = datetime.now(timezone.utc)             # correct
```

Files: `finance/importer.py:38+316`, `scripts/batch_process.py` (import block, L191, L212).

### 7.2 Categorizer seen-set (fix `id()` usage)

```python
# Before — finance/categorizer.py:527-537
for txn in transactions:
    if id(txn) in seen:
        continue
    ...
    seen.add(id(txn))

# After — consistent tuple key
for txn in transactions:
    if _txn_key(txn) in seen:
        continue
    ...
    seen.add(_txn_key(txn))
```

### 7.3 Test mock signature

```python
# Before — tests/test_backup.py:12
@classmethod
def now(cls):
    return cls._current

# After
@classmethod
def now(cls, tz=None):
    return cls._current
```

### 7.4 Pipeline shutdown flag

```python
# bridge/pipeline.py — add to __init__
self._stop_requested = False

# In stop():
self._stop_requested = True
self._enabled = False

# In finally block of _run_cycle:
finally:
    self._running = False
    _lock.release()
    if self.is_enabled() and not self._stop_requested:
        self._schedule(int(self.config.get("scan_interval_seconds", 14400)))
```

### 7.5 SSH host key pinning

```python
# finance/backup.py:233 — before
ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", "-p", "68"]

# After
ssh_base = ["ssh", "-o", "StrictHostKeyChecking=yes", "-p", "68"]
```

Run once to seed the known hosts file:
```bash
ssh-keyscan -p 68 ds920plus >> ~/.ssh/known_hosts
```

---

## §8 Verification Checklist

For the engineer or model picking this up:

- [ ] **Reproduce tz bug:** `python3 -m finance.importer --dry-run` on a fresh DB — this should work. Then remove `--dry-run` and confirm it crashes with `AttributeError` at L316 before the fix, and passes after.
- [ ] **Run tests:** `pytest tests/test_backup.py -v` — confirm `test_manual_backup_prunes_to_retention_limit` currently fails with `TypeError`; confirm it passes after the `now(cls, tz=None)` fix.
- [ ] **Batch process:** `python3 scripts/batch_process.py --dry-run` — currently crashes at registry insert L191. Confirm fix clears it.
- [ ] **Categorizer:** Write a unit test for the Helen-BCA block: create a Transfer txn and a Household-candidate txn with the same owner/account, run `_match_internal_transfers` followed by the ATM block, confirm the Transfer txn's category is NOT overwritten.
- [ ] **Static analysis:** `bandit -r finance/ bridge/ scripts/ -ll` and `ruff check .` — address new findings before merging.
- [ ] **NAS sync:** After setting `StrictHostKeyChecking=yes`, confirm `sync_to_nas` still reaches the NAS via `python3 -c "from finance.backup import sync_to_nas; print(sync_to_nas('data/finance.db', force=True))"`.
- [ ] **Pipeline stop:** Add a brief sleep in `run_cycle` during development and call `stop()` concurrently — confirm no extra cycle fires after stop.
- [ ] **CSP header:** `curl -I http://localhost:8090/app` — confirm `Content-Security-Policy` is present. `curl -I http://localhost:8090/api/health` — confirm no CSP header leaks on API routes.
- [ ] **PWA render:** Build the PWA (`npm run build` in `pwa/`), load it in Chrome DevTools, confirm the Settings page renders all sections and no JS console errors.

---

## §9 Coverage Gaps — Follow-Up Pass Needed

The following areas were NOT fully reviewed. A second pass (cheaper model, same rubric) should cover:

| Area | Path | Notes |
|---|---|---|
| Finance API — bulk | `finance/api.py:250-end` (~4,750 lines) | Only first 250 lines reviewed; wealth endpoints, holdings, liabilities routes unaudited |
| Bank parsers | `parsers/` | 10+ bank-specific parsers; focus on IDR number parsing edge cases and date handling |
| Exporters | `exporters/` | XLS writer; check for openpyxl memory issues on large statements |
| Mail agent | `agent/app/` | Orchestrator, classifier cascade (Ollama → Anthropic), alert dedup |
| PWA — other views | `pwa/src/views/` | `Dashboard.vue`, `Transactions.vue`, `Summary.vue` etc. unreviewed |
| Infra | `Dockerfile`, `docker-compose.yml`, launchd plists | Check image layering, secret injection, health-check config |
| Full security scan | all | Run `bandit -r .` and `npm audit` in `pwa/` — review all findings |

---

## Changelog

| Date | Reviewer | Summary |
|---|---|---|
| 2026-04-17 | Senior pass (Claude Code) | Initial review — 3 critical, 3 high, 10+ medium, full §9 gap list |
