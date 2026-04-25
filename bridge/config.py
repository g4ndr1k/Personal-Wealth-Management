"""
Settings loader and validator for the bridge.

Supports both file-based and Keychain secret sources, controlled by
settings.toml keys:
  - auth.token_source = "file" | "keychain"
  - auth.keychain_service = "agentic-ai-bridge"
  - pdf.bank_passwords_source = "file" | "keychain"
"""
from pathlib import Path
import tomllib


def load_settings(path: str = "config/settings.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_token_path(settings: dict) -> Path:
    return Path(settings["auth"]["token_file"]).expanduser()


def _safe_int(value, label: str, errors: list[str]) -> int | None:
    try:
        return int(value)
    except Exception:
        errors.append(f"{label} must be an integer: {value}")
        return None


def validate_settings(settings: dict) -> None:
    errors = []

    for section in ["bridge", "auth", "mail", "imessage",
                     "classifier", "ollama", "agent"]:
        if section not in settings:
            errors.append(f"Missing required section: [{section}]")
    if errors:
        raise ValueError(f"Config validation failed: {'; '.join(errors)}")

    port = _safe_int(settings["bridge"].get("port", 0), "bridge.port", errors)
    if port is not None and not (1 <= port <= 65535):
        errors.append(f"Invalid bridge port: {port}")

    # Token source: "file" or "keychain"
    auth = settings.get("auth", {})
    token_source = auth.get("token_source", "file")
    if token_source not in ("file", "keychain"):
        errors.append(f"auth.token_source must be 'file' or 'keychain', got: {token_source}")

    if token_source == "file":
        # Validate file exists only when using file source
        token_path = Path(auth["token_file"]).expanduser()
        if not token_path.exists():
            errors.append(f"Token file not found: {token_path}")
    elif token_source == "keychain":
        # Validate service name is set
        if not auth.get("keychain_service"):
            errors.append("auth.keychain_service must be set when token_source=keychain")

    # Bank passwords source: "file" or "keychain"
    pdf = settings.get("pdf", {})
    bp_source = pdf.get("bank_passwords_source", "file")
    if bp_source not in ("file", "keychain"):
        errors.append(f"pdf.bank_passwords_source must be 'file' or 'keychain', got: {bp_source}")

    if bp_source == "file":
        bank_pw_file = pdf.get("bank_passwords_file", "")
        if bank_pw_file:
            bp_path = Path(bank_pw_file).expanduser()
            if not bp_path.exists():
                errors.append(f"Bank passwords file not found: {bp_path}")

    recipient = settings["imessage"].get("primary_recipient", "")
    if not recipient or "@" not in recipient:
        errors.append(f"Invalid primary_recipient: {recipient}")

    authorized = settings["imessage"].get("authorized_senders", [])
    if not isinstance(authorized, list) or not authorized:
        errors.append("imessage.authorized_senders must be a non-empty list")

    cmd_prefix = settings["imessage"].get("command_prefix", "")
    if not cmd_prefix:
        errors.append("imessage.command_prefix must not be empty")

    poll = _safe_int(settings["agent"].get("poll_interval_seconds", 0), "agent.poll_interval_seconds", errors)
    if poll is not None and poll < 10:
        errors.append(f"poll_interval_seconds too low: {poll}")

    cmd_poll = _safe_int(settings["agent"].get("command_poll_interval_seconds", 0), "agent.command_poll_interval_seconds", errors)
    if cmd_poll is not None and cmd_poll < 5:
        errors.append(f"command_poll_interval_seconds too low: {cmd_poll}")

    max_alerts = _safe_int(settings["imessage"].get("max_alerts_per_hour", 0), "imessage.max_alerts_per_hour", errors)
    if max_alerts is not None and max_alerts < 1:
        errors.append(f"max_alerts_per_hour must be >= 1: {max_alerts}")

    max_cmds = _safe_int(settings["imessage"].get("max_commands_per_hour", 0), "imessage.max_commands_per_hour", errors)
    if max_cmds is not None and max_cmds < 1:
        errors.append(f"max_commands_per_hour must be >= 1: {max_cmds}")

    try:
        from app.providers import PROVIDERS
        valid_providers = set(PROVIDERS)
    except ImportError:
        valid_providers = {"ollama", "rule_based"}
    for p in settings["classifier"].get("provider_order", []):
        if p not in valid_providers:
            errors.append(f"Unknown provider: {p}")

    if errors:
        raise ValueError(f"Config validation failed: {'; '.join(errors)}")
