from pydantic import BaseSettings

class Settings(BaseSettings):
    BASIC_USER: str = "admin"
    BASIC_PASS: str = "changeme"
    # For Phase 2 (S3), keep placeholders here:
    AWS_REGION: str | None = None
    S3_BUCKET: str | None = None

settings = Settings()
