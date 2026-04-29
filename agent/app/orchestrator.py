import time
import logging
import copy
from datetime import datetime, timezone

import httpx

from app.net_guard import network_ok
from app.rules import evaluate_message

logger = logging.getLogger("agent.orchestrator")

MAX_PER_CYCLE = 50
MAX_CYCLE_SECONDS = 300


class Orchestrator:
    def __init__(self, bridge, classifier, state,
                 commands, settings, stats):
        self.bridge = bridge
        self.classifier = classifier
        self.state = state
        self.commands = commands
        self.settings = settings
        self.stats = stats

        # Bridge health tracking for reconnect with backoff
        self.bridge_ok = True
        self._last_bridge_retry = 0.0
        self.BRIDGE_RETRY_INTERVAL = 45  # seconds
        self.mode = self._resolve_mode()

        imap_cfg = self.settings.get("mail", {}).get("imap", {})
        accounts = [
            acct for acct in imap_cfg.get("accounts", [])
            if not str(acct.get("email", "")).startswith("YOUR_EMAIL")
        ]
        self.use_imap = bool(accounts)
        self.imap_intake = None
        self.pdf_router = None
        if self.use_imap:
            from app.imap_source import IMAPIntake
            from app.pdf_router import PdfRouter
            imap_settings = copy.deepcopy(self.settings)
            imap_settings.setdefault("mail", {}).setdefault("imap", {})[
                "accounts"] = accounts
            self.imap_intake = IMAPIntake(imap_settings, self.state)
            self.pdf_router = PdfRouter(
                self.state,
                self.settings.get("mail_agent", {}).get("pdf", {}),
            )

    def reload_config(self, new_settings: dict):
        """Update instance settings and re-initialise intake if needed."""
        self.settings = new_settings
        self.mode = self._resolve_mode()
        
        imap_cfg = self.settings.get("mail", {}).get("imap", {})
        accounts = [
            acct for acct in imap_cfg.get("accounts", [])
            if not str(acct.get("email", "")).startswith("YOUR_EMAIL")
            and not acct.get("deleted_at")
            and acct.get("enabled", True)
        ]
        
        was_using_imap = self.use_imap
        self.use_imap = bool(accounts)
        
        if self.use_imap:
            from app.imap_source import IMAPIntake
            from app.pdf_router import PdfRouter
            imap_settings = copy.deepcopy(self.settings)
            imap_settings.setdefault("mail", {}).setdefault("imap", {})[
                "accounts"] = accounts
            self.imap_intake = IMAPIntake(imap_settings, self.state)
            self.pdf_router = PdfRouter(
                self.state,
                self.settings.get("mail_agent", {}).get("pdf", {}),
            )
        else:
            self.imap_intake = None
            self.pdf_router = None
            
        logger.info("Configuration reloaded (IMAP %s)", "enabled" if self.use_imap else "disabled")

    def scan_mail_once(self) -> bool:
        """Scan pending mail and process.

        Returns True if the bridge was reachable (regardless of
        whether any emails were found). Returns False if the bridge
        was unreachable — the caller should NOT advance last_mail.
        """
        self.mode = self._resolve_mode()

        if self.commands.paused:
            logger.info("Scan skipped: paused")
            return True  # bridge is fine, just paused

        # Pre-flight: verify network before any I/O
        ok, reasons = network_ok()
        self.bridge_ok = any(r.startswith("bridge:ok") for r in reasons)
        if not ok:
            logger.warning(
                "scan_mail_once aborted — network probe failed: %s",
                "; ".join(reasons))
            return False

        if self.use_imap:
            return self._scan_imap_once()

        cycle_start = time.time()
        total = 0

        while total < MAX_PER_CYCLE:
            if time.time() - cycle_start > MAX_CYCLE_SECONDS:
                logger.info("Cycle budget exceeded")
                break

            try:
                payload = self.bridge.mail_pending(
                    limit=self.settings["mail"]["max_batch"])
            except (httpx.ConnectError, httpx.TimeoutException,
                    ConnectionRefusedError, OSError) as e:
                logger.error(
                    "Bridge unreachable during scan: %s", e)
                self.bridge_ok = False
                return False
            except Exception as e:
                logger.error(
                    "Bridge error during scan: %s", e)
                self.bridge_ok = False
                return False

            items = payload.get("items", [])
            if not items:
                break

            logger.info(
                "Processing %d emails (cycle total: %d)",
                len(items), total)
            batch_start_total = total
            last_ack = None

            for item in items:
                bid = item["bridge_id"]
                mid = item.get("message_id", "")

                # Dedup by bridge_id (ROWID-based)
                if self.state.message_processed(bid):
                    last_ack = str(item["source_rowid"])
                    continue

                # Dedup by Message-ID header
                if self.state.message_id_processed(mid):
                    logger.info("Dedup: %s (message_id)", bid)
                    self.stats.incr("emails_deduped")
                    # Record so bridge_id is also marked
                    self.state.save_message_result(
                        bid, mid, "dedup_skipped", "low",
                        "message_id_dedup", False,
                        "Duplicate Message-ID")
                    last_ack = str(item["source_rowid"])
                    continue

                # Classify
                try:
                    result = self.classifier.classify(item)
                except Exception:
                    logger.exception(
                        "Classification failed: %s", bid)
                    self.stats.incr("classification_failures")
                    break  # Stop batch, retry next cycle

                self.stats.incr("emails_seen")
                if result.provider in ("apple_ml_prefilter", "domain_prefilter"):
                    self.stats.incr("emails_prefiltered")

                # Alert if needed
                alert_cats = set(
                    self.settings["agent"][
                        "alert_on_categories"])
                should_alert = (
                    result.category in alert_cats
                    and self._action_allowed("imessage"))
                alert_sent = False

                if should_alert and not self.commands.quiet:
                    alert_text = self._format_alert(
                        item, result)
                    try:
                        resp = self.bridge.send_alert(
                            alert_text)
                        alert_sent = bool(
                            resp.get("success", False))
                        self.state.save_alert(
                            bid, result.category,
                            resp.get("recipient", ""),
                            alert_text, alert_sent)
                        if alert_sent:
                            self.stats.incr("alerts_sent")
                    except Exception as e:
                        logger.error(
                            "Alert error %s: %s", bid, e)

                # Save result
                self.state.save_message_result(
                    bid, mid, result.category,
                    result.urgency, result.provider,
                    alert_sent, result.summary)

                last_ack = str(item["source_rowid"])
                total += 1

            # Ack through last successfully processed
            if last_ack:
                self.bridge.mail_ack(last_ack)
                logger.info("Acked through %s", last_ack)

            # If the entire batch was already processed (all deduped),
            # break out to avoid re-fetching the same items forever.
            if total == batch_start_total:
                logger.info("All items deduped, ending scan cycle")
                break

        self.stats.update(
            last_scan=(
                datetime.now(timezone.utc).isoformat()))

        return True

    def _scan_imap_once(self) -> bool:
        if self.imap_intake is None:
            logger.warning("IMAP scan requested but intake is not configured")
            return True

        try:
            items = self.imap_intake.poll_all()
        except Exception:
            logger.exception("IMAP intake failed")
            return False

        if not items:
            logger.info(
                "phase4a_scan_summary messages_seen=0 "
                "messages_dedup_skipped=0 messages_evaluator_ran=0 "
                "rules_matched=0 events_written=0 needs_reply_written=0")
            self.stats.update(
                last_scan=datetime.now(timezone.utc).isoformat())
            return True

        logger.info("Processing %d IMAP email(s)", len(items))
        phase4a_counts = {
            "messages_seen": 0,
            "messages_dedup_skipped": 0,
            "messages_evaluator_ran": 0,
            "rules_matched": 0,
            "events_written": 0,
            "needs_reply_written": 0,
        }

        for item in items[:MAX_PER_CYCLE]:
            phase4a_counts["messages_seen"] += 1
            mkey = item.get("message_key")
            fkey = item.get("fallback_message_key")
            if ((mkey and self.state.message_key_processed(mkey))
                    or (fkey and self.state.fallback_message_key_processed(fkey))):
                phase4a_counts["messages_dedup_skipped"] += 1
                self._log_phase4a_evaluation(
                    item, skipped_by_dedup=True)
                self._checkpoint_imap_message(item)
                self.stats.incr("emails_deduped")
                continue

            status = item.get("status", "pending")
            if status.startswith("skipped_with_reason"):
                self.state.save_message_result_imap(
                    item, "not_financial", "low",
                    "imap_size_guard", False,
                    item.get("skipped_reason", "skipped"))
                self._checkpoint_imap_message(item)
                continue

            try:
                rule_eval = evaluate_message(self.state, item)
            except Exception:
                logger.exception(
                    "Mail rule evaluation failed: %s",
                    item.get("bridge_id"))
                self.stats.incr("classification_failures")
                return False
            matched_rule_count = self._matched_rule_count(rule_eval)
            phase4a_counts["messages_evaluator_ran"] += 1
            phase4a_counts["rules_matched"] += matched_rule_count
            phase4a_counts["events_written"] += rule_eval.events_written
            phase4a_counts["needs_reply_written"] += (
                rule_eval.needs_reply_written)
            self._log_phase4a_evaluation(
                item, rule_eval, skipped_by_dedup=False,
                matched_rule_count=matched_rule_count)

            try:
                result = self.classifier.classify(item)
            except Exception:
                logger.exception("Classification failed: %s",
                                 item.get("bridge_id"))
                self.stats.incr("classification_failures")
                return False

            self.stats.incr("emails_seen")
            if result.provider in ("apple_ml_prefilter", "domain_prefilter"):
                self.stats.incr("emails_prefiltered")

            if self._action_allowed("pdf_route"):
                self._process_imap_attachments(item)
            elif item.get("attachments"):
                self.state.write_event(
                    "mode_blocked",
                    {"account": item.get("imap_account"),
                     "action": "pdf_route",
                     "mode": self.mode,
                     "bridge_id": item.get("bridge_id")})

            alert_cats = set(
                self.settings["agent"]["alert_on_categories"])
            should_alert = (
                result.category in alert_cats
                and self._action_allowed("imessage"))
            alert_sent = False

            if should_alert and not self.commands.quiet:
                alert_text = self._format_alert(item, result)
                try:
                    resp = self.bridge.send_alert(alert_text)
                    alert_sent = bool(resp.get("success", False))
                    self.state.save_alert(
                        item["bridge_id"], result.category,
                        resp.get("recipient", ""),
                        alert_text, alert_sent)
                    if alert_sent:
                        self.stats.incr("alerts_sent")
                except Exception as e:
                    logger.error("Alert error %s: %s",
                                 item.get("bridge_id"), e)
            elif result.category in alert_cats and not should_alert:
                self.state.write_event(
                    "mode_blocked",
                    {"account": item.get("imap_account"),
                     "action": "imessage",
                     "mode": self.mode,
                     "bridge_id": item.get("bridge_id")})

            self.state.save_message_result_imap(
                item, result.category, result.urgency,
                result.provider, alert_sent, result.summary)
            self._checkpoint_imap_message(item)

        logger.info(
            "phase4a_scan_summary messages_seen=%d "
            "messages_dedup_skipped=%d messages_evaluator_ran=%d "
            "rules_matched=%d events_written=%d needs_reply_written=%d",
            phase4a_counts["messages_seen"],
            phase4a_counts["messages_dedup_skipped"],
            phase4a_counts["messages_evaluator_ran"],
            phase4a_counts["rules_matched"],
            phase4a_counts["events_written"],
            phase4a_counts["needs_reply_written"],
        )

        self.stats.update(
            last_scan=datetime.now(timezone.utc).isoformat())
        return True

    def _log_phase4a_evaluation(
            self, item: dict, rule_eval=None,
            skipped_by_dedup: bool | None = None,
            matched_rule_count: int | None = None) -> None:
        if rule_eval is None:
            matched_rule_count = 0 if matched_rule_count is None else matched_rule_count
            planned_action_count = 0
            continue_to_classifier = False
            route_to_pdf_pipeline = False
        else:
            if matched_rule_count is None:
                matched_rule_count = self._matched_rule_count(rule_eval)
            planned_action_count = len(rule_eval.planned_actions)
            continue_to_classifier = rule_eval.continue_to_classifier
            route_to_pdf_pipeline = rule_eval.route_to_pdf_pipeline

        logger.info(
            "phase4a_evaluator message_key=%s message_id=%s "
            "account_id=%s account_name=%s subject=%s sender_email=%s "
            "dedup_status=%s matched_rule_count=%d "
            "planned_action_count=%d continue_to_classifier=%s "
            "route_to_pdf_pipeline=%s skipped_by_dedup=%s",
            item.get("message_key") or item.get("fallback_message_key"),
            item.get("message_id"),
            item.get("imap_account"),
            item.get("imap_account") or item.get("account_name"),
            self._truncate_log_value(item.get("subject"), 120),
            item.get("sender_email"),
            "skipped" if skipped_by_dedup else "not_skipped",
            matched_rule_count,
            planned_action_count,
            continue_to_classifier,
            route_to_pdf_pipeline,
            skipped_by_dedup,
        )

    def _matched_rule_count(self, rule_eval) -> int:
        return sum(
            1 for r in rule_eval.matched_conditions
            if r.get("matched"))

    def _truncate_log_value(self, value, limit: int) -> str:
        text = str(value or "").replace("\n", " ").replace("\r", " ")
        return text[:limit]

    def _process_imap_attachments(self, item: dict) -> None:
        if self.pdf_router is None:
            return
        message_key = (
            item.get("message_key")
            or item.get("fallback_message_key")
            or item.get("bridge_id"))
        for att in item.get("attachments", []):
            original = att.get("filename") or "attachment.pdf"
            if att.get("status") == "skipped_oversized":
                self.state.upsert_pdf_attachment(
                    attachment_key=f"{message_key}:oversize:{original}",
                    message_key=message_key,
                    fallback_message_key=item.get("fallback_message_key"),
                    account=item.get("imap_account", ""),
                    folder=item.get("imap_folder", ""),
                    uid=int(item.get("imap_uid", 0)),
                    original_filename=original,
                    status="pending_review",
                    error_reason="attachment_size_limit",
                )
                continue
            content = att.get("content")
            if not content:
                continue
            self.pdf_router.process_attachment(
                message_key=message_key,
                fallback_message_key=item.get("fallback_message_key"),
                account=item.get("imap_account", ""),
                folder=item.get("imap_folder", ""),
                uid=int(item.get("imap_uid", 0)),
                original_filename=original,
                pdf_bytes=content,
                sender=item.get("sender_email") or item.get("sender", ""),
                subject=item.get("subject", ""),
            )

    def _checkpoint_imap_message(self, item: dict) -> None:
        account = item.get("imap_account")
        folder = item.get("imap_folder")
        uid = item.get("imap_uid")
        uidvalidity = item.get("imap_uidvalidity")
        if account and folder and uid is not None and uidvalidity is not None:
            self.state.set_imap_folder_state(
                account, folder, int(uid), int(uidvalidity))

    def _resolve_mode(self) -> str:
        agent_cfg = self.settings.get("agent", {})
        mode = str(agent_cfg.get("mode", "")).strip()
        if mode in ("observe", "draft_only", "live"):
            return mode
        safe_default = str(
            agent_cfg.get("safe_default", "draft_only")).strip()
        if safe_default in ("observe", "draft_only"):
            return safe_default
        return "draft_only"

    def _action_allowed(self, action: str) -> bool:
        if action == "imessage" and not self.bridge_ok:
            return False
        required = {
            "imessage": "draft_only",
            "pdf_route": "draft_only",
            "email_mutation": "live",
        }.get(action, "live")
        rank = {"observe": 0, "draft_only": 1, "live": 2}
        return rank[self.mode] >= rank[required]

    def scan_commands_once(self):
        payload = self.bridge.commands_pending(limit=20)
        items = payload.get("items", [])
        last_ack = None

        for item in items:
            if self.state.command_processed(
                    item["command_id"]):
                last_ack = str(item["rowid"])
                continue

            logger.info("Command: %s", item["text"])
            try:
                reply = self.commands.handle(item["text"])
                self.bridge.send_alert(
                    f"\U0001f916 {reply}")
            except Exception as e:
                logger.error("Command error: %s", e)
                reply = f"Error: {e}"

            self.state.save_command_result(
                item["command_id"], item["text"], reply)
            last_ack = str(item["rowid"])
            self.stats.incr("commands_processed")

        if last_ack:
            self.bridge.commands_ack(last_ack)

    def _format_alert(self, item, result):
        cat = result.category.replace("_", " ").title()
        sender = (item.get("sender_email")
                  or item.get("sender", "Unknown"))
        subject = item.get("subject", "(No Subject)")
        date = (item.get("date_received") or ""
                )[:16].replace("T", " ")

        # For rule_based and fallback_error providers, show raw body
        if result.provider in ("rule_based",) or result.provider.startswith("fallback_error:"):
            body = (item.get("body_text")
                    or item.get("snippet") or "").strip()
            content = body[:1500] if body else "(no body)"
        else:
            content = result.summary

        return (
            f"\U0001f514 {cat}\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n\n"
            f"{content}"
        )
