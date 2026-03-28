"""
Stage 2 — 4-layer expense categorization engine.

Layer 1  Merchant alias exact match       → auto-assigns, no user input
Layer 2  Regex pattern match              → auto-assigns, no user input
Layer 3  Ollama AI suggestion             → pre-fills review queue, user confirms
Layer 4  Review queue fallback            → blank entry, user types manually

Confirmed Layer 3/4 entries are written back to the Merchant Aliases tab by
the caller (PWA → FastAPI), not by this module.  This module is read-only
with respect to Google Sheets.
"""
from __future__ import annotations
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Fallback category list used when the Sheets Categories tab is empty
DEFAULT_CATEGORIES = [
    "Housing", "Utilities", "Groceries", "Dining Out", "Transport",
    "Shopping", "Healthcare", "Entertainment", "Subscriptions", "Travel",
    "Education", "Personal Care", "Gifts & Donations", "Fees & Interest",
    "Income", "Other",
]


@dataclass
class CategorizationResult:
    merchant: Optional[str]   # None if not determined
    category: Optional[str]   # None if not determined (= review queue)
    layer: int                # 1=exact  2=regex  3=ollama  4=review
    confidence: str           # "auto" | "suggested" | "none"


class Categorizer:
    """
    4-layer categorization engine.

    Usage:
        cat = Categorizer(aliases, categories, ollama_host=..., ...)
        result = cat.categorize("STARBUCKS SENAYAN CITY")
        # CategorizationResult(merchant='Starbucks', category='Dining Out',
        #                      layer=1, confidence='auto')
    """

    def __init__(
        self,
        aliases: list[dict],
        categories: list[str],
        ollama_host: str = "http://localhost:11434",
        ollama_model: str = "llama3.2:3b",
        ollama_timeout: int = 60,
        anthropic_api_key: str = "",
        anthropic_model: str = "claude-haiku-4-20250514",
    ):
        self.categories = categories or DEFAULT_CATEGORIES[:]
        self.ollama_host = ollama_host.rstrip("/")
        self.ollama_model = ollama_model
        self.ollama_timeout = ollama_timeout
        self.anthropic_api_key = anthropic_api_key
        self.anthropic_model = anthropic_model

        # Layer 1: exact match  {UPPER_ALIAS: (merchant, category)}
        self._exact: dict[str, tuple[str, str]] = {}
        # Layer 1b: contains match  [(upper_substring, merchant, category)]
        self._contains: list[tuple[str, str, str]] = []
        # Layer 2: regex match  [(compiled_pattern, merchant, category)]
        self._regex: list[tuple[re.Pattern, str, str]] = []
        # Few-shot examples for Layer 3 (up to 10, FIFO)
        self._examples: list[tuple[str, str, str]] = []  # (desc, merchant, category)

        self._load_aliases(aliases)

    # ── Alias loading ─────────────────────────────────────────────────────────

    def _load_aliases(self, aliases: list[dict]):
        for row in aliases:
            alias     = str(row.get("alias",      "")).strip()
            merchant  = str(row.get("merchant",   "")).strip()
            category  = str(row.get("category",   "")).strip()
            mtype     = str(row.get("match_type", "exact")).strip().lower()
            if not alias or not merchant:
                continue
            if mtype == "regex":
                try:
                    self._regex.append(
                        (re.compile(alias, re.IGNORECASE), merchant, category)
                    )
                except re.error as e:
                    log.warning("Invalid regex alias %r: %s", alias, e)
            elif mtype == "contains":
                self._contains.append((alias.upper(), merchant, category))
            else:
                self._exact[alias.upper()] = (merchant, category)

    def reload_aliases(self, aliases: list[dict]):
        """Replace all alias rules (call after pulling fresh data from Sheets)."""
        self._exact.clear()
        self._contains.clear()
        self._regex.clear()
        self._load_aliases(aliases)
        log.debug(
            "Aliases reloaded: %d exact, %d regex",
            len(self._exact), len(self._regex),
        )

    # ── Few-shot example management ───────────────────────────────────────────

    def add_confirmed_example(
        self, raw_description: str, merchant: str, category: str
    ):
        """
        Record a user-confirmed (merchant, category) as a few-shot example
        for the Ollama prompt.  Oldest examples are evicted past 10.
        """
        self._examples.append((raw_description, merchant, category))
        if len(self._examples) > 10:
            self._examples = self._examples[-10:]

    # ── Main entry point ──────────────────────────────────────────────────────

    def categorize(self, raw_description: str) -> CategorizationResult:
        """
        Run the 4-layer pipeline and return a CategorizationResult.

        Layers 1 and 2 return confidence="auto" — the caller should write these
        directly to the Transactions tab without user interaction.

        Layers 3 and 4 return confidence="suggested"/"none" — the caller should
        surface these in the PWA review queue for user confirmation.
        """
        desc = raw_description.strip()

        # ── Layer 1: exact match ──────────────────────────────────────────────
        key = desc.upper()
        if key in self._exact:
            merchant, category = self._exact[key]
            log.debug("L1 exact: %r → %s / %s", desc, merchant, category)
            return CategorizationResult(merchant, category, layer=1, confidence="auto")

        # ── Layer 1b: contains match ─────────────────────────────────────────
        for substring, merchant, category in self._contains:
            if substring in key:
                log.debug("L1b contains: %r → %s / %s", desc, merchant, category)
                return CategorizationResult(merchant, category, layer=1, confidence="auto")

        # ── Layer 2: regex match ──────────────────────────────────────────────
        for pattern, merchant, category in self._regex:
            if pattern.search(desc):
                log.debug("L2 regex: %r → %s / %s", desc, merchant, category)
                return CategorizationResult(merchant, category, layer=2, confidence="auto")

        # ── Layer 3: AI suggestion (Ollama first, Anthropic fallback) ────────
        suggestion = self._ollama_suggest(desc)
        if suggestion:
            merchant, category = suggestion
            log.debug("L3 ollama: %r → %s / %s", desc, merchant, category)
            return CategorizationResult(
                merchant, category, layer=3, confidence="suggested"
            )

        suggestion = self._anthropic_suggest(desc)
        if suggestion:
            merchant, category = suggestion
            log.debug("L3 anthropic: %r → %s / %s", desc, merchant, category)
            return CategorizationResult(
                merchant, category, layer=3, confidence="suggested"
            )

        # ── Layer 4: review queue ─────────────────────────────────────────────
        log.debug("L4 review: %r → no suggestion", desc)
        return CategorizationResult(None, None, layer=4, confidence="none")

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _ollama_suggest(self, desc: str) -> Optional[tuple[str, str]]:
        """
        Ask Ollama for a (merchant, category) suggestion.
        Returns None if Ollama is unavailable, times out, or returns garbage.
        """
        if self._examples:
            examples_text = "\n".join(
                f'- "{d}" → {m}, {c}' for d, m, c in self._examples
            )
        else:
            examples_text = (
                '- "GRAB* TRANSPORT" → Grab, Transport\n'
                '- "NETFLIX.COM" → Netflix, Subscriptions\n'
                '- "INDOMARET" → Indomaret, Groceries'
            )

        categories_text = ", ".join(self.categories)

        prompt = (
            "You are a personal finance categorizer for an Indonesian household.\n\n"
            f"Known categories: {categories_text}\n\n"
            f"Recent confirmed examples:\n{examples_text}\n\n"
            f'Transaction: "{desc}"\n\n'
            'Reply with JSON only, no explanation: {"merchant": "...", "category": "..."}'
        )

        payload = json.dumps({
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},  # low temp for deterministic output
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self.ollama_host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.ollama_timeout) as resp:
                data = json.loads(resp.read())

            raw_response = data.get("response", "").strip()
            return self._parse_ollama_response(raw_response, desc)

        except urllib.error.URLError as e:
            log.debug("Ollama unreachable for %r: %s", desc, e)
        except TimeoutError:
            log.debug("Ollama timed out for %r", desc)
        except (json.JSONDecodeError, KeyError) as e:
            log.debug("Ollama bad response for %r: %s", desc, e)
        except Exception as e:
            log.debug("Ollama unexpected error for %r: %s", desc, e)

        return None

    def _anthropic_suggest(self, desc: str) -> Optional[tuple[str, str]]:
        """
        Ask Anthropic Claude for a (merchant, category) suggestion.
        Used as a fallback when Ollama is unavailable or times out.
        Returns None if the API key is missing, the call fails, or the
        response cannot be parsed.
        """
        if not self.anthropic_api_key:
            return None

        categories_text = ", ".join(self.categories)
        if self._examples:
            examples_text = "\n".join(
                f'- "{d}" → {m}, {c}' for d, m, c in self._examples
            )
        else:
            examples_text = (
                '- "GRAB* TRANSPORT" → Grab, Transport\n'
                '- "NETFLIX.COM" → Netflix, Subscriptions\n'
                '- "INDOMARET" → Indomaret, Groceries'
            )

        prompt = (
            "You are a personal finance categorizer for an Indonesian household.\n\n"
            f"Known categories: {categories_text}\n\n"
            f"Recent confirmed examples:\n{examples_text}\n\n"
            f'Transaction: "{desc}"\n\n'
            'Reply with JSON only, no explanation: {"merchant": "...", "category": "..."}'
        )

        payload = json.dumps({
            "model": self.anthropic_model,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": self.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            raw_response = data["content"][0]["text"].strip()
            return self._parse_ollama_response(raw_response, desc)

        except urllib.error.URLError as e:
            log.debug("Anthropic unreachable for %r: %s", desc, e)
        except TimeoutError:
            log.debug("Anthropic timed out for %r", desc)
        except (json.JSONDecodeError, KeyError) as e:
            log.debug("Anthropic bad response for %r: %s", desc, e)
        except Exception as e:
            log.debug("Anthropic unexpected error for %r: %s", desc, e)

        return None

    def _parse_ollama_response(
        self, raw: str, desc: str
    ) -> Optional[tuple[str, str]]:
        """Extract (merchant, category) from Ollama's text response."""
        # Find the first {...} block in the response
        m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if not m:
            log.debug("Ollama: no JSON found in response for %r: %.120s", desc, raw)
            return None

        try:
            parsed = json.loads(m.group())
        except json.JSONDecodeError as e:
            log.debug("Ollama: JSON parse error for %r: %s", desc, e)
            return None

        merchant = str(parsed.get("merchant", "")).strip()
        category = str(parsed.get("category", "")).strip()

        if not merchant or not category:
            return None

        # Validate / normalise category against known list
        if category not in self.categories:
            case_match = next(
                (c for c in self.categories if c.lower() == category.lower()),
                None,
            )
            if case_match:
                category = case_match
            else:
                log.debug(
                    "Ollama returned unknown category %r for %r — falling back to Other",
                    category, desc,
                )
                category = "Other"

        return merchant, category
