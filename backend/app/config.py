"""
config.py — Application settings loaded from environment / .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "solar_pv_karnataka"


settings = Settings()
