from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    # Return gpt-oss's reasoning text with Monitoring/Remediation answers.
    # Off in production (fewer tokens to transfer); training.generate_dataset
    # turns it on so students can be distilled on reasoning + answer.
    llm_include_reasoning: bool = False
    agent_port: int = 8001
    use_synthetic: bool = True

    # Independent judge (different provider than the fix generator, on
    # purpose) that reviews a generated fix before it is pushed to GitHub.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    mongodb_uri: str = ""
    mongodb_db: str = "pipeguard"

    # Default tool credentials for local/single-user setups: the Settings
    # page auto-fills and auto-connects from these on load, so the engineer
    # doesn't re-enter them every session. Left blank, each integration just
    # falls back to the existing manual "Test and connect" flow.
    jenkins_url: str = ""
    jenkins_username: str = ""
    jenkins_api_token: str = ""

    github_token: str = ""

    prometheus_url: str = ""

    grafana_url: str = ""
    grafana_token: str = ""

    loki_url: str = ""

    sonarqube_url: str = ""
    sonarqube_token: str = ""

    kubernetes_url: str = ""
    kubernetes_token: str = ""
    kubernetes_verify_tls: bool = True


settings = Settings()
