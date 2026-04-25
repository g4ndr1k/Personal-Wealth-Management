# providers package
from .rule_based_provider import RuleBasedProvider
from .ollama_provider import OllamaProvider

PROVIDERS = {
    "rule_based": RuleBasedProvider,
    "ollama": OllamaProvider,
}
