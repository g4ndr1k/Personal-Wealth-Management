import os
import tomllib


def load_settings() -> dict:
    settings_file = os.environ.get("SETTINGS_FILE", "/app/config/settings.toml")
    with open(settings_file, "rb") as f:
        settings = tomllib.load(f)

    # Inject Finance API credentials from environment (Docker Compose)
    classifier = settings.setdefault("classifier", {})
    if os.environ.get("FINANCE_API_URL"):
        classifier["finance_api_url"] = os.environ["FINANCE_API_URL"]
    if os.environ.get("FINANCE_API_KEY"):
        classifier["finance_api_key"] = os.environ["FINANCE_API_KEY"]

    return settings
