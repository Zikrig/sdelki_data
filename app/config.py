from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    BOT_TOKEN: str = Field(..., description="Telegram bot token")
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/warehouse",
        description="SQLAlchemy async connection string",
    )


settings = Settings()  # type: ignore[arg-type]


