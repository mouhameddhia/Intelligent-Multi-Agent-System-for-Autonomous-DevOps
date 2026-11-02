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

    # Local Ollama server, used only for embedding incident text for the
    # Incident Advisor chatbot's semantic search (agent_system/db.py).
    ollama_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"

    # Gemini — used only as an independent judge in scripts/eval_rag.py, a
    # different provider/model family from the Groq model it judges, on
    # purpose (same-model-judging-itself is a real limitation otherwise).
    gemini_api_key: str = ""
    gemini_judge_model: str = "gemini-3.5-flash-lite"

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
