from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    # Optional local QLoRA student. Empty preserves the existing Groq route.
    llm_include_reasoning: bool = False
    local_llm_model: str = "pipeguard-qwen3-4b"
    local_llm_url: str = "http://localhost:11434"
    local_llm_num_ctx: int = 8192
    local_llm_num_gpu: int = 99
    # Raw prompts and outputs can contain source/log data; retain them for
    # offline fine-tuning only when explicitly enabled.
    store_llm_calls: bool = False
    agent_port: int = 8001
    use_synthetic: bool = True

    mongodb_uri: str = ""
    mongodb_db: str = "pipeguard"
    # Optional Fernet key for encrypting persisted integration credentials.
    # When unset, PipeGuard creates a mode-600 local key file on first use.
    integration_encryption_key: str = ""
    
    judge_api_key: str = ""
    judge_model: str = "qwen/qwen3.8-27b"
    learning_enabled: bool = True
    learning_batch_size: int = 5

    # The local Vagrant VM whose Docker daemon hosts the selected log source.
    # Override with LOKI_VAGRANT_DIR when the Vagrant project lives elsewhere.
    loki_vagrant_dir: str = str(Path.home() / "Vagrant" / "CentOS7")

    # Incident Advisor semantic retrieval uses the local Ollama REST API.
    ollama_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"

    # Optional, used only by the standalone RAG evaluation script.
    gemini_api_key: str = ""
    gemini_judge_model: str = "gemini-3.5-flash-lite"


settings = Settings()
