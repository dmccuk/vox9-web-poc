from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Basic auth (POC)
    BASIC_USER: str = "admin"
    BASIC_PASS: str = "changeme"

    # AWS / S3
    AWS_REGION: str | None = None
    S3_BUCKET: str | None = None

    # Project layout
    PROJECTS_PREFIX: str = "projects/"  # e.g., projects/<story>/...

    # ElevenLabs
    ELEVEN_API_KEY: str | None = None
    ELEVEN_VOICE_ID: str | None = None
    ELEVEN_MODEL_ID: str = "eleven_multilingual_v2"
    ELEVEN_OUTPUT_FORMAT: str = "mp3"  # mp3 or wav

    # (legacy fields kept for compatibility; unused in new flow)
    S3_INPUT_PREFIX: str = "inputs/"
    S3_OUTPUT_PREFIX: str = "outputs/"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()

