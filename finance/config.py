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


