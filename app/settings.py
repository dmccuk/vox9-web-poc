from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Basic auth (POC)
    BASIC_USER: str = "admin"
    BASIC_PASS: str = "changeme"

    # AWS / S3
    AWS_REGION: str | None = None
    S3_BUCKET: str | None = None
    S3_INPUT_PREFIX: str = "inputs/"
    S3_OUTPUT_PREFIX: str = "outputs/"

    # ElevenLabs
    ELEVEN_API_KEY: str | None = None
    ELEVEN_VOICE_ID: str | None = None
    ELEVEN_MODEL_ID: str = "eleven_monolingual_v1"
    # Accepted: "mp3" or "wav"
    ELEVEN_OUTPUT_FORMAT: str = "mp3"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
