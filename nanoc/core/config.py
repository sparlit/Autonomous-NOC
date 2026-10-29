import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator
from typing import Optional

#: Repository root — the directory holding ``nanoc/`` and ``backend/``. Every
#: relative default below is anchored to it so the backend starts from any
#: working directory (systemd, docker, ``uvicorn backend.main:app`` from
#: elsewhere) instead of only from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    PROJECT_NAME: str = "NANOC"
    DB_PATH: str = str(REPO_ROOT / "nanoc" / "memory" / "nanoc.db")
    WORKSPACE_DIR: str = "nanoc/workspace"
    STAGING_DIR: str = "nanoc/staging"
    LOGS_DIR: str = "nanoc/logs"

    # Runtime Settings
    INITIAL_WORKERS: int = 5
    MAX_WORKERS: int = 20
    #: Legacy admin alias kept for existing deployments. No default: an unset
    #: token must never grant administrative access (see ``app.core.auth``).
    #: Prefer ``NOC_ADMIN_TOKEN`` in ``backend/app/core/config.py``.
    TERMINAL_ACCESS_TOKEN: str = ""

    # LLM Settings
    DEFAULT_PROVIDER: str = "openrouter" # openrouter or ollama
    OPENROUTER_API_KEY: Optional[str] = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "meta-llama/llama-3-8b-instruct:free"

    model_config = ConfigDict(env_file=".env")

    @field_validator("DB_PATH")
    @classmethod
    def _resolve_db_path(cls, value: str) -> str:
        """
        Anchor ``DB_PATH`` to the repository root.

        An absolute value (the default, or an operator's ``NANOC_DB_PATH``) is
        returned unchanged; a relative one is resolved against
        :data:`REPO_ROOT` so the database is found regardless of the process
        working directory. ``~`` is expanded for convenience.

        Parameters:
            value (str): Raw ``DB_PATH`` from defaults or the environment.

        Returns:
            str: Absolute path to the SQLite database file.
        """
        if not value:
            return str(REPO_ROOT / "nanoc" / "memory" / "nanoc.db")
        path = Path(os.path.expanduser(str(value)))
        if not path.is_absolute():
            path = REPO_ROOT / path
        return str(path)

settings = Settings()
