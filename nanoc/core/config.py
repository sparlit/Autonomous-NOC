import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "NANOC"
    DB_PATH: str = "nanoc/memory/nanoc.db"
    WORKSPACE_DIR: str = "nanoc/workspace"
    STAGING_DIR: str = "nanoc/staging"
    LOGS_DIR: str = "nanoc/logs"

    # LLM Settings
    DEFAULT_PROVIDER: str = "openrouter" # openrouter or ollama
    OPENROUTER_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "meta-llama/llama-3-8b-instruct:free"

    # Orchestrator Settings
    MAX_WORKERS: int = 10
    INITIAL_WORKERS: int = 5

    model_config = ConfigDict(env_file=".env")

settings = Settings()
