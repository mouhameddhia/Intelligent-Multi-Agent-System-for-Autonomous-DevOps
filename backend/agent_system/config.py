from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parent / ".env", extra="ignore")

    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    agent_port: int = 8001
    use_synthetic: bool = True

    mongodb_uri: str = ""
    mongodb_db: str = "pipeguard"


settings = Settings()
