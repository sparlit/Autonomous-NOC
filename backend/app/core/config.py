from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Autonomous NOC API"
    PROMETHEUS_URL: str = "http://prometheus:9090"
    KEEP_URL: str = "http://keep:8080"
    
    class Config:
        env_file = ".env"

settings = Settings()
