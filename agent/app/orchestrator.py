import time
import logging
from datetime import datetime, timezone

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

    def scan_mail_once(self):
        if self.commands.paused:
            logger.info("Scan skipped: paused")
            return

        cycle_start = time.time()
        total = 0

        while total < MAX_PER_CYCLE:
            if time.time() - cycle_start > MAX_CYCLE_SECONDS:
                logger.info("Cycle budget exceeded")
                break

            payload = self.bridge.mail_pending(
                limit=self.settings["mail"]["max_batch"])
            items = payload.get("items", [])
            if not items:
                break

            logger.info(
                "Processing %d emails (cycle total: %d)",
                len(items), total)
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
                    result.category in alert_cats)
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

        self.stats.update(
            last_scan=(
                datetime.now(timezone.utc).isoformat()))

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
        return (
            f"\U0001f514 {cat} "
            f"[{result.urgency.upper()}]\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n"
            f"Summary: {result.summary}"
        )
