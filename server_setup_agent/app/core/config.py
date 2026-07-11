from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    LLM_PROVIDER: str = "groq"
    GROQ_API_KEY: str
    MODEL_NAME: str = "llama-3.3-70b-versatile"
    TEMPERATURE: float = 0.0
    TEAMS_WEBHOOK_URL: str = ""

    # GitHub personal access token for cloning private repositories
    GITHUB_TOKEN: Optional[str] = None  # e.g. ghp_xxxxxxxxxxxxxxxxxxxx

    # Default SSH connection settings (used when client does not supply credentials)
    SSH_HOST: str = "127.0.0.1"
    SSH_PORT: int = 2222
    SSH_USERNAME: str = "root"
    SSH_PASSWORD: Optional[str] = None  # root password for initial bootstrap connection
    SSH_KEY_PATH: Optional[str] = None  # e.g. C:/Users/Bipin Joshi/.ssh/id_ed25519

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
