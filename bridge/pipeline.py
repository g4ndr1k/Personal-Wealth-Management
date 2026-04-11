from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge.pdf_handler import run_pdf_pipeline_file
from finance.categorizer import Categorizer
from finance.config import (
    get_anthropic_finance_config,
    get_finance_config,
    get_ollama_finance_config,
    get_sheets_config,
    load_config,
)
from finance.importer import run as importer_run
from finance.pdf_log_sync import DEFAULT_REGISTRY_DB, TOTAL_EXPECTED, build_log_rows
from finance.sheets import SheetsClient
from finance.sync import sync as sync_run
from scripts.batch_process import Registry, is_stable, sha256_file

log = logging.getLogger(__name__)

_lock = threading.Lock()


@dataclass
class CandidateFile:
    path: Path
    sha256: str
    state: str


class PipelineRunner:
    def __init__(self, settings: dict, bridge_state, messages):
        self.settings = settings
        self.bridge_state = bridge_state
        self.messages = messages
        self.config = settings.get("pipeline", {})
        self.registry = Registry(DEFAULT_REGISTRY_DB)
        self._timer: threading.Timer | None = None
        self._running = False
        self._next_scheduled_at: str | None = None
        self._last_run_at: str | None = None
        self._last_result: dict | None = None

    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def status(self) -> dict:
        return {
            "status": "running" if self._running else "idle",
            "last_run_at": self._last_run_at,
            "last_result": self._last_result,
            "next_scheduled_at": self._next_scheduled_at,
        }

    def start(self):
        if not self.is_enabled():
            return
        delay = int(self.config.get("startup_delay_seconds", 60))
        self._schedule(delay)

    def stop(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._next_scheduled_at = None

    def trigger(self, trigger: str = "manual") -> dict:
        if not self.is_enabled():
            return {"status": "disabled"}
        if not _lock.acquire(blocking=False):
            return {"status": "already_running"}
        thread = threading.Thread(target=self._run_cycle_guarded, args=(trigger,), daemon=True)
        thread.start()
        return {"status": "started"}

    def _schedule(self, delay_seconds: int):
        if self._timer:
            self._timer.cancel()
        self._next_scheduled_at = (
            datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        ).isoformat()
        self._timer = threading.Timer(delay_seconds, lambda: self.trigger("scheduled"))
        self._timer.daemon = True
        self._timer.start()

    def _run_cycle_guarded(self, trigger: str):
        self._running = True
        started_at = datetime.now(timezone.utc).isoformat()
        run_id = self.bridge_state.start_pipeline_run(started_at, trigger)
        try:
            result = self.run_cycle(trigger)
            self._last_result = result
            self._last_run_at = result["finished_at"]
            self.bridge_state.finish_pipeline_run(run_id, result)
        except Exception as exc:
            log.exception("Pipeline cycle failed")
            result = {
                "trigger": trigger,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "files_scanned": 0,
                "files_new": 0,
                "files_skipped": 0,
                "files_ok": 0,
                "files_failed": 1,
                "import_new_tx": 0,
                "import_review": 0,
                "sync_performed": 0,
            }
            self._last_result = result
            self._last_run_at = result["finished_at"]
            self.bridge_state.finish_pipeline_run(run_id, result)
        finally:
            self._running = False
            _lock.release()
            if self.is_enabled():
                self._schedule(int(self.config.get("scan_interval_seconds", 14400)))

    def run_cycle(self, trigger: str) -> dict:
        started_at = datetime.now(timezone.utc).isoformat()
        inbox_dir = Path(self.settings["pdf"]["inbox_dir"])
        candidates, skipped_count = self._scan_candidates(inbox_dir)

        parse_results = []
        for candidate in candidates:
            outcome = self._process_candidate(candidate)
            parse_results.append(outcome)

        files_ok = sum(1 for r in parse_results if r["status"] == "ok")
        files_failed = sum(1 for r in parse_results if r["status"] == "error")
        import_stats = {"added": 0, "by_layer": {4: 0}}
        sync_stats = None

        if files_ok and self.config.get("auto_import_enabled", True):
            import_stats = self._run_import()
            if import_stats.get("added", 0) > 0 and self.config.get("auto_sync_enabled", True):
                sync_stats = self._run_sync()

        completed_months = self._find_completed_months()
        self._send_notifications(parse_results, import_stats, completed_months)

        return {
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "files_scanned": len(candidates) + skipped_count,
            "files_new": len(candidates),
            "files_skipped": skipped_count,
            "files_ok": files_ok,
            "files_failed": files_failed,
            "import_new_tx": import_stats.get("added", 0),
            "import_review": import_stats.get("by_layer", {}).get(4, 0),
            "sync_performed": 1 if sync_stats else 0,
            "results": parse_results,
            "completed_months": completed_months,
        }

    def _scan_candidates(self, inbox_dir: Path) -> tuple[list[CandidateFile], int]:
        candidates: list[CandidateFile] = []
        skipped = 0
        pdf_sources = sorted(
            path for path in inbox_dir.rglob("*.pdf")
            if path.is_file()
        )

        for pdf_path in pdf_sources:
            if not is_stable(pdf_path, 5):
                continue
            pdf_sha = sha256_file(pdf_path)
            if self.registry.seen(pdf_sha):
                skipped += 1
                continue
            state = "retry" if self.registry.attempted(pdf_sha) else "new"
            candidates.append(CandidateFile(pdf_path, pdf_sha, state))
        return candidates, skipped

    def _process_candidate(self, candidate: CandidateFile) -> dict:
        result = run_pdf_pipeline_file(str(candidate.path))
        status = "ok" if result["status"] == "done" else "error"
        error_category = self._categorize_error(result.get("error", ""), result.get("log", ""))
        self.registry.record(
            sha256=candidate.sha256,
            filename=candidate.path.name,
            source_path=str(candidate.path),
            status=status,
            bank=result.get("bank", ""),
            stmt_type=result.get("stmt_type", ""),
            period=result.get("period", ""),
            transactions=result.get("transactions", 0),
            output_file=result.get("output_file", ""),
            error=result.get("error", ""),
            error_category=error_category,
        )
        return {
            "filename": candidate.path.name,
            "path": str(candidate.path),
            "sha256": candidate.sha256,
            "state": candidate.state,
            "status": status,
            "bank": result.get("bank", ""),
            "stmt_type": result.get("stmt_type", ""),
            "period": result.get("period", ""),
            "transactions": result.get("transactions", 0),
            "error": result.get("error", ""),
            "error_category": error_category,
        }

    def _run_import(self) -> dict:
        cfg = load_config()
        finance_cfg = get_finance_config(cfg)
        sheets_cfg = get_sheets_config(cfg)
        ollama_cfg = get_ollama_finance_config(cfg)
        anthropic_cfg = get_anthropic_finance_config(cfg)

        sheets = SheetsClient(sheets_cfg)
        categorizer = Categorizer(
            aliases=[],
            categories=[],
            ollama_host=ollama_cfg.host,
            ollama_model=ollama_cfg.model,
            ollama_timeout=ollama_cfg.timeout_seconds,
            anthropic_api_key=anthropic_cfg.api_key,
            anthropic_model=anthropic_cfg.model,
        )
        return importer_run(
            xlsx_path=finance_cfg.xlsx_input,
            sheets_client=sheets,
            categorizer=categorizer,
            overwrite=False,
            dry_run=False,
            import_file_label=Path(finance_cfg.xlsx_input).name,
        )

    def _run_sync(self) -> dict:
        cfg = load_config()
        finance_cfg = get_finance_config(cfg)
        sheets_cfg = get_sheets_config(cfg)
        return sync_run(finance_cfg.sqlite_db, SheetsClient(sheets_cfg))

    def _find_completed_months(self) -> list[str]:
        if not self.config.get("completeness_alert", True):
            return []
        rows = build_log_rows(DEFAULT_REGISTRY_DB)
        month_counts: dict[str, int] = {}
        for month, _label, expected, actual, status, *_rest in rows:
            if status == "✓ Complete" and actual == expected:
                month_counts[month] = month_counts.get(month, 0) + actual
        completed = []
        for month, actual in month_counts.items():
            if actual >= TOTAL_EXPECTED and not self.bridge_state.has_pipeline_notification(month):
                completed.append(month)
        return sorted(completed)

    def _send_notifications(self, parse_results: list[dict], import_stats: dict, completed_months: list[str]):
        if self.messages and self.messages.can_access() and self.settings["imessage"].get("primary_recipient"):
            if parse_results and self.config.get("parse_alert", True):
                ok_results = [r for r in parse_results if r["status"] == "ok"]
                if ok_results:
                    lines = [f"Pipeline: {len(ok_results)} PDF(s) processed"]
                    lines.extend(
                        f"- {r['bank']} {r['stmt_type']} {r['period']} ({r['transactions']} txns)"
                        for r in ok_results[:5]
                    )
                    lines.append(
                        f"Imported {import_stats.get('added', 0)} new transactions. "
                        f"{import_stats.get('by_layer', {}).get(4, 0)} need review."
                    )
                    self.messages.send_alert("\n".join(lines))

            if parse_results and self.config.get("failure_alert", True):
                failures = [r for r in parse_results if r["status"] == "error"]
                if failures:
                    lines = [f"Pipeline: {len(failures)} PDF(s) failed"]
                    lines.extend(
                        f"- {r['filename']}: {r['error_category'] or 'parse_error'}"
                        for r in failures[:5]
                    )
                    self.messages.send_alert("\n".join(lines))

            for month in completed_months:
                message = f"{month} complete ({TOTAL_EXPECTED}/{TOTAL_EXPECTED} PDFs)"
                self.messages.send_alert(message)
                self.bridge_state.record_pipeline_notification(month, message)

    def _categorize_error(self, error: str, log_text: str) -> str:
        haystack = f"{error}\n{log_text}".lower()
        if "password" in haystack or "encrypted" in haystack:
            return "wrong_password"
        if "unknownstatement" in haystack or "could not detect" in haystack or "unrecognized" in haystack:
            return "unrecognized_format"
        if "verifier blocked" in haystack:
            return "verification_blocked"
        if "ollama" in haystack and ("connection" in haystack or "refused" in haystack or "unreachable" in haystack):
            return "ollama_unavailable"
        if "export" in haystack or "openpyxl" in haystack or "permission denied" in haystack:
            return "export_error"
        return "parse_error"
