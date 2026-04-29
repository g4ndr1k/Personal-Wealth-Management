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
class HouseholdConfig:
    base_url: str
    api_key_file: str


@dataclass
class CoretaxConfig:
    template_dir: str
    output_dir: str
    investment_match_mode: str
    rounding: str
    owner_aliases: dict[str, str]
    institution_aliases: dict[str, list[str]]


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


def get_fastapi_config(cfg: dict) -> FastAPIConfig:
    s = cfg.get("fastapi", {})
    return FastAPIConfig(
        host=s.get("host", "127.0.0.1"),
        port=s.get("port", 8090),
        cors_origins=s.get("cors_origins", [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
        ]),
    )


def get_ollama_finance_config(cfg: dict) -> OllamaFinanceConfig:
    s = cfg.get("ollama_finance", {})
    return OllamaFinanceConfig(
        # OLLAMA_FINANCE_HOST lets Docker containers point to host.docker.internal
        # while the settings.toml default (localhost) is used for host-side runs.
        host=os.environ.get("OLLAMA_FINANCE_HOST") or s.get("host", "http://localhost:11434"),
        model=os.environ.get("OLLAMA_FINANCE_MODEL") or s.get("model", "gemma3:4b"),
        timeout_seconds=s.get("timeout_seconds", 60),
    )


def get_household_config(cfg: dict) -> HouseholdConfig:
    s = cfg.get("household", {})
    return HouseholdConfig(
        base_url=(os.environ.get("HOUSEHOLD_API_BASE_URL") or s.get("base_url", "http://192.168.1.44:8088")).rstrip("/"),
        api_key_file=os.environ.get("HOUSEHOLD_API_KEY_FILE") or s.get("api_key_file", os.path.expanduser("~/agentic-ai/household-expense/secrets/household_api.key")),
    )


def get_coretax_config(cfg: dict) -> CoretaxConfig:
    s = cfg.get("coretax", {})
    raw_owner_aliases = s.get("owner_aliases", {})
    raw_institution_aliases = s.get("institution_aliases", {})
    return CoretaxConfig(
        template_dir=os.environ.get("CORETAX_TEMPLATE_DIR") or s.get("template_dir", "data/coretax/templates"),
        output_dir=os.environ.get("CORETAX_OUTPUT_DIR") or s.get("output_dir", "data/coretax/output"),
        investment_match_mode=s.get("investment_match_mode", "strict"),
        rounding=s.get("rounding", "none"),
        owner_aliases={str(alias): str(owner) for alias, owner in raw_owner_aliases.items()},
        institution_aliases={
            str(canonical): [str(alias) for alias in aliases]
            for canonical, aliases in raw_institution_aliases.items()
        },
    )


