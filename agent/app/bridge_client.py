import os
import time
import logging
from pathlib import Path
import httpx

logger = logging.getLogger("agent.bridge_client")


class BridgeClient:
    def __init__(self):
        token_path = os.environ.get("BRIDGE_TOKEN_FILE")
        if not token_path:
            raise RuntimeError("BRIDGE_TOKEN_FILE environment variable is not set")
        token_file = Path(token_path)
        if not token_file.exists():
            raise FileNotFoundError(f"Bridge token file not found: {token_file}")
        token = token_file.read_text().strip()
        if not token:
            raise ValueError(f"Bridge token file is empty: {token_file}")

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
