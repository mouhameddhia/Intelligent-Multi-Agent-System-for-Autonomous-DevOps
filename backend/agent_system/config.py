from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    agent_port: int = 8001
    use_synthetic: bool = True

    mongodb_uri: str = ""
    mongodb_db: str = "pipeguard"
    
    judge_api_key: str = ""
    judge_model: str = "qwen/qwen3.8-27b"

    # Offline learning layer: background evaluation and policy learning, and
    # injecting the active policy into the Remediation prompt. False turns all
    # of it off and leaves Remediation exactly as it was.
    learning_enabled: bool = True
    learning_batch_size: int = 5


settings = Settings()
