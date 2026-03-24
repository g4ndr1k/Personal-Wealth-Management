import time
import signal
import logging
from datetime import datetime, timezone

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

running = True


def main():
    global running

    def shutdown(signum, frame):
        global running
        logger.info("Signal %s, shutting down", signum)
        running = False

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

    # Bind health server to localhost inside container
    start_health_server(stats, host="127.0.0.1", port=8080)
    logger.info("Health server on 127.0.0.1:8080")

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
    last_mail = last_cmd = 0.0

    logger.info(
        "Main loop (mail %ds, commands %ds)",
        poll_mail, poll_cmd)

    while running:
        now = time.time()
        try:
            if (now - last_mail >= poll_mail
                    or commands.scan_requested):
                orch.scan_mail_once()
                last_mail = now
                commands.scan_requested = False

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


if __name__ == "__main__":
    main()
