import time
import logging
from collections import OrderedDict

import httpx

from app.providers.rule_based_provider import RuleBasedProvider
from app.schemas import Classification

logger = logging.getLogger("agent.classifier")


class CircuitBreaker:
    """Simple circuit breaker for provider failures."""

    _MAX_TRACKED = 128

    def __init__(self, max_failures: int = 3, cooldown_seconds: int = 300):
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self._failures: OrderedDict[str, int] = OrderedDict()
        self._cooldown_until: OrderedDict[str, float] = OrderedDict()

    def _evict_if_full(self, d: OrderedDict) -> None:
        while len(d) >= self._MAX_TRACKED:
            d.popitem(last=False)

    def record_failure(self, provider_name: str):
        self._evict_if_full(self._failures)
        self._failures[provider_name] = self._failures.get(provider_name, 0) + 1
        if self._failures[provider_name] >= self.max_failures:
            self._cooldown_until[provider_name] = time.time() + self.cooldown_seconds
            logger.warning(
                "Circuit breaker OPEN for %s after %d failures. Cooldown %ds.",
                provider_name, self._failures[provider_name], self.cooldown_seconds
            )

    def record_success(self, provider_name: str):
        self._failures[provider_name] = 0
        self._cooldown_until.pop(provider_name, None)

    def is_open(self, provider_name: str) -> bool:
        cooldown = self._cooldown_until.get(provider_name, 0)
        if cooldown == 0:
            return False
        if time.time() >= cooldown:
            # Cooldown expired, reset
            self._failures[provider_name] = 0
            self._cooldown_until.pop(provider_name, None)
            logger.info("Circuit breaker CLOSED for %s (cooldown expired)", provider_name)
            return False
        return True


class Classifier:
    def __init__(self, settings: dict):
        self.settings = settings
        self.circuit_breaker = CircuitBreaker(max_failures=3, cooldown_seconds=300)
        self.cloud_fallback_enabled = settings["classifier"].get("cloud_fallback_enabled", True)

        # Finance API for dynamic rule loading
        self._finance_api_url = settings["classifier"].get("finance_api_url", "")
        self._finance_api_key = settings["classifier"].get("finance_api_key", "")
        self._reload_interval = int(
            settings["classifier"].get("rule_reload_interval_seconds", 3600))
        self._last_rule_reload = 0.0

        # Rule sets (populated by _load_rules, fallback to TOML)
        self.email_rules: set[str] = set()
        self.domain_rules: set[str] = set()
        self.keyword_rules: set[str] = set()

        # TOML fallback for domains when Finance API is unreachable
        self._toml_domains = [
            d.lower().lstrip("@")
            for d in settings["classifier"].get("allowed_sender_domains", [])
        ]

        # Legacy alias used by _domain_not_allowed (TOML-only fallback)
        self.allowed_sender_domains: list[str] = list(self._toml_domains)

        self.providers = []
        for name in settings["classifier"]["provider_order"]:
            if name == "rule_based":
                self.providers.append(RuleBasedProvider())
            elif name == "ollama":
                self.providers.append(OllamaProvider(settings))
            elif name == "anthropic":
                logger.info("Anthropic provider removed — skipping")

        # Load rules on startup (Finance API or TOML fallback)
        self._load_rules()

    def reload_rules(self) -> None:
        """Public entry point for on-demand rule reload (e.g. from /trigger)."""
        self._load_rules()

    def _load_rules(self) -> None:
        """Fetch mail rules from Finance API; fall back to TOML domains."""
        if self._finance_api_url:
            try:
                resp = httpx.get(
                    f"{self._finance_api_url}/api/mail-rules",
                    headers={"X-Api-Key": self._finance_api_key},
                    timeout=5.0,
                )
                resp.raise_for_status()
                rules = resp.json()
                self.email_rules = {
                    r["pattern"].lower()
                    for r in rules
                    if r["rule_type"] == "sender_email" and r.get("enabled")
                }
                self.domain_rules = {
                    r["pattern"].lower()
                    for r in rules
                    if r["rule_type"] == "sender_domain" and r.get("enabled")
                }
                self.keyword_rules = {
                    r["pattern"].lower()
                    for r in rules
                    if r["rule_type"] == "subject_keyword" and r.get("enabled")
                }
                self._last_rule_reload = time.time()
                logger.info(
                    "Mail rules loaded from API: %d email, %d domain, %d keyword",
                    len(self.email_rules), len(self.domain_rules),
                    len(self.keyword_rules),
                )
                return
            except Exception as e:
                logger.warning(
                    "Failed to load mail rules from Finance API: %s "
                    "— falling back to TOML", e)

        # Fallback: TOML allowed_sender_domains
        self.domain_rules = set(self._toml_domains)
        self.email_rules = set()
        self.keyword_rules = set()
        self.allowed_sender_domains = list(self._toml_domains)
        logger.info(
            "Mail rules: using TOML fallback (%d domains)",
            len(self.domain_rules))

    def classify(self, message: dict) -> Classification:
        # Periodic rule reload
        if (self._finance_api_url
                and time.time() - self._last_rule_reload > self._reload_interval):
            self._load_rules()

        # Rule-based pre-filter: skip senders not matching any rule
        if not self._matches_any_rule(message):
            sender_email = message.get("sender_email", "")
            logger.debug(
                "Rule filter: skipping %s (%s)",
                message.get("bridge_id"), sender_email,
            )
            return Classification(
                category="not_financial",
                urgency="low",
                summary=f"Skipped: sender not in mail rules ({sender_email})",
                requires_action=False,
                provider="domain_prefilter",
            )

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
            if self.circuit_breaker.is_open(provider.name):
                logger.debug("Skipping %s (circuit breaker open)", provider.name)
                continue

            try:
                result = provider.classify(message)
                self.circuit_breaker.record_success(provider.name)
                logger.info(
                    "Classified %s as %s/%s via %s",
                    message.get("bridge_id"),
                    result.category,
                    result.urgency,
                    result.provider,
                )
                return result
            except Exception as e:
                self.circuit_breaker.record_failure(provider.name)
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

    def close(self) -> None:
        for provider in self.providers:
            try:
                provider.close()
            except Exception as e:
                logger.warning("Provider close failed for %s: %s", provider.name, e)

    def _matches_any_rule(self, message: dict) -> bool:
        """Return True if the message matches at least one active mail rule.

        Checks in order: sender_email (exact) → sender_domain → subject_keyword.
        If all rule sets are empty (API unreachable AND no TOML fallback),
        block everything to avoid notification spam.
        """
        if (not self.email_rules
                and not self.domain_rules
                and not self.keyword_rules):
            return False  # no rules → block all

        sender = (message.get("sender_email") or "").lower().strip()
        domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
        subject = (message.get("subject") or "").lower()

        return (
            (bool(self.email_rules)
             and sender in self.email_rules)
            or
            (bool(self.domain_rules)
             and domain in self.domain_rules)
            or
            (bool(self.keyword_rules)
             and any(kw in subject for kw in self.keyword_rules))
        )

    def _domain_not_allowed(self, message: dict) -> bool:
        """Legacy domain check — kept for TOML-only fallback path.

        Returns True if the sender's domain is NOT in allowed_sender_domains.
        """
        if not self.allowed_sender_domains:
            return False  # allowlist disabled — let everything through

        sender_email = (message.get("sender_email") or "").lower().strip()
        if not sender_email or "@" not in sender_email:
            return True

        domain = sender_email.rsplit("@", 1)[1]
        return domain not in self.allowed_sender_domains

    def _apple_says_skip(self, message: dict) -> bool:
        apple_cat = message.get("apple_category")
        if message.get("apple_urgent"):
            return False
        if message.get("apple_high_impact"):
            return False
        if apple_cat == 3:
            return True
        return False
