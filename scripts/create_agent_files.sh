#!/bin/bash
set -euo pipefail

BASE="$HOME/agentic-ai"
cd "$BASE"

echo "Creating agent directory structure..."
mkdir -p agent/app/providers

# ============================================================
# agent/requirements.txt
# ============================================================
cat > agent/requirements.txt << 'EOF'
httpx==0.28.1
pydantic==2.11.3
EOF

# ============================================================
# agent/Dockerfile
# ============================================================
cat > agent/Dockerfile << 'EOF'
FROM python:3.12-slim

RUN useradd -m -s /bin/bash agentuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app

RUN mkdir -p /app/data /app/config
RUN chown -R agentuser:agentuser /app

USER agentuser

CMD ["python", "-m", "app.main"]
EOF

# ============================================================
# agent/app/__init__.py
# ============================================================
cat > agent/app/__init__.py << 'EOF'
# agent app package
EOF

# ============================================================
# agent/app/config.py
# ============================================================
cat > agent/app/config.py << 'EOF'
import os
import tomllib


def load_settings() -> dict:
    settings_file = os.environ.get("SETTINGS_FILE", "/app/config/settings.toml")
    with open(settings_file, "rb") as f:
        return tomllib.load(f)
EOF

# ============================================================
# agent/app/schemas.py
# ============================================================
cat > agent/app/schemas.py << 'EOF'
from pydantic import BaseModel, Field
from typing import Literal


Category = Literal[
    "transaction_alert",
    "bill_statement",
    "bank_clarification",
    "payment_due",
    "security_alert",
    "financial_other",
    "not_financial",
]

Urgency = Literal["low", "medium", "high"]


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    summary: str = Field(max_length=200)
    requires_action: bool = False
    provider: str
EOF

# ============================================================
# agent/app/state.py
# ============================================================
cat > agent/app/state.py << 'EOF'
import sqlite3
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager


class AgentState:
    def __init__(self, db_path: str = "/app/data/agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                bridge_id TEXT PRIMARY KEY,
                message_id TEXT,
                processed_at TEXT,
                category TEXT,
                urgency TEXT,
                provider TEXT,
                alert_sent INTEGER,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS processed_commands (
                command_id TEXT PRIMARY KEY,
                processed_at TEXT,
                command_text TEXT,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bridge_id TEXT,
                sent_at TEXT,
                category TEXT,
                recipient TEXT,
                alert_text TEXT,
                success INTEGER
            );
            """)
            conn.commit()

    def message_processed(self, bridge_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_messages WHERE bridge_id = ?",
                (bridge_id,)
            ).fetchone()
            return row is not None

    def save_message_result(self, bridge_id: str, message_id: str, category: str,
                            urgency: str, provider: str, alert_sent: bool, summary: str):
        with self._connect() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO processed_messages
            (bridge_id, message_id, processed_at, category, urgency, provider, alert_sent, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bridge_id, message_id, datetime.now().isoformat(),
                category, urgency, provider, int(alert_sent), summary
            ))
            conn.commit()

    def save_alert(self, bridge_id: str, category: str, recipient: str,
                   alert_text: str, success: bool):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO alerts (bridge_id, sent_at, category, recipient, alert_text, success)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                bridge_id, datetime.now().isoformat(), category, recipient, alert_text, int(success)
            ))
            conn.commit()

    def command_processed(self, command_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_commands WHERE command_id = ?",
                (command_id,)
            ).fetchone()
            return row is not None

    def save_command_result(self, command_id: str, command_text: str, result: str):
        with self._connect() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO processed_commands
            (command_id, processed_at, command_text, result)
            VALUES (?, ?, ?, ?)
            """, (
                command_id, datetime.now().isoformat(), command_text, result
            ))
            conn.commit()

    def recent_alerts(self, limit: int = 5):
        with self._connect() as conn:
            rows = conn.execute("""
            SELECT sent_at, category, alert_text, success
            FROM alerts
            ORDER BY id DESC
            LIMIT ?
            """, (limit,)).fetchall()
            return rows
EOF

# ============================================================
# agent/app/bridge_client.py
# ============================================================
cat > agent/app/bridge_client.py << 'EOF'
import os
import time
import logging
from pathlib import Path
import httpx

logger = logging.getLogger("agent.bridge_client")


class BridgeClient:
    def __init__(self):
        token_file = Path(os.environ["BRIDGE_TOKEN_FILE"])
        token = token_file.read_text().strip()

        self.client = httpx.Client(
            base_url=os.environ["BRIDGE_URL"],
            headers={"Authorization": f"Bearer {token}"},
            timeout=90.0,
        )

    def _request(self, method: str, path: str, **kwargs):
        last_error = None
        for attempt in range(3):
            try:
                resp = self.client.request(method, path, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                last_error = e
                logger.warning("Bridge 5xx on %s (attempt %d): %s", path, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning("Bridge connection error on %s (attempt %d): %s", path, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
            except Exception as e:
                raise
        raise last_error

    def health(self):
        return self._request("GET", "/health")

    def mail_pending(self, limit: int = 25):
        return self._request("GET", "/mail/pending", params={"limit": limit})

    def mail_ack(self, ack_token: str):
        return self._request("POST", "/mail/ack", json={"ack_token": ack_token})

    def commands_pending(self, limit: int = 20):
        return self._request("GET", "/commands/pending", params={"limit": limit})

    def commands_ack(self, ack_token: str):
        return self._request("POST", "/commands/ack", json={"ack_token": ack_token})

    def send_alert(self, text: str):
        return self._request("POST", "/alerts/send", json={"text": text})
EOF

# ============================================================
# agent/app/providers/__init__.py
# ============================================================
cat > agent/app/providers/__init__.py << 'EOF'
# providers package
EOF

# ============================================================
# agent/app/providers/base.py
# ============================================================
cat > agent/app/providers/base.py << 'EOF'
from abc import ABC, abstractmethod
from app.schemas import Classification


class Provider(ABC):
    name: str

    @abstractmethod
    def classify(self, message: dict) -> Classification:
        raise NotImplementedError
EOF

# ============================================================
# agent/app/providers/ollama_provider.py
# ============================================================
cat > agent/app/providers/ollama_provider.py << 'EOF'
import json
import logging
import httpx
from app.schemas import Classification
from app.providers.base import Provider

logger = logging.getLogger("agent.ollama")

ALLOWED_CATEGORIES = {
    "transaction_alert",
    "bill_statement",
    "bank_clarification",
    "payment_due",
    "security_alert",
    "financial_other",
    "not_financial",
}

ALLOWED_URGENCY = {"low", "medium", "high"}


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, settings: dict):
        self.host = settings["ollama"]["host"]
        self.model = settings["ollama"]["model_primary"]
        self.timeout = int(settings["ollama"]["timeout_seconds"])
        self.http = httpx.Client(timeout=self.timeout)

    def classify(self, message: dict) -> Classification:
        prompt = self._prompt(message)
        logger.debug("Sending to Ollama model %s", self.model)
        r = self.http.post(
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 250
                }
            }
        )
        r.raise_for_status()
        text = r.json().get("response", "")
        return self._parse(text)

    def _prompt(self, message: dict) -> str:
        sender = message.get("sender", "")
        subject = message.get("subject", "")
        snippet = message.get("snippet", "")
        body_text = (message.get("body_text") or "")[:4000]

        content = body_text if body_text else snippet

        return f"""You are classifying an email for a personal finance alert system.

Return ONLY valid JSON with no other text:
{{"category": "...", "urgency": "...", "summary": "...", "requires_action": true}}

Allowed categories:
- transaction_alert: purchase, transfer, withdrawal, deposit notifications
- bill_statement: monthly bills, credit card statements, utility bills
- bank_clarification: bank asking for verification, document requests
- payment_due: upcoming payment deadlines, overdue notices
- security_alert: login attempts, password resets, 2FA codes, fraud alerts
- financial_other: other money-related emails that don't fit above
- not_financial: newsletters, promotions, social media, non-financial

Allowed urgency: low, medium, high

Rules:
- security_alert and fraud = high urgency
- payment_due = medium or high
- transaction_alert = medium
- bill_statement = low or medium
- summary must be 1 short sentence describing what the email is about
- If unsure between financial and not, lean toward financial_other

Email:
From: {sender}
Subject: {subject}
Body: {content}
""".strip()

    def _parse(self, text: str) -> Classification:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"No JSON found in Ollama response: {text[:200]}")

        payload = json.loads(text[start:end])

        category = payload.get("category", "financial_other")
        urgency = payload.get("urgency", "medium")
        summary = str(payload.get("summary", ""))[:200]
        requires_action = bool(payload.get("requires_action", False))

        if category not in ALLOWED_CATEGORIES:
            category = "financial_other"
        if urgency not in ALLOWED_URGENCY:
            urgency = "medium"
        if not summary:
            summary = "No summary provided"

        return Classification(
            category=category,
            urgency=urgency,
            summary=summary,
            requires_action=requires_action,
            provider=f"ollama/{self.model}",
        )
EOF

# ============================================================
# agent/app/providers/anthropic_provider.py
# ============================================================
cat > agent/app/providers/anthropic_provider.py << 'EOF'
import os
import json
import logging
import httpx
from app.schemas import Classification
from app.providers.base import Provider

logger = logging.getLogger("agent.anthropic")

ALLOWED_CATEGORIES = {
    "transaction_alert",
    "bill_statement",
    "bank_clarification",
    "payment_due",
    "security_alert",
    "financial_other",
    "not_financial",
}

ALLOWED_URGENCY = {"low", "medium", "high"}


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, settings: dict):
        self.enabled = bool(settings["anthropic"]["enabled"])
        self.model = settings["anthropic"]["model"]
        env_name = settings["anthropic"]["api_key_env"]
        self.api_key = os.environ.get(env_name, "")
        self.http = httpx.Client(timeout=90.0)

    def classify(self, message: dict) -> Classification:
        if not self.enabled or not self.api_key:
            raise RuntimeError("Anthropic not enabled or API key missing")

        sender = message.get("sender", "")
        subject = message.get("subject", "")
        snippet = message.get("snippet", "")
        body_text = (message.get("body_text") or "")[:4000]

        content = body_text if body_text else snippet

        prompt = f"""Classify this email for a personal finance alert system.

Return ONLY valid JSON:
{{"category": "...", "urgency": "...", "summary": "...", "requires_action": true}}

Categories: transaction_alert, bill_statement, bank_clarification, payment_due, security_alert, financial_other, not_financial
Urgency: low, medium, high

Email:
From: {sender}
Subject: {subject}
Body: {content}""".strip()

        logger.debug("Sending to Anthropic model %s", self.model)
        r = self.http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 250,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        r.raise_for_status()
        payload = r.json()
        text = payload["content"][0]["text"]
        return self._parse(text)

    def _parse(self, text: str) -> Classification:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"No JSON found in Anthropic response: {text[:200]}")

        payload = json.loads(text[start:end])

        category = payload.get("category", "financial_other")
        urgency = payload.get("urgency", "medium")
        summary = str(payload.get("summary", ""))[:200]
        requires_action = bool(payload.get("requires_action", False))

        if category not in ALLOWED_CATEGORIES:
            category = "financial_other"
        if urgency not in ALLOWED_URGENCY:
            urgency = "medium"
        if not summary:
            summary = "No summary provided"

        return Classification(
            category=category,
            urgency=urgency,
            summary=summary,
            requires_action=requires_action,
            provider=f"anthropic/{self.model}",
        )
EOF

# ============================================================
# agent/app/providers/openai_provider.py
# ============================================================
cat > agent/app/providers/openai_provider.py << 'EOF'
class OpenAIProvider:
    name = "openai"
    enabled = False
EOF

# ============================================================
# agent/app/providers/gemini_provider.py
# ============================================================
cat > agent/app/providers/gemini_provider.py << 'EOF'
class GeminiProvider:
    name = "gemini"
    enabled = False
EOF

# ============================================================
# agent/app/classifier.py
# ============================================================
cat > agent/app/classifier.py << 'EOF'
import logging
from app.providers.ollama_provider import OllamaProvider
from app.providers.anthropic_provider import AnthropicProvider
from app.schemas import Classification

logger = logging.getLogger("agent.classifier")


class Classifier:
    def __init__(self, settings: dict):
        self.settings = settings
        self.providers = []
        for name in settings["classifier"]["provider_order"]:
            if name == "ollama":
                self.providers.append(OllamaProvider(settings))
            elif name == "anthropic":
                self.providers.append(AnthropicProvider(settings))

    def classify(self, message: dict) -> Classification:
        # Use Apple ML category as pre-filter: skip promotions (category 3)
        if self._apple_says_skip(message):
            return Classification(
                category="not_financial",
                urgency="low",
                summary="Skipped: Apple classified as promotion/marketing",
                requires_action=False,
                provider="apple_ml_prefilter",
            )

        last_error = None
        for provider in self.providers:
            try:
                result = provider.classify(message)
                logger.info(
                    "Classified %s as %s/%s via %s",
                    message.get("bridge_id"),
                    result.category,
                    result.urgency,
                    result.provider,
                )
                return result
            except Exception as e:
                logger.warning(
                    "Provider %s failed for %s: %s",
                    provider.name,
                    message.get("bridge_id"),
                    e,
                )
                last_error = e

        if self.settings["classifier"]["generic_alert_on_total_failure"]:
            return Classification(
                category="financial_other",
                urgency="medium",
                summary="Classification failed - may be important",
                requires_action=True,
                provider=f"fallback_error:{last_error}",
            )

        return Classification(
            category="not_financial",
            urgency="low",
            summary="Classification failed",
            requires_action=False,
            provider=f"fallback_error:{last_error}",
        )

    def _apple_says_skip(self, message: dict) -> bool:
        apple_cat = message.get("apple_category")
        if message.get("apple_urgent"):
            return False
        if message.get("apple_high_impact"):
            return False
        if apple_cat == 3:
            return True
        return False
EOF

# ============================================================
# agent/app/commands.py
# ============================================================
cat > agent/app/commands.py << 'EOF'
class CommandHandler:
    def __init__(self, state):
        self.state = state
        self.paused = False
        self.quiet = False
        self.scan_requested = False

    def handle(self, text: str) -> str:
        cmd = text.strip().lower()

        if cmd.startswith("agent:"):
            cmd = cmd[len("agent:"):].strip()

        if cmd == "help":
            return (
                "Commands: status, summary, test, scan, pause, resume, "
                "quiet on, quiet off, health, last 5"
            )
        if cmd == "status":
            return f"Agent running. paused={self.paused}, quiet={self.quiet}"
        if cmd == "summary":
            rows = self.state.recent_alerts(5)
            if not rows:
                return "No recent alerts"
            lines = []
            for r in rows:
                status = "ok" if r[3] else "fail"
                lines.append(f"{r[0]} | {r[1]} | {status}")
            return "\n".join(lines)
        if cmd == "test":
            return "Test OK - agent is responding"
        if cmd == "scan":
            self.scan_requested = True
            return "Manual scan scheduled for next cycle"
        if cmd == "pause":
            self.paused = True
            return "Agent paused - no more scans until resumed"
        if cmd == "resume":
            self.paused = False
            return "Agent resumed"
        if cmd == "quiet on":
            self.quiet = True
            return "Quiet mode: alerts suppressed"
        if cmd == "quiet off":
            self.quiet = False
            return "Quiet mode off: alerts active"
        if cmd == "health":
            return "Agent healthy and running"
        if cmd == "last 5":
            rows = self.state.recent_alerts(5)
            if not rows:
                return "No recent alerts"
            lines = []
            for r in rows:
                status = "ok" if r[3] else "fail"
                lines.append(f"{r[0]} | {r[1]} | {status}")
            return "\n".join(lines)

        return "Unknown command. Send 'agent: help' for options"
EOF

# ============================================================
# agent/app/health.py
# ============================================================
cat > agent/app/health.py << 'EOF'
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock


class StatsView:
    def __init__(self, initial: dict):
        self._data = dict(initial)
        self._lock = Lock()

    def update(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)

    def incr(self, key: str, value: int = 1):
        with self._lock:
            self._data[key] = self._data.get(key, 0) + value

    def snapshot(self):
        with self._lock:
            return dict(self._data)


def start_health_server(stats: StatsView, host="0.0.0.0", port=8080):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(stats.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
EOF

# ============================================================
# agent/app/orchestrator.py
# ============================================================
cat > agent/app/orchestrator.py << 'EOF'
import logging
from datetime import datetime

logger = logging.getLogger("agent.orchestrator")


class Orchestrator:
    def __init__(self, bridge, classifier, state, commands, settings, stats):
        self.bridge = bridge
        self.classifier = classifier
        self.state = state
        self.commands = commands
        self.settings = settings
        self.stats = stats

    def scan_mail_once(self):
        if self.commands.paused:
            logger.info("Scan skipped: agent is paused")
            return

        payload = self.bridge.mail_pending(limit=self.settings["mail"]["max_batch"])
        items = payload.get("items", [])
        ack_token = payload.get("next_ack_token")

        if not items:
            logger.debug("No pending mail")
            return

        logger.info("Processing %d pending emails", len(items))

        for item in items:
            bridge_id = item["bridge_id"]
            if self.state.message_processed(bridge_id):
                continue

            result = self.classifier.classify(item)
            alert_categories = set(self.settings["agent"]["alert_on_categories"])
            should_alert = result.category in alert_categories

            alert_sent = False
            if should_alert and not self.commands.quiet:
                alert_text = self._format_alert(item, result)
                try:
                    resp = self.bridge.send_alert(alert_text)
                    alert_sent = bool(resp.get("success", False))
                    self.state.save_alert(
                        bridge_id=bridge_id,
                        category=result.category,
                        recipient=resp.get("recipient", ""),
                        alert_text=alert_text,
                        success=alert_sent,
                    )
                    if alert_sent:
                        logger.info("Alert sent for %s: %s", bridge_id, result.category)
                    else:
                        logger.warning("Alert send failed for %s", bridge_id)
                except Exception as e:
                    logger.error("Alert send error for %s: %s", bridge_id, e)
                    alert_sent = False

            self.state.save_message_result(
                bridge_id=bridge_id,
                message_id=item.get("message_id", ""),
                category=result.category,
                urgency=result.urgency,
                provider=result.provider,
                alert_sent=alert_sent,
                summary=result.summary,
            )

            self.stats.incr("emails_scanned")
            if alert_sent:
                self.stats.incr("alerts_sent")

        if items and ack_token:
            self.bridge.mail_ack(ack_token)
            logger.info("Acked through %s", ack_token)

        self.stats.update(last_scan=datetime.now().isoformat())

    def scan_commands_once(self):
        payload = self.bridge.commands_pending(limit=20)
        items = payload.get("items", [])
        ack_token = payload.get("next_ack_token")

        for item in items:
            if self.state.command_processed(item["command_id"]):
                continue

            logger.info("Processing command: %s", item["text"])
            reply = self.commands.handle(item["text"])

            try:
                self.bridge.send_alert(f"\U0001f916 {reply}")
            except Exception as e:
                logger.error("Command response send failed: %s", e)

            self.state.save_command_result(
                command_id=item["command_id"],
                command_text=item["text"],
                result=reply,
            )

        if items and ack_token:
            self.bridge.commands_ack(ack_token)

    def _format_alert(self, item: dict, result) -> str:
        cat = result.category.replace("_", " ").title()
        urgency = result.urgency.upper()
        sender = item.get("sender_email") or item.get("sender", "Unknown")
        subject = item.get("subject", "(No Subject)")
        date = item.get("date_received", "")
        if date:
            date = date[:16].replace("T", " ")

        return (
            f"\U0001f514 {cat} [{urgency}]\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n"
            f"Summary: {result.summary}"
        )
EOF

# ============================================================
# agent/app/main.py
# ============================================================
cat > agent/app/main.py << 'EOF'
import time
import logging
from datetime import datetime

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
    logger.info("Agent starting...")
    settings = load_settings()
    bridge = BridgeClient()
    state = AgentState()
    classifier = Classifier(settings)
    commands = CommandHandler(state)

    stats = StatsView({
        "started_at": datetime.now().isoformat(),
        "emails_scanned": 0,
        "alerts_sent": 0,
        "last_scan": None,
        "last_error": None,
    })

    start_health_server(stats)
    logger.info("Health server started on :8080")

    orch = Orchestrator(
        bridge=bridge,
        classifier=classifier,
        state=state,
        commands=commands,
        settings=settings,
        stats=stats,
    )

    # Check bridge connectivity
    try:
        health = bridge.health()
        logger.info("Bridge health: %s", health)
    except Exception:
        logger.exception("Bridge unreachable at startup")
        raise

    # Send startup notification
    if settings["imessage"]["startup_notifications"]:
        try:
            bridge.send_alert("\U0001f916 Mail agent started and monitoring")
        except Exception:
            logger.warning("Could not send startup notification")

    poll_mail = int(settings["agent"]["poll_interval_seconds"])
    poll_cmd = int(settings["agent"]["command_poll_interval_seconds"])
    last_mail = 0.0
    last_cmd = 0.0

    logger.info("Entering main loop (mail every %ds, commands every %ds)", poll_mail, poll_cmd)

    while True:
        now = time.time()

        try:
            if now - last_mail >= poll_mail or commands.scan_requested:
                orch.scan_mail_once()
                last_mail = now
                commands.scan_requested = False

            if now - last_cmd >= poll_cmd:
                orch.scan_commands_once()
                last_cmd = now

            time.sleep(2)

        except KeyboardInterrupt:
            logger.info("Agent shutting down")
            break
        except Exception as e:
            stats.update(last_error=str(e))
            logger.exception("Main loop error")
            time.sleep(10)


if __name__ == "__main__":
    main()
EOF

# ============================================================
# docker-compose.yml
# ============================================================
cat > "$BASE/docker-compose.yml" << 'EOF'
services:
  mail-agent:
    build:
      context: ./agent
    container_name: mail-agent
    restart: unless-stopped
    environment:
      SETTINGS_FILE: /app/config/settings.toml
      BRIDGE_URL: http://host.docker.internal:9100
      BRIDGE_TOKEN_FILE: /run/secrets/bridge.token
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
    volumes:
      - ./config/settings.toml:/app/config/settings.toml:ro
      - ./data:/app/data
      - ./secrets/bridge.token:/run/secrets/bridge.token:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    mem_limit: 2g
    security_opt:
      - no-new-privileges:true
EOF

# ============================================================
# .env
# ============================================================
if [ ! -f "$BASE/.env" ]; then
cat > "$BASE/.env" << 'EOF'
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
EOF
echo "Created .env - add your ANTHROPIC_API_KEY if you want cloud fallback"
fi

echo ""
echo "=== All agent files created ==="
echo ""
echo "Verifying..."
for f in agent/Dockerfile agent/requirements.txt \
         agent/app/__init__.py agent/app/main.py agent/app/config.py \
         agent/app/schemas.py agent/app/state.py agent/app/bridge_client.py \
         agent/app/classifier.py agent/app/commands.py agent/app/health.py \
         agent/app/orchestrator.py \
         agent/app/providers/__init__.py agent/app/providers/base.py \
         agent/app/providers/ollama_provider.py \
         agent/app/providers/anthropic_provider.py \
         docker-compose.yml; do
    if [ -f "$f" ]; then
        echo "  ✅ $f"
    else
        echo "  ❌ $f MISSING"
    fi
done
