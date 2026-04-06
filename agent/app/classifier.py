import time
import logging
from app.providers.ollama_provider import OllamaProvider
from app.providers.anthropic_provider import AnthropicProvider
from app.schemas import Classification

logger = logging.getLogger("agent.classifier")


class CircuitBreaker:
    """Simple circuit breaker for provider failures."""

    def __init__(self, max_failures: int = 3, cooldown_seconds: int = 300):
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}

    def record_failure(self, provider_name: str):
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

        # Domain allowlist — empty list means no restriction (all senders pass)
        raw_domains = settings["classifier"].get("allowed_sender_domains", [])
        self.allowed_sender_domains: list[str] = [
            d.lower().lstrip("@") for d in raw_domains
        ]
        if self.allowed_sender_domains:
            logger.info(
                "Domain allowlist active: %s",
                ", ".join(self.allowed_sender_domains),
            )
        else:
            logger.info("Domain allowlist: disabled (all senders allowed)")

        self.providers = []
        for name in settings["classifier"]["provider_order"]:
            if name == "ollama":
                self.providers.append(OllamaProvider(settings))
            elif name == "anthropic":
                if self.cloud_fallback_enabled:
                    self.providers.append(AnthropicProvider(settings))
                else:
                    logger.info("Anthropic skipped: cloud_fallback_enabled=false")

    def classify(self, message: dict) -> Classification:
        # Domain allowlist pre-filter: skip senders not on the allowed list
        if self._domain_not_allowed(message):
            sender_email = message.get("sender_email", "")
            logger.debug(
                "Domain filter: skipping %s (%s)",
                message.get("bridge_id"), sender_email,
            )
            return Classification(
                category="not_financial",
                urgency="low",
                summary=f"Skipped: sender domain not in allowlist ({sender_email})",
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

    def _domain_not_allowed(self, message: dict) -> bool:
        """Return True if the sender's domain is NOT in the allowlist.

        If allowed_sender_domains is empty, every sender is allowed (returns False).
        The check is case-insensitive and tolerates missing/malformed sender_email.
        """
        if not self.allowed_sender_domains:
            return False  # allowlist disabled — let everything through

        sender_email = (message.get("sender_email") or "").lower().strip()
        if not sender_email or "@" not in sender_email:
            # No parseable email address — block by default when allowlist is active
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
