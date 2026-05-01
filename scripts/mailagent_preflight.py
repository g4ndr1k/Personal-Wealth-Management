#!/usr/bin/env python3
"""
mailagent_preflight.py — read-only inventory of the mail-agent stack.

Usage (from repo root):
    python3 scripts/mailagent_preflight.py

Outputs a markdown report to stdout and saves it to docs/preflight-<date>.md.
Nothing is written to any database or runtime state.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path

# ── Repo root ─────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # pip fallback
    except ImportError:
        tomllib = None  # type: ignore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(s: str) -> str:
    return f"✅ {s}"

def _warn(s: str) -> str:
    return f"⚠️  {s}"

def _fail(s: str) -> str:
    return f"❌ {s}"

def _section(title: str) -> str:
    return f"\n## {title}\n"

def _load_toml(path: Path) -> dict | None:
    if tomllib is None:
        return None
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


# ── 1. Docker ─────────────────────────────────────────────────────────────────

def inspect_docker() -> list[str]:
    lines: list[str] = [_section("Docker — docker-compose.yml")]
    compose_path = REPO / "docker-compose.yml"
    if not compose_path.exists():
        lines.append(_fail("docker-compose.yml not found"))
        return lines

    raw = compose_path.read_text()
    lines.append(f"**File:** `{compose_path}`\n")

    # Parse with PyYAML if available, else do best-effort regex
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(raw)
        services = cfg.get("services", {})
    except ImportError:
        services = _parse_compose_fallback(raw)
        cfg = {"services": services}

    lines.append(f"**Services found:** {', '.join(services) or '(none)'}\n")

    for svc_name, svc in services.items():
        lines.append(f"### Service: `{svc_name}`")
        restart = svc.get("restart", "(not set)")
        lines.append(f"- **restart:** `{restart}`")
        if restart not in ("unless-stopped", "always", "on-failure"):
            lines.append(f"  {_warn('restart policy may not auto-recover')}")

        hc = svc.get("healthcheck")
        if hc:
            lines.append(f"- **healthcheck:** interval={hc.get('interval','?')} "
                         f"timeout={hc.get('timeout','?')} retries={hc.get('retries','?')} "
                         f"start_period={hc.get('start_period','?')}")
        else:
            lines.append(f"- **healthcheck:** {_warn('none configured')}")

        vols = svc.get("volumes", [])
        if vols:
            lines.append(f"- **volumes ({len(vols)}):**")
            for v in vols:
                lines.append(f"  - `{v}`")
        else:
            lines.append("- **volumes:** (none)")
        lines.append("")

    return lines


def _parse_compose_fallback(raw: str) -> dict:
    """Small docker-compose parser for the fields this report needs.

    This intentionally avoids a PyYAML dependency for read-only preflight
    environments. It handles top-level services and their restart,
    healthcheck, and volumes fields.
    """
    services: dict[str, dict] = {}
    current: str | None = None
    in_services = False
    in_volumes = False
    in_healthcheck = False

    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            in_services = stripped == "services:"
            current = None
            in_volumes = False
            in_healthcheck = False
            continue

        if not in_services:
            continue

        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            services[current] = {}
            in_volumes = False
            in_healthcheck = False
            continue

        if current is None or indent < 4:
            continue

        svc = services[current]
        if indent == 4:
            in_volumes = stripped == "volumes:"
            in_healthcheck = stripped == "healthcheck:"
            if stripped.startswith("restart:"):
                svc["restart"] = stripped.split(":", 1)[1].strip().strip('"')
            elif in_volumes:
                svc.setdefault("volumes", [])
            elif in_healthcheck:
                svc.setdefault("healthcheck", {})
            continue

        if in_volumes and stripped.startswith("- "):
            svc.setdefault("volumes", []).append(stripped[2:].strip().strip('"'))
        elif in_healthcheck and ":" in stripped:
            key, value = stripped.split(":", 1)
            svc.setdefault("healthcheck", {})[key.strip()] = value.strip().strip('"')

    return services


# ── 2. FastAPI ────────────────────────────────────────────────────────────────

def inspect_fastapi() -> list[str]:
    lines: list[str] = [_section("FastAPI — finance/api.py")]
    api_path = REPO / "finance" / "api.py"
    server_path = REPO / "finance" / "server.py"

    toml_cfg = _load_toml(REPO / "config" / "settings.toml")
    fastapi_cfg = (toml_cfg or {}).get("fastapi", {})
    host = fastapi_cfg.get("host", "127.0.0.1")
    port = fastapi_cfg.get("port", 8090)

    lines.append(f"- **Entrypoint module:** `finance.server` → `finance/server.py`")
    lines.append(f"- **Bound host:port:** `{host}:{port}`")

    # Extract route prefixes from api.py source
    if api_path.exists():
        src = api_path.read_text()
        routes = re.findall(r'(?:@app\.|router\.)\w+\(\s*["\']([^"\']+)["\']', src)
        mail_routes = [r for r in routes if r.startswith("/api/mail/")]
        lines.append(f"\n**Routes found ({len(routes)} total):**")
        for r in sorted(set(routes)):
            collision = " ← `/api/mail/*` collision!" if r.startswith("/api/mail/") else ""
            lines.append(f"  - `{r}`{collision}")
        if not mail_routes:
            lines.append(f"\n{_ok('No /api/mail/* route collisions detected')}")
        else:
            lines.append(f"\n{_warn(f'/api/mail/* routes present: {mail_routes}')}")
    else:
        lines.append(_warn("finance/api.py not found"))

    return lines


# ── 3. Bridge ─────────────────────────────────────────────────────────────────

def inspect_bridge() -> list[str]:
    lines: list[str] = [_section("Bridge — bridge/server.py + launchd plist")]

    # Declared routes from source
    srv_path = REPO / "bridge" / "server.py"
    if srv_path.exists():
        src = srv_path.read_text()
        paths = sorted(set(re.findall(r'path == ["\']([^"\']+)["\']', src)))
        paths += sorted(set(re.findall(r'path\.startswith\(["\']([^"\']+)["\']', src)))
        lines.append("**Declared routes (source scan):**")
        for p in sorted(set(paths)):
            lines.append(f"  - `{p}`")
    else:
        lines.append(_warn("bridge/server.py not found"))

    # Launchd plist
    plist_path = REPO / "launchd" / "com.agentic.bridge.plist"
    lines.append(f"\n**Plist:** `{plist_path}`")
    if plist_path.exists():
        raw_plist = plist_path.read_text()
        wd_match = re.search(r'<key>WorkingDirectory</key>\s*<string>([^<]+)</string>', raw_plist)
        out_match = re.search(r'<key>StandardOutPath</key>\s*<string>([^<]+)</string>', raw_plist)
        err_match = re.search(r'<key>StandardErrorPath</key>\s*<string>([^<]+)</string>', raw_plist)
        ka_dict = bool(re.search(r'<key>KeepAlive</key>\s*<dict>', raw_plist))
        ka_true = bool(re.search(r'<key>KeepAlive</key>\s*<true/>', raw_plist))
        throttle = re.search(r'<key>ThrottleInterval</key>\s*<integer>(\d+)</integer>', raw_plist)

        wd = wd_match.group(1) if wd_match else "(not set)"
        wd_exists = Path(wd).exists() if wd_match else False
        lines.append(f"  - WorkingDirectory: `{wd}` → {'exists' if wd_exists else _warn('NOT FOUND')}")

        out = out_match.group(1) if out_match else "(not set)"
        out_exists = Path(out).parent.exists() if out_match else False
        lines.append(f"  - StandardOutPath: `{out}` → {'parent dir exists' if out_exists else _warn('parent dir missing')}")

        err = err_match.group(1) if err_match else "(not set)"
        lines.append(f"  - StandardErrorPath: `{err}`")

        if ka_dict:
            # Extract SuccessfulExit / Crashed from plist dict block
            se_match = re.search(r'<key>SuccessfulExit</key>\s*<(true|false)/>', raw_plist)
            cr_match = re.search(r'<key>Crashed</key>\s*<(true|false)/>', raw_plist)
            se = se_match.group(1) if se_match else "?"
            cr = cr_match.group(1) if cr_match else "?"
            lines.append(f"  - KeepAlive: dict (SuccessfulExit={se}, Crashed={cr})")
            if se == "false" and cr == "true":
                lines.append(f"    {_ok('KeepAlive policy is hardened')}")
            else:
                lines.append(f"    {_warn('KeepAlive dict values may not be optimal (want SuccessfulExit=false, Crashed=true)')}")
        elif ka_true:
            lines.append(f"  - KeepAlive: `<true/>` — {_warn('simple boolean; consider dict with SuccessfulExit=false, Crashed=true')}")
        else:
            lines.append(f"  - KeepAlive: {_warn('not set')}")

        ti = throttle.group(1) if throttle else None
        if ti:
            ti_int = int(ti)
            status = _ok(f"ThrottleInterval={ti_int}s") if ti_int >= 30 else _warn(f"ThrottleInterval={ti_int}s (< 30s recommended)")
            lines.append(f"  - {status}")
        else:
            lines.append(f"  - ThrottleInterval: {_warn('not set')}")
    else:
        lines.append(_warn("plist not found"))

    # Live /health probe (optional — bridge may not be running)
    toml_cfg = _load_toml(REPO / "config" / "settings.toml")
    bridge_cfg = (toml_cfg or {}).get("bridge", {})
    bridge_host = bridge_cfg.get("host", "127.0.0.1")
    if bridge_host in ("0.0.0.0", ""):
        bridge_host = "127.0.0.1"
    bridge_port = bridge_cfg.get("port", 9100)

    # Try to read token for authenticated probe
    token: str | None = None
    token_file = REPO / "secrets" / "bridge.token"
    if token_file.exists():
        try:
            token = token_file.read_text().strip()
        except Exception:
            pass

    lines.append(f"\n**Live probe:** GET http://{bridge_host}:{bridge_port}/health")
    try:
        req = urllib.request.Request(
            f"http://{bridge_host}:{bridge_port}/health",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read())
        lines.append(_ok(f"Bridge responded: {json.dumps(body, default=str)}"))
    except urllib.error.HTTPError as e:
        lines.append(_warn(f"Bridge HTTP {e.code}: {e.reason}"))
    except Exception as e:
        lines.append(_warn(f"Bridge not reachable: {type(e).__name__}: {e}"))

    return lines


# ── 4. SQLite schemas ─────────────────────────────────────────────────────────

def _describe_table(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            return [f"  - (table `{table}` is empty or not found)"]
        return [f"  - `{r[1]}` {r[2]}" + (" NOT NULL" if r[3] else "") + (" PK" if r[5] else "")
                for r in rows]
    except Exception as e:
        return [f"  - ERROR: {e}"]


def inspect_sqlite() -> list[str]:
    lines: list[str] = [_section("SQLite — agent state + pdf_jobs.db")]

    # agent/app/state.py schema (declared — not from live DB)
    state_path = REPO / "agent" / "app" / "state.py"
    if state_path.exists():
        src = state_path.read_text()
        tables = re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', src)
        lines.append("**Tables declared in `agent/app/state.py`:**")
        for t in tables:
            lines.append(f"  - `{t}`")
        lines.append("")

        # Try live agent DB
        toml_cfg = _load_toml(REPO / "config" / "settings.toml")
        agent_db = REPO / "data" / "agent.db"
        lines.append(f"**Live agent DB:** `{agent_db}`")
        if agent_db.exists():
            try:
                conn = sqlite3.connect(f"file:{agent_db}?mode=ro", uri=True, timeout=5)
                for tbl in [
                    "processed_messages",
                    "alerts",
                    "agent_flags",
                    "mail_rule_ai_draft_audit",
                    "mail_rule_ai_golden_probe_runs",
                ]:
                    col_lines = _describe_table(conn, tbl)
                    lines.append(f"\n  **`{tbl}`** columns:")
                    lines.extend(col_lines)
                    try:
                        cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                        lines.append(f"  → {cnt[0]} rows")
                    except sqlite3.OperationalError:
                        lines.append("  → row count unavailable")
                conn.close()
            except Exception as e:
                lines.append(_warn(f"Cannot open agent DB: {e}"))
        else:
            lines.append(_warn("agent.db not found (agent never ran or path differs)"))
    else:
        lines.append(_warn("agent/app/state.py not found"))

    # pdf_jobs.db
    toml_cfg = _load_toml(REPO / "config" / "settings.toml")
    pdf_db_path_str = (toml_cfg or {}).get("pdf", {}).get("jobs_db", str(REPO / "data" / "pdf_jobs.db"))
    pdf_db = Path(pdf_db_path_str)
    lines.append(f"\n**pdf_jobs.db:** `{pdf_db}`")
    if pdf_db.exists():
        try:
            conn = sqlite3.connect(f"file:{pdf_db}?mode=ro", uri=True, timeout=5)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            lines.append(f"  Tables: {[r[0] for r in tables]}")
            for (tbl,) in tables:
                cnt = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()
                lines.append(f"  - `{tbl}`: {cnt[0]} rows")
                col_lines = _describe_table(conn, tbl)
                lines.extend(col_lines)
            conn.close()
        except Exception as e:
            lines.append(_warn(f"Cannot open pdf_jobs.db: {e}"))
    else:
        lines.append(_warn("pdf_jobs.db not found (PDF pipeline never ran or path differs)"))

    return lines


# ── 5. Config ─────────────────────────────────────────────────────────────────

def inspect_config() -> list[str]:
    lines: list[str] = [_section("Config — config/settings.toml")]
    cfg_path = REPO / "config" / "settings.toml"
    if not cfg_path.exists():
        lines.append(_fail("config/settings.toml not found"))
        return lines

    if tomllib is None:
        lines.append(_warn("tomllib/tomli not available — showing raw text excerpt"))
        raw = cfg_path.read_text()
        lines.append("```toml")
        lines.append(raw[:4000])
        lines.append("```")
        return lines

    cfg = _load_toml(cfg_path)
    sections = ["bridge", "mail", "imessage", "classifier", "agent", "ollama", "pdf"]
    for sec in sections:
        data = cfg.get(sec)
        if data is None:
            lines.append(f"### `[{sec}]` — {_warn('section not found')}")
            continue
        lines.append(f"### `[{sec}]`")
        for k, v in data.items():
            # Redact secrets
            display = "***" if any(x in k.lower() for x in ("token", "key", "secret", "password", "pass")) else repr(v)
            lines.append(f"  - `{k}` = {display}")
        lines.append("")

    mutation_cfg = cfg.get("mail", {}).get("imap_mutations", {})
    rule_ai_cfg = cfg.get("mail", {}).get("rule_ai", {})
    lines.append("### Phase 4F rule AI probe")
    if rule_ai_cfg.get("enabled") is True:
        lines.append(_warn("mail.rule_ai.enabled=true"))
    else:
        lines.append(_ok("mail.rule_ai.enabled=false or unset"))
    lines.append(f"  - `provider` = {repr(rule_ai_cfg.get('provider', 'ollama'))}")
    lines.append(f"  - `model` = {repr(rule_ai_cfg.get('model', 'qwen2.5:7b-instruct-q4_K_M'))}")
    lines.append(f"  - `base_url` = {repr(rule_ai_cfg.get('base_url', 'http://host.docker.internal:11434'))}")
    lines.append(f"  - `timeout_seconds` = {repr(rule_ai_cfg.get('timeout_seconds', 30))}")
    golden_probe = REPO / "scripts" / "mail_rule_ai_golden_probe.py"
    api_mail_path = REPO / "agent" / "app" / "api_mail.py"
    if golden_probe.exists():
        lines.append(_ok("Rule AI golden probe available at scripts/mail_rule_ai_golden_probe.py"))
        lines.append("  - Preflight does not run the golden probe, call Ollama, or call the draft endpoint.")
    else:
        lines.append(_warn("Rule AI golden probe script not found"))
    if api_mail_path.exists() and "/rules/ai/golden-probe" in api_mail_path.read_text():
        lines.append(_ok("Rule AI golden probe endpoint declared at POST /api/mail/rules/ai/golden-probe"))
        lines.append("  - Preflight does not call the golden probe endpoint.")
    else:
        lines.append(_warn("Rule AI golden probe endpoint not found"))
    lines.append("")

    lines.append("### Phase 4E.2 execution safety")
    verifier_path = REPO / "agent" / "app" / "action_verification.py"
    executor_path = REPO / "agent" / "app" / "action_execution.py"
    lines.append(
        _ok("Final read-only verification module present")
        if verifier_path.exists()
        else _fail("Final read-only verification module missing"))
    lines.append(
        _ok("Mock execution module present")
        if executor_path.exists()
        else _fail("Mock execution module missing"))
    if mutation_cfg.get("enabled") is True:
        lines.append(_warn("mail.imap_mutations.enabled=true"))
    else:
        lines.append(_ok("mail.imap_mutations.enabled=false"))
    if mutation_cfg.get("dry_run_default") is True:
        lines.append(_ok("dry_run_default=true"))
    else:
        lines.append(_warn("dry_run_default=false"))
    for key in (
            "allow_mark_read", "allow_mark_unread",
            "allow_add_label", "allow_move_to_folder"):
        value = mutation_cfg.get(key)
        if value is True and not mutation_cfg.get("enabled"):
            lines.append(_warn(f"{key}=true while global mutations are disabled"))
        elif value is True:
            lines.append(_warn(f"{key}=true"))
        else:
            lines.append(_ok(f"{key}=false"))
    lines.append(_ok("Preflight performs no mailbox mutation"))
    lines.append("")

    return lines


# ── 6. Filesystem ─────────────────────────────────────────────────────────────

def inspect_filesystem() -> list[str]:
    lines: list[str] = [_section("Filesystem")]

    nas_path = Path("/Volumes/Synology/mailagent")
    lines.append(f"**NAS mailagent mount:** `{nas_path}`")
    if nas_path.exists():
        lines.append(_ok("Path exists"))
    elif Path("/Volumes/Synology").exists():
        lines.append(_warn("/Volumes/Synology exists but /Volumes/Synology/mailagent does not"))
    else:
        lines.append(_warn("/Volumes/Synology not mounted"))

    banks_toml = REPO / "secrets" / "banks.toml"
    lines.append(f"\n**secrets/banks.toml:** `{banks_toml}`")
    if not banks_toml.exists():
        lines.append(_warn("File not found"))
    else:
        try:
            _ = banks_toml.read_text()
            lines.append(_ok("Readable"))
        except PermissionError:
            lines.append(_fail("Permission denied"))
        except Exception as e:
            lines.append(_warn(f"Read error: {e}"))

    return lines


# ── Main ──────────────────────────────────────────────────────────────────────

def build_report() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sections: list[list[str]] = [
        [f"# Mail-Agent Preflight Report\n",
         f"**Generated:** {ts}  ",
         f"**Repo:** `{REPO}`  ",
         f"**Python:** {sys.version.split()[0]}  "],
        inspect_docker(),
        inspect_fastapi(),
        inspect_bridge(),
        inspect_sqlite(),
        inspect_config(),
        inspect_filesystem(),
    ]
    all_lines: list[str] = []
    for sec in sections:
        all_lines.extend(sec)
        all_lines.append("")
    return "\n".join(all_lines)


def main():
    report = build_report()
    print(report)

    docs_dir = REPO / "docs"
    docs_dir.mkdir(exist_ok=True)
    out_path = docs_dir / f"preflight-{date.today().isoformat()}.md"
    out_path.write_text(report)
    print(f"\n--- Report saved to {out_path} ---", file=sys.stderr)


if __name__ == "__main__":
    main()
