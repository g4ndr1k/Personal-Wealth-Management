import os
import tomllib


def load_settings() -> dict:
    settings_file = os.environ.get("SETTINGS_FILE", "/app/config/settings.toml")
    with open(settings_file, "rb") as f:
        return tomllib.load(f)
