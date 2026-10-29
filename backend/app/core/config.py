from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import ConfigDict

#: ``backend/`` — the directory holding ``app/`` and ``config/``. Resolved from
#: this file so the automation rules path works regardless of the process CWD.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    PROJECT_NAME: str = "Autonomous NOC API"
    PROMETHEUS_URL: str = "http://prometheus:9090"
    KEEP_URL: str = "http://keep:8080"

    # ------------------------------------------------------------- automation
    #: Master switch. When false the engine loads no rules and stays inert, so a
    #: deployment can ship the code without letting it react to anything.
    AUTOMATION_ENABLED: bool = True
    #: Declarative rule file (JSON — stdlib only, no YAML dependency).
    AUTOMATION_RULES_PATH: str = str(BACKEND_DIR / "config" / "automation_rules.json")
    #: Default webhook target. Deliberately empty: an unset secret makes webhook
    #: actions report ``skipped`` instead of posting to a guessed URL.
    AUTOMATION_WEBHOOK_URL: str = ""
    #: Optional shared secret sent as ``X-NOC-Signature`` on webhook posts.
    AUTOMATION_WEBHOOK_SECRET: str = ""
    AUTOMATION_WEBHOOK_TIMEOUT_S: float = 5.0
    AUTOMATION_WEBHOOK_RETRIES: int = 2
    #: Bounded concurrency: at most this many action batches run at once.
    AUTOMATION_MAX_CONCURRENCY: int = 4
    #: Bounded queue: beyond this depth new matches are dropped (and logged)
    #: rather than blocking the EventBus polling loop.
    AUTOMATION_QUEUE_SIZE: int = 200

    model_config = ConfigDict(env_file=".env")


settings = Settings()
