"""Load Stage 2 finance config from settings.toml."""
from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass


# ── Config dataclasses ────────────────────────────────────────────────────────

@dataclass
class FinanceConfig:
    sqlite_db: str
    xlsx_input: str


@dataclass
class SheetsConfig:
    credentials_file: str
    token_file: str
    spreadsheet_id: str
    transactions_tab: str
    aliases_tab: str
    categories_tab: str
    currency_tab: str
    import_log_tab: str
    overrides_tab: str
    pdf_import_log_tab: str
    holdings_tab: str


@dataclass
class FastAPIConfig:
    host: str
    port: int
    cors_origins: list[str]


@dataclass
class OllamaFinanceConfig:
    host: str
    model: str
    timeout_seconds: int


@dataclass
class AnthropicFinanceConfig:
    api_key: str
    model: str
    enabled: bool


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_config(settings_file: str | None = None) -> dict:
    path = settings_file or os.environ.get(
        "SETTINGS_FILE", "config/settings.toml"
    )
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_finance_config(cfg: dict) -> FinanceConfig:
    s = cfg["finance"]
    # Env vars let Docker containers override host-absolute paths from settings.toml.
    # On the host, these env vars are unset so settings.toml values are used as-is.
    return FinanceConfig(
        sqlite_db  = os.environ.get("FINANCE_SQLITE_DB")  or s["sqlite_db"],
        xlsx_input = os.environ.get("FINANCE_XLSX_INPUT") or s["xlsx_input"],
    )


def get_sheets_config(cfg: dict) -> SheetsConfig:
    s = cfg["google_sheets"]
    creds = os.environ.get("GOOGLE_CREDENTIALS_FILE") or s["credentials_file"]
    # Default token file lives beside the credentials file
    default_token = os.path.join(os.path.dirname(creds), "google_token.json")
    token = os.environ.get("GOOGLE_TOKEN_FILE") or s.get("token_file", default_token)
    return SheetsConfig(
        credentials_file=creds,
        token_file=token,
        spreadsheet_id=s["spreadsheet_id"],
        transactions_tab=s.get("transactions_tab", "Transactions"),
        aliases_tab=s.get("aliases_tab", "Merchant Aliases"),
        categories_tab=s.get("categories_tab", "Categories"),
        currency_tab=s.get("currency_tab", "Currency Codes"),
        import_log_tab=s.get("import_log_tab", "Import Log"),
        overrides_tab=s.get("overrides_tab", "Category Overrides"),
        pdf_import_log_tab=s.get("pdf_import_log_tab", "PDF Import Log"),
        holdings_tab=s.get("holdings_tab", "Holdings"),
    )


def get_fastapi_config(cfg: dict) -> FastAPIConfig:
    s = cfg.get("fastapi", {})
    return FastAPIConfig(
        host=s.get("host", "0.0.0.0"),
        port=s.get("port", 8090),
        cors_origins=s.get("cors_origins", ["http://localhost:5173"]),
    )


def get_ollama_finance_config(cfg: dict) -> OllamaFinanceConfig:
    s = cfg.get("ollama_finance", {})
    return OllamaFinanceConfig(
        # OLLAMA_FINANCE_HOST lets Docker containers point to host.docker.internal
        # while the settings.toml default (localhost) is used for host-side runs.
        host=os.environ.get("OLLAMA_FINANCE_HOST") or s.get("host", "http://localhost:11434"),
        model=os.environ.get("OLLAMA_FINANCE_MODEL") or s.get("model", "gemma4:e4b"),
        timeout_seconds=s.get("timeout_seconds", 60),
    )


def get_anthropic_finance_config(cfg: dict) -> AnthropicFinanceConfig:
    s = cfg.get("anthropic", {})
    env_name = s.get("api_key_env", "ANTHROPIC_API_KEY")
    return AnthropicFinanceConfig(
        api_key=os.environ.get(env_name, ""),
        model=s.get("model", "claude-haiku-4-20250514"),
        enabled=bool(s.get("enabled", False)),
    )
