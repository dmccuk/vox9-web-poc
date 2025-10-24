from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BASIC_USER: str = "admin"
    BASIC_PASS: str = "changeme"
    AWS_REGION: str | None = None
    S3_BUCKET: str | None = None
    S3_INPUT_PREFIX: str = "inputs/"
    S3_OUTPUT_PREFIX: str = "outputs/"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
