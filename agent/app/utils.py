"""Shared utilities for the agent app."""
from __future__ import annotations
import json
import re


def extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a text response.

    Tries three strategies in order:
    1. Direct parse (response is already pure JSON)
    2. Fenced code block (```json ... ```)
    3. Outermost brace extraction (JSON embedded in prose)
    """
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None
