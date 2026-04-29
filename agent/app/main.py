import time
import signal
import logging
import threading
from datetime import datetime, timezone
from threading import Event

from app.config import load_settings
from app.bridge_client import BridgeClient
from app.state import AgentState
from app.classifier import Classifier
from app.commands import CommandHandler
from app.orchestrator import Orchestrator
from app.health import start_health_server, StatsView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent")


def main():
    shutdown_event = Event()

    def shutdown(signum, frame):
        logger.info("Signal %s, shutting down", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Agent starting...")
    settings = load_settings()
    state = AgentState()
    classifier = Classifier(settings)
    commands = CommandHandler(state)

    stats = StatsView({
        "started_at": (
            datetime.now(timezone.utc).isoformat()),
        "emails_seen": 0,
        "emails_prefiltered": 0,
        "emails_deduped": 0,
        "alerts_sent": 0,
        "classification_failures": 0,
        "commands_processed": 0,
        "last_scan": None,
        "last_error": None,
    })

    # Shared state accessible from health handler
    _shared = {
        "last_mail": 0.0,
        "orch": None,
    }

    # Scan lock prevents /trigger and main loop from overlapping
    _scan_lock = threading.Lock()

    def trigger_callback(force: bool = False) -> dict:
        commands.scan_requested = True
        if force:
            commands.force_scan = True
        # Reload mail rules from Finance API before the next scan
        if _shared["orch"]:
            _shared["orch"].classifier.reload_rules()
        return {
            "queued": True,
            "force": force,
            "last_mail": _shared["last_mail"],
            "bridge_ok": (
                _shared["orch"].bridge_ok
                if _shared["orch"] else False),
        }

    # Bind health server to all interfaces so Docker port mapping works
    start_health_server(
        stats, host="0.0.0.0", port=8080,
        trigger_callback=trigger_callback)
    logger.info("Health server on 0.0.0.0:8080")

    # Retry bridge connection (3 minutes)
    bridge = BridgeClient()
    bridge_ready = False
    for attempt in range(18):
        try:
            health = bridge.health()
            logger.info("Bridge health: %s", health)
            bridge_ready = True
            break
        except Exception as e:
            logger.warning(
                "Bridge not ready (%d/18): %s",
                attempt + 1, e)
            time.sleep(10)

    if not bridge_ready:
        logger.error("Bridge unreachable after 3 minutes")
        return

    orch = Orchestrator(
        bridge, classifier, state, commands,
        settings, stats)
    _shared["orch"] = orch

    if settings["imessage"].get(
            "startup_notifications", True):
        try:
            bridge.send_alert(
                "\U0001f916 Mail agent started")
        except Exception:
            logger.warning("Startup notification failed")

    poll_mail = int(
        settings["agent"]["poll_interval_seconds"])
    poll_cmd = int(
        settings["agent"]["command_poll_interval_seconds"])
    _shared["last_mail"] = 0.0
    last_cmd = 0.0

    logger.info(
        "Main loop (mail %ds, commands %ds)",
        poll_mail, poll_cmd)

    while not shutdown_event.is_set():
        now = time.time()
        try:
            # --- Config reload check ---
            if state.get_bool_flag("config_reload_pending"):
                logger.info("Picking up configuration changes...")
                new_settings = load_settings()
                orch.reload_config(new_settings)
                state.set_bool_flag("config_reload_pending", False)

            # --- Bridge reconnect with backoff ---
            if not orch.bridge_ok:
                now_r = time.time()
                if now_r - orch._last_bridge_retry >= orch.BRIDGE_RETRY_INTERVAL:
                    logger.info(
                        "Bridge down, attempting reconnect...")
                    try:
                        health = bridge.health()
                        orch.bridge_ok = True
                        orch._last_bridge_retry = now_r
                        logger.info("Bridge reconnect OK: %s", health)
                    except Exception as e:
                        orch._last_bridge_retry = now_r
                        logger.warning(
                            "Bridge still unreachable: %s", e)

            # --- Mail scan ---
            should_scan = (
                (now - _shared["last_mail"] >= poll_mail)
                or commands.scan_requested
                or commands.force_scan)

            if should_scan and _scan_lock.acquire(blocking=False):
                try:
                    scan_executed = orch.scan_mail_once()
                    if scan_executed:
                        _shared["last_mail"] = now
                        commands.scan_requested = False
                        commands.force_scan = False
                    # If scan_executed is False (bridge down),
                    # flags stay set so next loop retries.
                    # last_mail is NOT advanced, so the timer
                    # remains overdue -> retry on next cycle.
                finally:
                    _scan_lock.release()

            # --- Command scan ---
            if now - last_cmd >= poll_cmd:
                orch.scan_commands_once()
                last_cmd = now

            time.sleep(2)

        except Exception as e:
            stats.update(last_error=str(e))
            logger.exception("Main loop error")
            time.sleep(10)

    logger.info("Agent stopped")
    if settings["imessage"].get(
            "shutdown_notifications", False):
        try:
            bridge.send_alert(
                "\U0001f534 Agent shutting down")
        except Exception:
            pass
    try:
        bridge.client.close()
    except Exception:
        pass
    try:
        classifier.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
