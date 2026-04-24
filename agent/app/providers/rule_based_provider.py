"""Rule-based classifier provider — no LLM, no network.

For the mail-agent hot path: any email passing the domain allowlist
and Apple ML pre-filter is classified as financial_other with medium
urgency. This eliminates the Ollama dependency from the alert path,
removing the "model not loaded after wake" failure mode entirely.
"""

from app.schemas import Classification


class RuleBasedProvider:
    name = "rule_based"

    def classify(self, message: dict) -> Classification:
        return Classification(
            category="financial_other",
            urgency="medium",
            summary="",
            requires_action=True,
            provider="rule_based",
        )

    def close(self) -> None:
        pass
