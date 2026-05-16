from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Autonomous NOC API"
    PROMETHEUS_URL: str = "http://prometheus:9090"
    KEEP_URL: str = "http://keep:8080"
    API_KEY: str = "nanoc-secret-key" # Default for FOSS/Dev
    
    model_config = ConfigDict(env_file=".env")

settings = Settings()
