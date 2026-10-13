from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from ._upstream_config import Settings as UpstreamSettings


class Settings(UpstreamSettings):
    """Upstream settings with credentials read from mounted Docker secrets."""

    model_config = SettingsConfigDict(secrets_dir="/run/secrets")
    postgres_password: str
    vesselapi_api_key: str = ""

    @model_validator(mode="after")
    def configure_database(self) -> "Settings":
        """Construct the database URL using the mounted password."""
        password = quote(self.postgres_password, safe="")
        self.database_url = f"postgresql://ais:{password}@db:5432/ais"
        return self
