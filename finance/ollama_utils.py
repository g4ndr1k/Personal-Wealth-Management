"""Shared Ollama API helpers with retry logic."""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error

log = logging.getLogger(__name__)


def ollama_generate(
    host: str,
    model: str,
    prompt: str,
    timeout: float = 60,
    *,
    temperature: float = 0.2,
    num_predict: int = 300,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    format_json: bool = False,
) -> dict:
    """POST to Ollama ``/api/generate`` with exponential-backoff retry.

    Retries only on transient network errors (``URLError``,
    ``TimeoutError``, ``ConnectionError``).  Non-transient failures
    (bad JSON, unexpected status) are raised immediately.

    Set ``format_json=True`` to pass ``"format": "json"`` in the payload,
    which forces Ollama to return valid JSON (supported by gemma3 and most
    modern models).

    Returns the parsed JSON response dict.
    """
    url = f"{host.rstrip('/')}/api/generate"
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if format_json:
        body["format"] = "json"
    payload = json.dumps(body).encode()

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                full_response = ""
                for line in resp:
                    chunk = json.loads(line)
                    full_response += chunk.get("response", "")
                    if chunk.get("done"):
                        return {
                            "model": chunk.get("model", model),
                            "response": full_response,
                            "done": True,
                            "done_reason": chunk.get("done_reason", ""),
                            "context": chunk.get("context", []),
                            "total_duration": chunk.get("total_duration", 0),
                            "eval_count": chunk.get("eval_count", 0),
                        }
                return {"response": full_response, "done": True}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if attempt < max_retries:
                delay = backoff_base ** (attempt - 1)
                log.debug(
                    "Ollama call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt, max_retries, delay, exc,
                )
                time.sleep(delay)
            else:
                log.warning(
                    "Ollama call failed after %d attempts: %s",
                    max_retries, exc,
                )

    raise last_err  # type: ignore[misc]
