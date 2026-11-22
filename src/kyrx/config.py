"""Runtime configuration.

Every setting is environment-overridable with the ``KYRX_`` prefix, or read
from a ``.env`` file next to the project. Secrets stay in ``SecretStr`` and are
never logged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["CoordinationMode", "Settings", "Transport", "get_settings"]

Transport = Literal["stdio", "http"]
CoordinationMode = Literal["auto", "solo", "fleet"]
AuthMode = Literal["none", "token"]


class Settings(BaseSettings):
    """Resolved configuration for one KYRX process."""

    model_config = SettingsConfigDict(
        env_prefix="KYRX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("var")
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    streamable_http_path: str = "/mcp"

    auth_mode: AuthMode = "none"
    auth_token: SecretStr | None = None
    auth_required_scopes: list[str] = []
    require_auth_for_http: bool = True

    rate_limit_calls: int = 120
    rate_limit_window_s: float = 60.0
    max_artifact_bytes: int = 8 * 1024 * 1024

    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_timeout_s: float = 30.0

    lease_offer_ttl_s: int = 5
    lease_run_ttl_s: int = 20
    lease_heartbeat_s: int = 5
    lease_steal_after_s: int = 30

    sandbox_timeout_s: int = 30
    sandbox_memory_bytes: int = 64 * 1024 * 1024
    sandbox_fuel: int = 200_000_000

    default_budget_tokens: int = 8_000

    # How the plane protects itself. "solo": single-agent fast path (no rate
    # limiter, audit trail in memory, no thread hop). "fleet": every protection
    # on. "auto" (default): starts solo on stdio and fleet on http, and switches
    # to fleet for good the first time a second agent id claims a node.
    # Leases are enforced in every mode. See "Coordination mode" in README.md.
    mode: CoordinationMode = "auto"

    # Legacy switch kept for compatibility: True is the same as mode="solo".
    solo_mode: bool = False

    # Price of the model, in US dollars per million tokens. Used to turn the
    # measured token counts into a measured dollar cost.
    input_price_per_mtok: float = 0.0
    output_price_per_mtok: float = 0.0

    @property
    def effective_mode(self) -> CoordinationMode:
        """The requested mode, with the legacy ``solo_mode`` flag folded in."""
        return "solo" if self.solo_mode else self.mode

    @property
    def journal_path(self) -> Path:
        return self.data_dir / "journal.ndjson"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def economy_path(self) -> Path:
        return self.data_dir / "economy.json"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.ndjson"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, resolved once."""
    return Settings()
