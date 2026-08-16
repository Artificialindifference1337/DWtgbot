from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./drugwars.db"
    game_timezone: str = "Europe/Amsterdam"
    admin_user_ids: str = ""
    log_level: str = "INFO"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def admins(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_user_ids.split(',') if x.strip()}

@lru_cache
def get_settings() -> Settings:
    return Settings()
