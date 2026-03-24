class OpenAIProvider:
    """Placeholder - not implemented in v1. Add to provider_order when ready."""
    name = "openai"
    enabled = False

    def classify(self, message: dict):
        raise NotImplementedError("OpenAI provider not implemented in v1")