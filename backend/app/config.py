from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://recoverai:recoverai_password@localhost:5432/recoverai_dev"
    )

    # Razorpay
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # LLM
    LLM_PROVIDER: str = "gemini"
    LLM_MODEL: str = "gemini-2.0-flash"
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Recovery Policy Defaults
    RECOVERY_MAX_ATTEMPTS: int = 3
    RECOVERY_MAX_WINDOW_HOURS: int = 72
    RECOVERY_LINK_EXPIRY_HOURS: int = 24

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
