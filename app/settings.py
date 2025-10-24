from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BASIC_USER: str = "admin"
    BASIC_PASS: str = "changeme"
    # Optional for later (S3)
    AWS_REGION: str | None = None
    S3_BUCKET: str | None = None

    # Read from .env if present
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
